#!/usr/bin/env python3
"""Local provenance/recovery contract tests; no remote cache or native execution."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('provenance', ROOT / '.github/scripts/mac_preview_provenance.py')
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)


def legacy_metadata():
    return {'schema': 1, 'source_repository': 'iamrajjoshi/helium',
            'source_sha': p.LEGACY_SOURCE, 'packaging_sha': p.LEGACY_PACKAGING,
            'arch': 'arm64', 'xcode': 'Xcode 26.5\nBuild version 17F42',
            'sdk_version': '26.5', 'sdk_build': '25F70', 'sdk_path': '/Applications/Xcode_26.5.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX26.5.sdk',
            'metal': p.LEGACY_METAL, 'python': 'Python 3.13.16',
            'args_sha256': '98d97624d5bef7e3e8f2003304002cb130511e5d299dbab3e45957b573c7ae03',
            'clang_sha256': 'a0acdda5df49257a38004653c4689a76be76e90bdf01393be2055848528069fd',
            'gn_sha256': '8b50f57cfac9eba62f07ce5d9ad15d738c6bfeb6e58de33fe2b586800545bee6',
            'siso_sha256': '6ff644f51e5395bb98c909d2aa994acb881cc8400b06f041ab33c2fbb74a24b2', 'targets': ['chrome', 'chromedriver', 'chrome/installer/mac'],
            'compile_jobs': 2, 'link_jobs': 1}


def current_metadata():
    return dict(legacy_metadata(), schema=2, packaging_sha='b' * 40,
                metal={'path': p.stable_metal_path(p.LEGACY_METAL), 'version': 'Apple metal version fixture',
                       'sha256': '5' * 64, 'metallib_sha256': '6' * 64})


class PreviewProvenance(unittest.TestCase):
    def test_exact_saved_manifest_fixture_matches_logged_sha256(self):
        raw = (json.dumps(legacy_metadata(), sort_keys=True, indent=2) + '\n').encode()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), p.LEGACY_PROVENANCE_SHA256)

    def test_only_exact_random_mount_component_normalizes(self):
        other = p.LEGACY_METAL.replace('MXlKIW', 'uACdZV')
        self.assertEqual(p.stable_metal_path(other), p.stable_metal_path(p.LEGACY_METAL))
        self.assertNotEqual(p.stable_metal_path(other.replace('17.6.42.0', '17.6.43.0')),
                            p.stable_metal_path(p.LEGACY_METAL))
        for bad in [other.replace('uACdZV', '..'), other + '/extra', other.replace('/var/run/', '/tmp/'),
                    other.replace('/usr/bin/metal', '/usr/bin/not-metal')]:
            with self.subTest(path=bad), self.assertRaises(ValueError):
                p.stable_metal_path(bad)

    def test_real_executable_hashes_ignore_nonce_but_detect_changed_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pattern = re.compile(p.METAL_PATH.pattern.replace('/var/run', re.escape(str(root))))
            identities = []
            with mock.patch.object(p, 'METAL_PATH', pattern):
                for nonce in ['ABC123', 'XYZ789']:
                    metal = root / p.LEGACY_METAL.removeprefix('/var/run/').replace('MXlKIW', nonce)
                    metal.parent.mkdir(parents=True)
                    metal.write_bytes(b'identical executable')
                    lib = metal.with_name('metallib'); lib.write_bytes(b'identical library tool')
                    identities.append(p.metal_identity(str(metal), f'Apple metal 17\nInstalledDir: {metal.parent}', str(lib)))
                self.assertEqual(*identities)
                lib.write_bytes(b'changed library tool')
                self.assertNotEqual(identities[0], p.metal_identity(str(metal), f'Apple metal 17\nInstalledDir: {metal.parent}', str(lib)))
                lib.write_bytes(b'identical library tool'); metal.write_bytes(b'changed executable')
                self.assertNotEqual(identities[0], p.metal_identity(str(metal), f'Apple metal 17\nInstalledDir: {metal.parent}', str(lib)))

    def test_normal_restores_require_all_fields_including_metal_hashes_and_version(self):
        current = current_metadata()
        p.verify_metadata(current, copy.deepcopy(current))
        for key in current:
            changed = copy.deepcopy(current)
            changed[key] = 'changed'
            with self.subTest(field=key), self.assertRaises((ValueError, TypeError)):
                p.verify_metadata(current, changed)
        for key in current['metal']:
            changed = copy.deepcopy(current)
            changed['metal'][key] = '7' * 64 if key.endswith('sha256') else 'changed'
            with self.subTest(metal_field=key), self.assertRaises(ValueError):
                p.verify_metadata(current, changed)

    def test_legacy_only_permits_schema_packaging_and_known_mount_migration(self):
        old, new = legacy_metadata(), current_metadata()
        p.verify_metadata(old, new, legacy=True)
        for key in old:
            if key in ['schema', 'packaging_sha', 'metal']:
                continue
            changed = copy.deepcopy(new); changed[key] = 'changed'
            with self.subTest(field=key), self.assertRaises(ValueError):
                p.verify_metadata(old, changed, legacy=True)
        for bad in [dict(old, source_sha='a' * 40), dict(old, packaging_sha='a' * 40),
                    dict(old, metal=p.LEGACY_METAL.replace('MXlKIW', 'OTHER1')), dict(old, unexpected=True)]:
            with self.assertRaises(ValueError):
                p.verify_metadata(bad, new, legacy=True)
        changed = copy.deepcopy(new); changed['metal']['path'] = changed['metal']['path'].replace('17.6.42.0', '17.6.43.0')
        with self.assertRaises(ValueError):
            p.verify_metadata(old, changed, legacy=True)

    def test_normal_checkpoint_gate_cannot_select_other_run(self):
        with mock.patch.dict(os.environ, {'RECOVER_PHASE_ONE': 'false', 'GITHUB_RUN_ID': '123', 'RESTORE_KEY': 'helium-preview-123-2-1'}):
            p.checkpoint_gate(ROOT)
            for key in [p.LEGACY_KEY, 'helium-preview-1234-2-1', '', 'helium-preview-123-11-1']:
                with mock.patch.dict(os.environ, {'RESTORE_KEY': key}), self.assertRaises(ValueError):
                    p.checkpoint_gate(ROOT)

    def test_exact_legacy_hashes_context_and_build_tree_required(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); folder = root / 'preview-checkpoint'; folder.mkdir()
            archive = folder / 'build.tar.zst'; archive.write_bytes(b'fixture archive')
            manifest = folder / 'provenance.json'; manifest.write_text(json.dumps(legacy_metadata()))
            env = {'RECOVER_PHASE_ONE': 'true', 'PHASE': '1', 'SOURCE_SHA': p.LEGACY_SOURCE, 'RESTORE_KEY': p.LEGACY_KEY}
            with mock.patch.dict(os.environ, env), mock.patch.object(p, 'LEGACY_ARCHIVE_SHA256', p.digest(archive)), \
                    mock.patch.object(p, 'LEGACY_PROVENANCE_SHA256', p.digest(manifest)), mock.patch.object(p, 'audit_packaging') as audit:
                p.checkpoint_gate(root); audit.assert_called_once_with(root)
                for key, value in [('PHASE', '2'), ('SOURCE_SHA', 'a' * 40), ('RESTORE_KEY', p.LEGACY_KEY + '-other')]:
                    with mock.patch.dict(os.environ, {key: value}), self.assertRaises(ValueError):
                        p.checkpoint_gate(root)
                archive.write_bytes(b'changed archive')
                with self.assertRaises(ValueError): p.checkpoint_gate(root)
                archive.write_bytes(b'fixture archive'); manifest.write_text('{}')
                with self.assertRaises(ValueError): p.checkpoint_gate(root)

    def test_build_tree_pin_includes_all_non_ci_bytes_and_modes(self):
        tree = subprocess.check_output(['git', '-C', str(ROOT), 'ls-tree', '-rz', p.LEGACY_PACKAGING])
        self.assertEqual(p.build_tree_digest(tree), p.LEGACY_BUILD_TREE_SHA256)
        p.audit_packaging(ROOT)
        # Every non-CI entry is hashed; a source patch path, object ID, or mode change fails.
        self.assertNotEqual(p.build_tree_digest(tree + b'100644 blob ' + b'a' * 40 + b'\tpatches/evil.patch\0'), p.LEGACY_BUILD_TREE_SHA256)
        self.assertNotEqual(p.build_tree_digest(tree.replace(b'100644', b'100755', 1)), p.LEGACY_BUILD_TREE_SHA256)
        self.assertEqual(p.build_tree_digest(tree + b'100644 blob ' + b'a' * 40 + b'\t.github/scripts/mac_preview_provenance.py\0'), p.LEGACY_BUILD_TREE_SHA256)

    def test_stale_generated_metal_path_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); out = root / 'build/src/out/Default'; out.mkdir(parents=True)
            current = p.LEGACY_METAL.replace('MXlKIW', 'uACdZV')
            graph = out / 'toolchain.ninja'
            with mock.patch.object(p, 'command', return_value=current):
                graph.write_text('command = xcrun metal')
                p.check_graph(root)
                graph.write_text('command = ' + current)
                p.check_graph(root)
                graph.write_text('command = ' + p.LEGACY_METAL)
                with self.assertRaises(ValueError): p.check_graph(root)

    def test_legacy_invalidates_only_three_declared_outputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); out = root / 'build/src/out/Default/gen/angle'; out.mkdir(parents=True)
            names = ['mtl_internal_shaders_autogen.air', 'mtl_internal_shaders_autogen.metallib', 'mtl_internal_shaders_metallib.h']
            for name in names + ['unrelated.o']: (out / name).write_bytes(b'keep or rebuild')
            with mock.patch.dict(os.environ, {'RECOVER_PHASE_ONE': 'true'}), \
                    mock.patch.object(p.subprocess, 'check_output', side_effect=['angle_metal_toolchain_dir = ""\n', 'use_system_xcode = true\n']):
                p.invalidate_legacy_metal(root)
            self.assertTrue((out / 'unrelated.o').exists())
            self.assertTrue(all(not (out / name).exists() for name in names))
            with mock.patch.dict(os.environ, {'RECOVER_PHASE_ONE': 'true'}), \
                    mock.patch.object(p.subprocess, 'check_output', return_value='angle_metal_toolchain_dir = "/other"\n'), self.assertRaises(ValueError):
                p.invalidate_legacy_metal(root)
            with mock.patch.dict(os.environ, {'RECOVER_PHASE_ONE': 'true'}), \
                    mock.patch.object(p.subprocess, 'check_output', side_effect=['angle_metal_toolchain_dir = ""\n', 'use_system_xcode = false\n']), self.assertRaises(ValueError):
                p.invalidate_legacy_metal(root)


if __name__ == '__main__':
    unittest.main()
