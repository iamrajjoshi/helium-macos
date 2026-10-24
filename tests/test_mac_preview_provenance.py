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


SAVED_METADATA_JSON = r'''{
  "arch": "arm64",
  "args_sha256": "98d97624d5bef7e3e8f2003304002cb130511e5d299dbab3e45957b573c7ae03",
  "clang_sha256": "a0acdda5df49257a38004653c4689a76be76e90bdf01393be2055848528069fd",
  "compile_jobs": 2,
  "gn_sha256": "8b50f57cfac9eba62f07ce5d9ad15d738c6bfeb6e58de33fe2b586800545bee6",
  "link_jobs": 1,
  "metal": {
    "metallib_sha256": "c914999bf5a2cb10ac2303d2faca5dddacd1311f4fb58eb1b8723a30938245ad",
    "path": "/var/run/com.apple.security.cryptexd/mnt/com.apple.MobileAsset.MetalToolchain-v17.6.42.0.<mount>/Metal.xctoolchain/usr/bin/metal",
    "sha256": "60fa8ee31c7befa609708d853e28adcc1841105b67f4dfb9268030474d6290aa",
    "version": "Apple metal version 32023.883 (metalfe-32023.883)\nTarget: air64-apple-darwin25.6.0\nThread model: posix\nInstalledDir: /private/var/run/com.apple.security.cryptexd/mnt/com.apple.MobileAsset.MetalToolchain-v17.6.42.0.f8TSG3/Metal.xctoolchain/usr/metal/current/bin"
  },
  "packaging_sha": "1f1cc956e4c094f43dad5f3b1fd6e211ca3cb004",
  "python": "Python 3.13.16",
  "schema": 2,
  "sdk_build": "25F70",
  "sdk_path": "/Applications/Xcode_26.5.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX26.5.sdk",
  "sdk_version": "26.5",
  "siso_sha256": "6ff644f51e5395bb98c909d2aa994acb881cc8400b06f041ab33c2fbb74a24b2",
  "source_repository": "iamrajjoshi/helium",
  "source_sha": "0b1af54b49c343484035c0977a85c4cc8642595a",
  "targets": [
    "chrome",
    "chromedriver",
    "chrome/installer/mac"
  ],
  "xcode": "Xcode 26.5\nBuild version 17F42"
}
'''


SAVED_ORIGIN_JSON = r'''{
  "archive_sha256": "4c775ddebcde7ea0be330be1f24533dc341bd67b2de2fd7672191f1c655545f1",
  "checkpoint_key": "helium-preview-40019797612-1-1",
  "legacy_packaging_sha": "7935eb905b333044ade4653a144b1ac3e1b0a8f1",
  "legacy_provenance_sha256": "00f840bbaa377de729dd8edefda8031acfb1b1fc1a49e621e97fb0ef46290ae0",
  "metal_historical_evidence": "Legacy recorded exact toolchain version/path, not executable bytes. Only the known mount nonce is normalized; all other legacy metadata is identical. Metal and metallib hashes plus version output are enforced from schema 2 onward. The three declared ANGLE Metal generated outputs are invalidated for rebuilding.",
  "new_packaging_sha": "1f1cc956e4c094f43dad5f3b1fd6e211ca3cb004",
  "unchanged_build_tree_sha256": "af6bed91b8c6bffcea50a4d10768b23f34e9a518ec2955f9094fc3db51979fa9"
}
'''


def saved_metadata():
    return json.loads(SAVED_METADATA_JSON)


def current_metadata():
    current = saved_metadata()
    current['packaging_sha'] = 'b' * 40
    current['metal']['version'] = p.stable_metal_version(p.BRIDGE_METAL_RAW_PATH, current['metal']['version'])
    return current


