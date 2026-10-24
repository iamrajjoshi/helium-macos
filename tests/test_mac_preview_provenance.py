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


def metal_action_results():
    root = '//third_party/angle/src/libANGLE/renderer/metal'
    values = ['angle_metal_toolchain_dir = ""\n']
    for name, tool, output in [('angle_metal_internal_shaders_to_air', 'metal', 'mtl_internal_shaders_autogen.air'),
                               ('angle_metal_internal_shaders_to_mtllib', 'metallib', 'mtl_internal_shaders_autogen.metallib')]:
        values.append(json.dumps({root + ':' + name: {'type': 'action', 'script': root + '/shaders/metal_wrapper.py',
                                                     'args': ['xcrun', tool], 'outputs': ['//out/Default/gen/angle/' + output]}}))
    return values

# Copyright 2019 The ANGLE Project Authors. BSD-style license.
# Exact run_metal_tool template/actions from ANGLE commit
# 3b89d84e9d59f75675ac4e2bef58fc068a9f0577, src/libANGLE/renderer/metal/BUILD.gn.
PINNED_ANGLE_METAL_ACTIONS = r'''  template("run_metal_tool") {
    action(target_name) {
      forward_variables_from(invoker,
                             [
                               "deps",
                               "sources",
                               "outputs",
                               "metal_tool",
                             ])
      script = "shaders/metal_wrapper.py"
      if (angle_metal_toolchain_dir != "") {
        # Explicit Metal toolchain: run the "metal"/"metallib" binaries directly
        # from the provided directory. This bypasses "xcrun" and the hermetic
        # Chromium Mac SDK, which is required to prebuild the internal shaders
        # for iOS and other cross-compilation scenarios.
        args = [ angle_metal_toolchain_dir + "/" + metal_tool ]
      } else if (use_system_xcode) {
        # System Xcode: run metal and metallib via xcrun. Since Xcode 26.0, the
        # Metal toolchain has been unbundled from Xcode, and must be installed
        # separately by running `xcodebuild -downloadComponent MetalToolchain`.
        # There is a vestigial metal executable in mac_bin_path, but it’s
        # incapable of running successfuly without the
        # rest of the Metal toolchain surrounding it. `xcrun` is able to find
        # and run the correct Metal toolchain when properly installed.
        #
        # If you’re using system Xcode and your build fails with this message:
        #   error: error: cannot execute tool 'metal' due to missing Metal Toolchain; use: xcodebuild -downloadComponent MetalToolchain
        # then do what the error message suggests, and then retry your build.
        args = [
          "xcrun",
          metal_tool,
        ]
      } else {
        # Hermetic Xcode: at least for now, the Metal toolchain is
        # “spliced” into the location in the hermetic toolchain where it
        # lived before Xcode 26.0, so it can be run directly from there.
        args = [ mac_bin_path + metal_tool ]
      }

      args += invoker.args
    }
  }

  _metal_internal_shaders_air_file =
      "$root_gen_dir/angle/mtl_internal_shaders_autogen.air"

  run_metal_tool("angle_metal_internal_shaders_to_air") {
    _metal_internal_shaders_metal_source =
        "shaders/mtl_internal_shaders_autogen.metal"
    sources = [ _metal_internal_shaders_metal_source ]

    outputs = [ _metal_internal_shaders_air_file ]

    metal_tool = "metal"

    args = [
      "-c",
      rebase_path(_metal_internal_shaders_metal_source, root_build_dir),
      "-o",
      rebase_path(_metal_internal_shaders_air_file, root_build_dir),
    ]

    if (is_mac) {
      args += [
        "--std=macos-metal2.1",
        "-mmacosx-version-min=10.14",
      ]
    } else if (is_ios) {
      args += [
        "--std=ios-metal2.1",
        "-mios-version-min=12",
      ]
    }
  }

  _metal_internal_shaders_metallib_file =
      "$root_gen_dir/angle/mtl_internal_shaders_autogen.metallib"

  run_metal_tool("angle_metal_internal_shaders_to_mtllib") {
    deps = [ ":angle_metal_internal_shaders_to_air" ]

    sources = [ _metal_internal_shaders_air_file ]

    outputs = [ _metal_internal_shaders_metallib_file ]

    metal_tool = "metallib"

    args = [
      rebase_path(_metal_internal_shaders_air_file, root_build_dir),
      "-o",
      rebase_path(_metal_internal_shaders_metallib_file, root_build_dir),
    ]
  }

'''


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
                    mock.patch.object(p.subprocess, 'check_output', side_effect=metal_action_results()):
                p.invalidate_legacy_metal(root)
            self.assertTrue((out / 'unrelated.o').exists())
            self.assertTrue(all(not (out / name).exists() for name in names))

    def test_wrong_generated_route_wrapper_target_or_output_preserves_outputs(self):
        good = metal_action_results()
        variants = [['angle_metal_toolchain_dir = "/other"\n']]
        for field, value in [('type', 'group'), ('script', '//other.py'), ('args', ['/hermetic/metal']),
                             ('outputs', ['//out/Default/gen/other.air'])]:
            altered = json.loads(good[1]); altered[next(iter(altered))][field] = value
            variants.append([good[0], json.dumps(altered), good[2]])
        variants.append([good[0], '{}', good[2]])
        altered = json.loads(good[2]); altered[next(iter(altered))]['args'] = ['xcrun', 'not-metallib']
        variants.append([good[0], good[1], json.dumps(altered)])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); out = root / 'build/src/out/Default/gen/angle'; out.mkdir(parents=True)
            output = out / 'mtl_internal_shaders_autogen.air'; output.write_bytes(b'preserve')
            for responses in variants:
                with mock.patch.dict(os.environ, {'RECOVER_PHASE_ONE': 'true'}), \
                        mock.patch.object(p.subprocess, 'check_output', side_effect=responses), self.assertRaises(ValueError):
                    p.invalidate_legacy_metal(root)
                self.assertEqual(output.read_bytes(), b'preserve')

    def test_real_pinned_gn_computed_arg_and_generated_action_semantics(self):
        binary = os.environ.get('HELIUM_TEST_GN_BINARY')
        if not binary:
            self.skipTest('Set HELIUM_TEST_GN_BINARY to official pinned GN to run real semantics')
        binary = str(Path(binary).resolve())
        self.assertEqual(subprocess.check_output([binary, '--version'], text=True).strip(), '2578 (510ec7992c30)')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); source = root / 'build/src'; source.mkdir(parents=True)
            (source / '.gn').write_text('buildconfig = "//BUILDCONFIG.gn"\n')
            # Pinned Chromium build_overrides/build.gni declares an empty arg,
            # then computes its boolean outside declare_args. GN reports the
            # declared argument value, not that later effective boolean.
            (source / 'BUILDCONFIG.gn').write_text('''set_default_toolchain("//:default")
declare_args() {
  use_system_xcode = ""
  angle_metal_toolchain_dir = ""
}
if (use_system_xcode == "") {
  _result = 0
  use_system_xcode = _result != 1
}
is_mac = true
is_ios = false
mac_bin_path = "/hermetic/"
''')
            label_root = '//third_party/angle/src/libANGLE/renderer/metal'
            (source / 'BUILD.gn').write_text('''toolchain("default") {
  tool("stamp") { command = "touch {{output}}" }
}
group("all") {
  deps = [ "//third_party/angle/src/libANGLE/renderer/metal:angle_metal_internal_shaders_to_mtllib" ]
}
''')
            metal = source / label_root.removeprefix('//'); metal.mkdir(parents=True)
            (metal / 'BUILD.gn').write_text(PINNED_ANGLE_METAL_ACTIONS)
            (metal / 'shaders').mkdir()
            (metal / 'shaders/metal_wrapper.py').write_text('# Not executed in this GN-only test.\n')
            (metal / 'shaders/mtl_internal_shaders_autogen.metal').write_text('// Fixture input\n')
            gn = source / 'buildtools/mac/gn'; gn.parent.mkdir(parents=True); gn.symlink_to(binary)
            def generate(args):
                subprocess.run([binary, 'gen', 'out/Default', '--args=' + args], cwd=source,
                               check=True, capture_output=True, text=True)
            generate('')
            reported = subprocess.check_output([binary, 'args', 'out/Default', '--list=use_system_xcode', '--short'], cwd=source, text=True)
            self.assertEqual(reported.strip(), 'use_system_xcode = ""')
            print('Real pinned GN declared use_system_xcode output:', repr(reported))
            out = source / 'out/Default/gen/angle'; out.mkdir(parents=True, exist_ok=True)
            names = ['mtl_internal_shaders_autogen.air', 'mtl_internal_shaders_autogen.metallib', 'mtl_internal_shaders_metallib.h']
            for name in names + ['unrelated.o']: (out / name).write_bytes(b'preserve or invalidate')
            with mock.patch.dict(os.environ, {'RECOVER_PHASE_ONE': 'true'}):
                p.invalidate_legacy_metal(root)
            self.assertTrue(all(not (out / name).exists() for name in names))
            self.assertTrue((out / 'unrelated.o').exists())
            for args in ['use_system_xcode=false', 'angle_metal_toolchain_dir="/other"']:
                generate(args)
                output = out / names[0]; output.write_bytes(b'preserve')
                with mock.patch.dict(os.environ, {'RECOVER_PHASE_ONE': 'true'}), self.assertRaises(ValueError):
                    p.invalidate_legacy_metal(root)
                self.assertEqual(output.read_bytes(), b'preserve')


if __name__ == '__main__':
    unittest.main()
