#!/usr/bin/env python3
"""Local CI-contract checks only; these do NOT compile or run Chromium."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('preview_tests', ROOT / '.github/scripts/mac_preview_tests.py')
preview = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preview)


class PreviewContract(unittest.TestCase):
    def test_summary_requires_every_success_once_in_each_iteration(self):
        expected = {'Suite.One', 'Suite.Two'}
        good = {'per_iteration_data': [
            {n: [{'status': 'SUCCESS'}] for n in expected} for _ in range(3)]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'result.json'
            path.write_text(json.dumps(good))
            self.assertEqual(preview.verify_summary(path, expected), sorted(expected))
            bad = [
                {}, {'per_iteration_data': []},
                {'per_iteration_data': [{}, {}, {}]},
                {'per_iteration_data': [good['per_iteration_data'][0]]},
            ]
            for status in ['FAILURE', 'SKIPPED', 'TIMEOUT', 'CRASH']:
                item = json.loads(json.dumps(good))
                item['per_iteration_data'][0]['Suite.One'][0]['status'] = status
                bad.append(item)
            item = json.loads(json.dumps(good))
            item['per_iteration_data'][0]['Suite.One'].append({'status': 'SUCCESS'})
            bad.append(item)
            for payload in bad:
                path.write_text(json.dumps(payload))
                with self.assertRaises(AssertionError):
                    preview.verify_summary(path, expected)

    def test_fixed_runner_no_paid_or_artifact_route(self):
        workflow = (ROOT / '.github/workflows/mac-preview.yml').read_text()
        phase = (ROOT / '.github/workflows/mac-preview-phase.yml').read_text()
        self.assertEqual(workflow.count('    uses: ./.github/workflows/mac-preview-phase.yml'), 10)
        self.assertIn('runs-on: macos-26', workflow)
        self.assertIn('runs-on: macos-26', phase)
        for token in ['upload-artifact', 'secrets: inherit', 'depot-', 'macos-26-large', 'self-hosted']:
            self.assertNotIn(token, workflow + phase)
        self.assertIn('github.event.repository.private == false', workflow)
        self.assertIn('github.event.repository.private == false', phase)
        self.assertIn('if: ${{ always()', workflow)
        self.assertIn('fail-on-cache-miss: true', workflow)
        self.assertIn('CACHE_HIT: ${{ steps.restore.outputs.cache-hit }}', workflow)
        self.assertIn('CACHE_HIT: ${{ steps.restore.outputs.cache-hit }}', phase)
        self.assertIn('[[ "$CACHE_HIT" != true ]]', workflow)
        self.assertIn('[[ "$CACHE_HIT" != true ]]', phase)

    def test_true_compile_and_no_sandbox_bypass(self):
        script = (ROOT / '.github/scripts/mac_preview.sh').read_text()
        self.assertIn('build -j 2 chrome/installer/mac', script)
        self.assertNotIn('build -j 2 chrome/installer/mac browser_tests', script)
        self.assertIn("'status': 'UNRUN'", script)
        self.assertIn('gtimeout -k 2m', script)
        self.assertIn('use_thin_lto=false', script)
        self.assertIn('concurrent_links=1', script)
        self.assertIn('symbol_level=0', script)
        self.assertIn('enable_sparkle=false', script)
        self.assertIn('enable_updater=false', script)
        for forbidden in ['--no-sandbox', 'spctl --master-disable', 'xattr -d', 'is_cfi=false', 'is_official_build=false']:
            self.assertNotIn(forbidden, script)

    def test_preview_identity_and_draft_only(self):
        identity = (ROOT / 'patches/helium/macos/arc-preview-identity.patch').read_text()
        self.assertIn('+PRODUCT_FULLNAME=Helium Arc Preview', identity)
        self.assertIn('+MAC_BUNDLE_ID=io.github.iamrajjoshi.helium-arc-preview', identity)
        self.assertIn('Helium Arc Preview Storage Key', identity)
        self.assertIn('UsesSeparateDefaultDataAndCache', identity)
        draft = (ROOT / '.github/scripts/mac_preview_draft.js').read_text()
        self.assertIn('draft: true, prerelease: true', draft)
        self.assertNotIn('draft: false', draft)
        self.assertIn('2 * 1024 ** 3', draft)
        self.assertIn('Existing asset mismatch', draft)


if __name__ == '__main__':
    unittest.main()