class PreviewProvenance(unittest.TestCase):
    def test_exact_saved_manifest_fixture_matches_logged_sha256(self):
        raw = (json.dumps(saved_metadata(), sort_keys=True, indent=2) + '\n').encode()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), p.BRIDGE_PROVENANCE_SHA256)
        self.assertEqual(hashlib.sha256(SAVED_ORIGIN_JSON.encode()).hexdigest(), p.BRIDGE_ORIGIN_SHA256)

    def test_only_exact_random_mount_component_normalizes(self):
        other = p.BRIDGE_METAL_RAW_PATH.replace('f8TSG3', 'uACdZV')
        self.assertEqual(p.stable_metal_path(other), p.stable_metal_path(p.BRIDGE_METAL_RAW_PATH))
        self.assertNotEqual(p.stable_metal_path(other.replace('17.6.42.0', '17.6.43.0')),
                            p.stable_metal_path(p.BRIDGE_METAL_RAW_PATH))
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
                    metal = root / p.BRIDGE_METAL_RAW_PATH.removeprefix('/var/run/').replace('f8TSG3', nonce)
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

    def test_bridge_only_permits_packaging_and_exact_installed_dir_normalization(self):
        old, new = saved_metadata(), current_metadata()
        p.verify_metadata(old, new, bridge=True)
        for key in old:
            if key == 'packaging_sha':
                continue
            changed = copy.deepcopy(new); changed[key] = 'changed'
            with self.subTest(field=key), self.assertRaises((ValueError, TypeError)):
                p.verify_metadata(old, changed, bridge=True)
        for key in old['metal']:
            changed = copy.deepcopy(new)
            changed['metal'][key] = '7' * 64 if key.endswith('sha256') else 'changed'
            with self.subTest(metal_field=key), self.assertRaises(ValueError):
                p.verify_metadata(old, changed, bridge=True)
        for bad in [dict(old, schema=1), dict(old, source_sha='a' * 40),
                    dict(old, packaging_sha='a' * 40), dict(old, unexpected=True)]:
            with self.assertRaises(ValueError):
                p.verify_metadata(bad, new, bridge=True)
        changed = copy.deepcopy(old)
        changed['metal']['version'] = changed['metal']['version'].replace('f8TSG3', 'OTHER1')
        with self.assertRaises(ValueError):
            p.verify_metadata(changed, new, bridge=True)

    def test_observed_installed_dir_alias_normalizes_only_matching_nonce_and_layout(self):
        path = p.BRIDGE_METAL_RAW_PATH
        version = saved_metadata()['metal']['version']
        stable = p.stable_metal_version(path, version)
        self.assertEqual(stable, version.replace('f8TSG3', '<mount>'))
        self.assertEqual(stable, p.stable_metal_version(path.replace('f8TSG3', 'ABC123'),
                                                       version.replace('f8TSG3', 'ABC123')))
        for changed in [version.replace('f8TSG3', 'OTHER1'),
                        version.replace('17.6.42.0', '17.6.43.0'),
                        version.replace('/usr/metal/current/bin', '/usr/other/bin'),
                        version.replace('/private/var/run/', '/tmp/'),
                        version + '\nInstalledDir: ' + str(Path(path).parent)]:
            with self.subTest(version=changed), self.assertRaises(ValueError):
                p.stable_metal_version(path, changed)
        for before, after in [('32023.883', '32023.884'), ('darwin25.6.0', 'darwin25.7.0'),
                              ('posix', 'other')]:
            changed = p.stable_metal_version(path, version.replace(before, after))
            self.assertNotEqual(changed, stable)
            current = current_metadata(); current['metal']['version'] = changed
            with self.assertRaises(ValueError):
                p.verify_metadata(saved_metadata(), current, bridge=True)

    def test_normal_checkpoint_gate_cannot_select_other_run(self):
        with mock.patch.dict(os.environ, {'RECOVER_PHASE_ONE': 'false', 'GITHUB_RUN_ID': '123', 'RESTORE_KEY': 'helium-preview-123-2-1'}):
            p.checkpoint_gate(ROOT)
            for key in [p.BRIDGE_KEY, 'helium-preview-1234-2-1', '', 'helium-preview-123-11-1']:
                with mock.patch.dict(os.environ, {'RESTORE_KEY': key}), self.assertRaises(ValueError):
                    p.checkpoint_gate(ROOT)

    def test_exact_bridge_hashes_context_and_build_tree_required(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); folder = root / 'preview-checkpoint'; folder.mkdir()
            archive = folder / 'build.tar.zst'; archive.write_bytes(b'fixture archive')
            manifest = folder / 'provenance.json'; manifest.write_text(json.dumps(saved_metadata()))
            origin = folder / 'recovery-origin.json'; origin.write_text('{"prior": true}')
            env = {'RECOVER_PHASE_ONE': 'true', 'PHASE': '1', 'SOURCE_SHA': p.BRIDGE_SOURCE, 'RESTORE_KEY': p.BRIDGE_KEY}
            with mock.patch.dict(os.environ, env), mock.patch.object(p, 'BRIDGE_ARCHIVE_SHA256', p.digest(archive)), \
                    mock.patch.object(p, 'BRIDGE_PROVENANCE_SHA256', p.digest(manifest)), \
                    mock.patch.object(p, 'BRIDGE_ORIGIN_SHA256', p.digest(origin)), mock.patch.object(p, 'audit_packaging') as audit:
                p.checkpoint_gate(root); audit.assert_called_once_with(root)
                for key, value in [('PHASE', '2'), ('SOURCE_SHA', 'a' * 40), ('RESTORE_KEY', p.BRIDGE_KEY + '-other')]:
                    with mock.patch.dict(os.environ, {key: value}), self.assertRaises(ValueError):
                        p.checkpoint_gate(root)
                origin.write_text('{}')
                with self.assertRaises(ValueError): p.checkpoint_gate(root)
                origin.write_text('{"prior": true}')
                archive.write_bytes(b'changed archive')
                with self.assertRaises(ValueError): p.checkpoint_gate(root)
                archive.write_bytes(b'fixture archive'); manifest.write_text('{}')
                with self.assertRaises(ValueError): p.checkpoint_gate(root)

    def test_build_tree_pin_includes_all_non_ci_bytes_and_modes(self):
        tree = subprocess.check_output(['git', '-C', str(ROOT), 'ls-tree', '-rz', p.BRIDGE_PACKAGING])
        self.assertEqual(p.build_tree_digest(tree), p.BRIDGE_BUILD_TREE_SHA256)
        p.audit_packaging(ROOT)
        # Every non-CI entry is hashed; a source patch path, object ID, or mode change fails.
        self.assertNotEqual(p.build_tree_digest(tree + b'100644 blob ' + b'a' * 40 + b'\tpatches/evil.patch\0'), p.BRIDGE_BUILD_TREE_SHA256)
        self.assertNotEqual(p.build_tree_digest(tree.replace(b'100644', b'100755', 1)), p.BRIDGE_BUILD_TREE_SHA256)
        self.assertEqual(p.build_tree_digest(tree + b'100644 blob ' + b'a' * 40 + b'\t.github/scripts/mac_preview_provenance.py\0'), p.BRIDGE_BUILD_TREE_SHA256)

    def test_stale_generated_metal_path_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); out = root / 'build/src/out/Default'; out.mkdir(parents=True)
            current = p.BRIDGE_METAL_RAW_PATH.replace('f8TSG3', 'uACdZV')
            graph = out / 'toolchain.ninja'
            with mock.patch.object(p, 'command', return_value=current):
                graph.write_text('command = xcrun metal')
                p.check_graph(root)
                graph.write_text('command = ' + current)
                p.check_graph(root)
                graph.write_text('command = ' + p.BRIDGE_METAL_RAW_PATH)
                with self.assertRaises(ValueError): p.check_graph(root)

    def test_bridge_report_retains_prior_audit_chain_and_never_deletes_outputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'preview-checkpoint').mkdir(); (root / 'preview-logs').mkdir()
            (root / 'preview-checkpoint/provenance.json').write_text(SAVED_METADATA_JSON)
            (root / 'preview-logs/provenance-current.json').write_text(json.dumps(current_metadata()))
            origin = root / 'preview-checkpoint/recovery-origin.json'
            prior = json.loads(SAVED_ORIGIN_JSON); origin.write_text(SAVED_ORIGIN_JSON)
            output = root / 'build/src/out/Default/gen/angle/mtl_internal_shaders_autogen.air'
            output.parent.mkdir(parents=True); output.write_bytes(b'preserve exact compiled output')
            with mock.patch.dict(os.environ, {'RECOVER_PHASE_ONE': 'true'}):
                p.verify(root)
            report = json.loads((root / 'preview-logs/recovery-origin.json').read_text())
            self.assertEqual(report['prior_recovery_origin'], prior)
            self.assertEqual(report['checkpoint_key'], p.BRIDGE_KEY)
            self.assertEqual(report['saved_provenance_sha256'], p.BRIDGE_PROVENANCE_SHA256)
            self.assertEqual(report['saved_packaging_sha'], p.BRIDGE_PACKAGING)
            self.assertEqual(output.read_bytes(), b'preserve exact compiled output')


if __name__ == '__main__':
    unittest.main()
