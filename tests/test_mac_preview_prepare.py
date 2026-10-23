#!/usr/bin/env python3
"""Execute the real prepare wrapper with fake external tools, not Chromium QA."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PreviewPrepare(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='helium prepare fixture ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'build/src'
        self.logs = self.root / 'preview-logs'
        self.script = self.root / '.github/scripts/mac_preview.sh'
        self.script.parent.mkdir(parents=True)
        self.script.write_bytes((ROOT / '.github/scripts/mac_preview.sh').read_bytes())
        self.source.mkdir(parents=True)
        (self.source / '.gn').write_text('# Fixture source root\n')
        helper = self.root / 'devutils/shared.sh'
        helper.parent.mkdir()
        # Use the actual upstream gn_gen body to exercise its pipeline cd behavior.
        gn_gen = re.search(r'^gn_gen\(\) \{\n.*?^\}',
                           (ROOT / 'devutils/shared.sh').read_text(), re.M | re.S).group()
        helper.write_text('''_src_dir="$ROOT/build/src"
_out_dir="$_src_dir/out/Default"
_gn_path="$_src_dir/buildtools/mac/gn"
prepare_sources() { echo 'Fixture source preparation'; }
write_gn_args() { mkdir -p "$_out_dir"; echo 'target_cpu="arm64"' > "$_out_dir/args.gn"; }
configure_siso() { :; }
''' + gn_gen + '\n')
        self.gn = self.source / 'buildtools/mac/gn'
        self.gn.parent.mkdir(parents=True)
        self.gn.write_text('''#!/usr/bin/env python3
import json, os, pathlib, sys
command = sys.argv[1]
with open(os.environ['GN_CALLS'], 'a') as log:
    log.write(json.dumps({'command': command, 'cwd': os.getcwd()}) + '\\n')
if not pathlib.Path('.gn').is_file():
    print('ERROR Could not find source root; no .gn file found.')
    sys.exit(1)
if os.environ.get('FAIL_GN') == command:
    print('fixture GN ' + command + ' failure', file=sys.stderr)
    sys.exit(42)
print('Done. Fixture graph generated.' if command == 'gen' else 'fixture_arg = true')
''')
        self.gn.chmod(0o755)
        self.clang = self.source / 'third_party/llvm-build/Release+Asserts/bin/clang'
        siso = self.source / 'third_party/siso/cipd/siso'
        for file in [self.clang, siso]:
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text('fixture tool contents\n')
        self.bin = self.root / 'fixture-bin'
        self.bin.mkdir()
        for name in ['xcrun', 'xcodebuild']:
            file = self.bin / name
            file.write_text('#!/bin/sh\nprintf "fixture tool metadata\\n"\n')
            file.chmod(0o755)
        self.env = dict(os.environ, PATH=f'{self.bin}{os.pathsep}{os.environ["PATH"]}',
                        GN_CALLS=str(self.root / 'gn-calls.jsonl'),
                        SOURCE_SHA='a' * 40, GITHUB_SHA='b' * 40)

    def run_prepare(self, **environment):
        return subprocess.run(['bash', str(self.script), 'prepare'], cwd=self.root,
                              env={**self.env, **environment}, text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=20)

    def calls(self):
        return [json.loads(line) for line in (self.root / 'gn-calls.jsonl').read_text().splitlines()]

    def test_pipeline_cd_stays_in_subshell_and_reproduces_original_failure(self):
        # Execute the original sequencing: successful gn_gen | tee does not cd
        # the parent, so the following direct GN args invocation fails.
        command = '''ROOT="$PWD"; source devutils/shared.sh
set -euo pipefail
gn_gen 2>&1 | tee generated.log
"$_gn_path" args "$_out_dir" --list --short > args.log
'''
        result = subprocess.run(['bash', '-c', command], cwd=self.root, env=self.env,
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.calls(), [{'command': 'gen', 'cwd': str(self.source)},
                                       {'command': 'args', 'cwd': str(self.root)}])
        self.assertIn('Could not find source root', (self.root / 'args.log').read_text())

    def test_real_prepare_wrapper_completes_and_records_absolute_provenance(self):
        result = self.run_prepare()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.calls(), [{'command': 'gen', 'cwd': str(self.source)},
                                       {'command': 'args', 'cwd': str(self.source)}])
        self.assertIn('fixture_arg = true', result.stdout)
        self.assertIn('Preparation complete', result.stdout)
        self.assertEqual((self.logs / 'resolved-gn-args.txt').read_text(), 'fixture_arg = true\n')
        provenance = json.loads((self.logs / 'provenance.json').read_text())
        self.assertEqual(provenance['source_sha'], 'a' * 40)
        self.assertEqual(provenance['gn_sha256'], hashlib.sha256(self.gn.read_bytes()).hexdigest())
        self.assertEqual(provenance['clang_sha256'], hashlib.sha256(self.clang.read_bytes()).hexdigest())
        self.assertEqual((self.logs / 'args.gn').read_bytes(),
                         (self.source / 'out/Default/args.gn').read_bytes())

    def test_gn_failure_stops_prepare_and_is_visible_in_console_and_log(self):
        for stage, filename in [('gen', 'gn.log'), ('args', 'resolved-gn-args.txt')]:
            with self.subTest(stage=stage):
                result = self.run_prepare(FAIL_GN=stage)
                self.assertEqual(result.returncode, 42, result.stdout)
                self.assertIn(f'fixture GN {stage} failure', result.stdout)
                self.assertIn(f'fixture GN {stage} failure', (self.logs / filename).read_text())
                self.assertNotIn('Record source/configuration/toolchain provenance', result.stdout)
                self.assertFalse((self.logs / 'provenance.json').exists())

    def test_missing_provenance_tool_fails_with_visible_path(self):
        self.clang.unlink()
        result = self.run_prepare()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Record source/configuration/toolchain provenance', result.stdout)
        self.assertIn(str(self.clang), result.stdout)
        self.assertNotIn('Preparation complete', result.stdout)


if __name__ == '__main__':
    unittest.main()
