#!/usr/bin/env python3
"""Execute the real prepare wrapper with fake external tools, not Chromium QA."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
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
        helper_source = (ROOT / '.github/scripts/mac_preview_provenance.py').read_text()
        fixture_cryptex = str(self.root / 'cryptex')
        (self.script.parent / 'mac_preview_provenance.py').write_text(
            helper_source.replace('/var/run/com', fixture_cryptex + '/com'))
        metal_folder = Path(fixture_cryptex) / 'com.apple.security.cryptexd/mnt/com.apple.MobileAsset.MetalToolchain-v17.6.42.0.ABC123/Metal.xctoolchain/usr/bin'
        metal_folder.mkdir(parents=True)
        self.metal = metal_folder / 'metal'
        self.metal.write_text('#!/bin/sh\necho "Fixture Metal version 1"\n')
        self.metal.chmod(0o755)
        self.metallib = metal_folder / 'metallib'
        self.metallib.write_text('Fixture metallib bytes\n')
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
        (self.bin / 'xcrun').write_text(
            '#!/bin/sh\nif [ "$1" = --find ]; then\n'
            f'  printf "%s\\n" "{metal_folder}/$2"\n'
            'else\n  echo "fixture tool metadata"\nfi\n')
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


class PreviewCheckpoint(unittest.TestCase):
    """Real archive tools and optional pinned GN/Siso, not browser/native QA."""

    def setUp(self):
        if not shutil.which('zstd') or not shutil.which('tar'):
            self.skipTest('Real tar and zstd executables are required')
        self.temp = tempfile.TemporaryDirectory(prefix='helium checkpoint ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'build/src'
        self.out = self.source / 'out/Default'
        self.out.mkdir(parents=True)
        self.script = self.root / '.github/scripts/mac_preview.sh'
        self.script.parent.mkdir(parents=True)
        self.script.write_bytes((ROOT / '.github/scripts/mac_preview.sh').read_bytes())
        (self.root / 'preview-logs').mkdir()
        (self.root / 'preview-logs/provenance.json').write_text('{"fixture": true}\n')

    def checkpoint(self):
        result = subprocess.run(['bash', str(self.script), 'checkpoint'], cwd=self.root,
                                text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return self.root / 'preview-checkpoint/build.tar.zst'

    def restore(self, archive):
        # Exercise the production decompression/extraction commands. Preserve
        # the same absolute source root while creating new filesystem objects.
        shutil.rmtree(self.source)
        result = subprocess.run(['bash', '-o', 'pipefail', '-c',
                                 'zstd -dc "$1" | tar -xf - -C "$2"',
                                 'restore', str(archive), str(self.root / 'build')],
                                text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_production_checkpoint_preserves_bytes_modes_and_nanosecond_mtimes(self):
        files = [self.source / 'input.cc', self.out / 'output.o',
                 self.out / '.siso_fs_state', self.out / '.siso_deps']
        before = {}
        for index, path in enumerate(files):
            path.write_bytes(b'fixture\x00' + bytes([index]))
            path.chmod(0o755 if index == 0 else 0o644)
            timestamp = 1700000000000000000 + 123456789 + index
            os.utime(path, ns=(timestamp, timestamp))
            before[path] = (path.read_bytes(), path.stat().st_mode & 0o777,
                            path.stat().st_mtime_ns)
        link = self.out / 'source-link'
        link.symlink_to('../../input.cc')
        archive = self.checkpoint()
        self.restore(archive)
        for path, expected in before.items():
            self.assertEqual((path.read_bytes(), path.stat().st_mode & 0o777,
                              path.stat().st_mtime_ns), expected, str(path))
        self.assertTrue(link.is_symlink())
        self.assertEqual(os.readlink(link), '../../input.cc')

    def test_real_pinned_gn_siso_reuses_checkpoint_with_and_without_regeneration(self):
        gn = os.environ.get('HELIUM_TEST_GN_BINARY')
        siso = os.environ.get('HELIUM_TEST_SISO_BINARY')
        if not gn or not siso:
            self.skipTest('Set HELIUM_TEST_GN_BINARY and HELIUM_TEST_SISO_BINARY for real reuse regression')
        self.assertEqual(subprocess.check_output([gn, '--version'], text=True).strip(),
                         '2578 (510ec7992c30)')
        version = subprocess.check_output([siso, 'version'], text=True)
        self.assertTrue(version.startswith('siso v1.6.3\n'), version)
        (self.source / '.gn').write_text('buildconfig = "//BUILDCONFIG.gn"\n')
        (self.source / 'BUILDCONFIG.gn').write_text('set_default_toolchain("//:default")\n')
        (self.source / 'copy.py').write_text('import shutil,sys\nshutil.copyfile(sys.argv[1],sys.argv[2])\n')
        count = 32
        graph = ['toolchain("default") { tool("stamp") { command = "touch {{output}}" } }']
        for index in range(count):
            (self.source / f'input{index:03}').write_text(f'content {index}\n')
            graph.append(f'action("copy{index:03}") {{ script="copy.py" '
                         f'sources=["input{index:03}"] outputs=["$target_gen_dir/result{index:03}"] '
                         'args=[rebase_path(sources[0],root_build_dir),rebase_path(outputs[0],root_build_dir)] }')
        graph.append('group("all") { deps=[' + ','.join(f'":copy{i:03}"' for i in range(count)) + '] }')
        (self.source / 'BUILD.gn').write_text('\n'.join(graph) + '\n')

        def generate():
            result = subprocess.run([gn, 'gen', 'out/Default'], cwd=self.source,
                                    text=True, capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        def build():
            result = subprocess.run([siso, 'ninja', '-C', 'out/Default', '--load', '',
                                     '--local_jobs=2', 'all'], cwd=self.source,
                                    text=True, capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return result.stdout + result.stderr

        generate()
        self.assertIn(f'local:{count} ', build())
        before = {path.name: (path.read_bytes(), path.stat().st_mtime_ns)
                  for path in (self.out / 'gen').glob('result*')}
        self.assertEqual(len(before), count)
        archive = self.checkpoint()
        for regenerate in [False, True]:
            with self.subTest(regenerate=regenerate):
                self.restore(archive)
                if regenerate:
                    generate()
                output = build()
                self.assertIn('ninja: no work to do.', output)
                self.assertEqual((self.out / 'siso_explain').read_text(), '')
                self.assertEqual({path.name: (path.read_bytes(), path.stat().st_mtime_ns)
                                  for path in (self.out / 'gen').glob('result*')}, before)
        # Negative control: restored state must not suppress a real source edit.
        (self.source / 'input000').write_text('changed input\n')
        self.assertIn('local:1 ', build())
        self.assertEqual((self.out / 'gen/result000').read_text(), 'changed input\n')


if __name__ == '__main__':
    unittest.main()
