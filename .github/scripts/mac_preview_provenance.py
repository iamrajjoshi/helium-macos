#!/usr/bin/env python3
"""Strict checkpoint identity plus one explicitly pinned legacy migration."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

LEGACY_KEY = 'helium-preview-40019797612-1-1'
LEGACY_SOURCE = '0b1af54b49c343484035c0977a85c4cc8642595a'
LEGACY_PACKAGING = '7935eb905b333044ade4653a144b1ac3e1b0a8f1'
LEGACY_ARCHIVE_SHA256 = '4c775ddebcde7ea0be330be1f24533dc341bd67b2de2fd7672191f1c655545f1'
LEGACY_PROVENANCE_SHA256 = '00f840bbaa377de729dd8edefda8031acfb1b1fc1a49e621e97fb0ef46290ae0'
LEGACY_METAL = '/var/run/com.apple.security.cryptexd/mnt/com.apple.MobileAsset.MetalToolchain-v17.6.42.0.MXlKIW/Metal.xctoolchain/usr/bin/metal'
# Every other Git tree entry (including executable modes and submodule pointers)
# must equal the audited original packaging commit. These are CI-only changes.
RECOVERY_CI_PATHS = frozenset({
    '.github/scripts/mac_preview.sh',
    '.github/scripts/mac_preview_provenance.py',
    '.github/scripts/mac_preview_cache.js',
    '.github/workflows/mac-preview.yml',
    '.github/workflows/mac-preview-phase.yml',
    'tests/test_mac_preview_prepare.py',
    'tests/test_mac_preview_provenance.py',
    'tests/test_mac_preview_cache.js',
    'docs/arc-preview-build.md',
})
LEGACY_BUILD_TREE_SHA256 = 'af6bed91b8c6bffcea50a4d10768b23f34e9a518ec2955f9094fc3db51979fa9'
METAL_PATH = re.compile(r'(?P<root>/var/run/com\.apple\.security\.cryptexd/mnt/com\.apple\.MobileAsset\.MetalToolchain-v(?P<version>\d+(?:\.\d+){3}))\.(?P<nonce>[A-Za-z0-9]{6})(?P<tail>/Metal\.xctoolchain/usr/bin/metal)\Z')
MOUNT_IN_GRAPH = re.compile(rb'/var/run/com\.apple\.security\.cryptexd/mnt/com\.apple\.MobileAsset\.MetalToolchain-v\d+(?:\.\d+){3}\.[A-Za-z0-9]{6}')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def command(*args):
    return subprocess.check_output(args, text=True).strip()


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def stable_metal_path(path):
    match = METAL_PATH.fullmatch(path)
    require(match is not None, 'Unrecognized Metal toolchain path; cannot normalize')
    return match['root'] + '.<mount>' + match['tail']


def metal_identity(path, version, metallib):
    stable = stable_metal_path(path)
    # Clang-style version output may contain InstalledDir. Remove only the same
    # verified mount nonce there, not version/build/target information.
    version = version.replace(str(Path(path).parent), str(Path(stable).parent))
    require(bool(version), 'Empty Metal version output')
    require(Path(metallib) == Path(path).with_name('metallib'), 'Metal/metallib toolchains differ')
    return {'path': stable, 'version': version, 'sha256': digest(path),
            'metallib_sha256': digest(metallib)}


def record(root):
    root = Path(root)
    metal = command('xcrun', '--find', 'metal')
    return {
        'schema': 2, 'source_repository': 'iamrajjoshi/helium',
        'source_sha': os.environ['SOURCE_SHA'], 'packaging_sha': os.environ['GITHUB_SHA'],
        'arch': command('uname', '-m'), 'xcode': command('xcodebuild', '-version'),
        'sdk_version': command('xcrun', '--sdk', 'macosx', '--show-sdk-version'),
        'sdk_build': command('xcrun', '--sdk', 'macosx', '--show-sdk-build-version'),
        'sdk_path': command('xcrun', '--sdk', 'macosx', '--show-sdk-path'),
        'metal': metal_identity(metal, command(metal, '--version'), command('xcrun', '--find', 'metallib')),
        'python': command('python3', '--version'),
        'args_sha256': digest(root / 'build/src/out/Default/args.gn'),
        'clang_sha256': digest(root / 'build/src/third_party/llvm-build/Release+Asserts/bin/clang'),
        'gn_sha256': digest(root / 'build/src/buildtools/mac/gn'),
        'siso_sha256': digest(root / 'build/src/third_party/siso/cipd/siso'),
        'targets': ['chrome', 'chromedriver', 'chrome/installer/mac'],
        'compile_jobs': 2, 'link_jobs': 1,
    }


def build_tree_digest(tree):
    entries = []
    for entry in tree.split(b'\0'):
        if not entry:
            continue
        _, name = entry.split(b'\t', 1)
        if name.decode() not in RECOVERY_CI_PATHS:
            entries.append(entry)
    return hashlib.sha256(b'\0'.join(entries) + b'\0').hexdigest()


def audit_packaging(root):
    tree = subprocess.check_output(['git', '-C', str(root), 'ls-tree', '-rz', 'HEAD'])
    require(build_tree_digest(tree) == LEGACY_BUILD_TREE_SHA256,
            'Packaging build inputs differ from the exact legacy commit')


def is_legacy_request():
    return os.environ.get('RECOVER_PHASE_ONE') == 'true'


def checkpoint_gate(root):
    """Run before extraction. No free-form cache key or digest input is accepted."""
    key = os.environ.get('RESTORE_KEY', '')
    if not is_legacy_request():
        run = os.environ.get('GITHUB_RUN_ID', '')
        require(re.fullmatch(r'[1-9][0-9]*', run) is not None, 'Invalid current run ID')
        require(re.fullmatch(r'helium-preview-' + run + r'-(?:[1-9]|10)-[1-9][0-9]*', key) is not None,
                'Normal restore must use this run\'s exact checkpoint')
        return
    require(key == LEGACY_KEY and os.environ.get('PHASE') == '1', 'Legacy recovery key/phase mismatch')
    require(os.environ.get('SOURCE_SHA') == LEGACY_SOURCE, 'Legacy recovery source mismatch')
    checkpoint = Path(root) / 'preview-checkpoint'
    require(digest(checkpoint / 'build.tar.zst') == LEGACY_ARCHIVE_SHA256, 'Legacy archive hash mismatch')
    require(digest(checkpoint / 'provenance.json') == LEGACY_PROVENANCE_SHA256, 'Legacy provenance hash mismatch')
    audit_packaging(root)


def verify_metadata(saved, current, legacy=False):
    require(current.get('schema') == 2, 'Current provenance schema must be 2')
    require(set(current.get('metal', {})) == {'path', 'version', 'sha256', 'metallib_sha256'}, 'Invalid Metal identity fields')
    require(all(re.fullmatch(r'[0-9a-f]{64}', current['metal'][key]) for key in ['sha256', 'metallib_sha256']), 'Invalid Metal executable hash')
    require(bool(current['metal']['version']), 'Missing Metal version')
    if not legacy:
        differences = sorted(key for key in saved.keys() | current.keys() if saved.get(key) != current.get(key))
        require(saved == current, 'Source/configuration/toolchain provenance changed: ' + ', '.join(differences))
        return
    require(saved.get('schema') == 1, 'Legacy provenance schema mismatch')
    require(saved.get('source_sha') == LEGACY_SOURCE and saved.get('packaging_sha') == LEGACY_PACKAGING,
            'Legacy source/packaging identity mismatch')
    require(saved.get('metal') == LEGACY_METAL, 'Legacy Metal path mismatch')
    require(stable_metal_path(saved['metal']) == current['metal']['path'], 'Legacy Metal toolchain version/path changed')
    # Only these three audited fields migrate. Unknown/missing or changed fields
    # anywhere else fail equality, including SDK path, Python, GN and clang hashes.
    expected = dict(saved, schema=2, packaging_sha=current['packaging_sha'], metal=current['metal'])
    differences = sorted(key for key in expected.keys() | current.keys() if expected.get(key) != current.get(key))
    require(expected == current, 'Legacy metadata differs beyond the audited migration: ' + ', '.join(differences))


def verify(root):
    root = Path(root)
    saved = json.loads((root / 'preview-checkpoint/provenance.json').read_text())
    current = json.loads((root / 'preview-logs/provenance-current.json').read_text())
    legacy = is_legacy_request()
    verify_metadata(saved, current, legacy)
    if legacy:
        report = {
            'checkpoint_key': LEGACY_KEY, 'archive_sha256': LEGACY_ARCHIVE_SHA256,
            'legacy_provenance_sha256': LEGACY_PROVENANCE_SHA256,
            'legacy_packaging_sha': LEGACY_PACKAGING,
            'new_packaging_sha': current['packaging_sha'],
            'unchanged_build_tree_sha256': LEGACY_BUILD_TREE_SHA256,
            'metal_historical_evidence': 'Legacy recorded exact toolchain version/path, not executable bytes. Only the known mount nonce is normalized; all other legacy metadata is identical. Metal and metallib hashes plus version output are enforced from schema 2 onward. The three declared ANGLE Metal generated outputs are invalidated for rebuilding.',
        }
        (root / 'preview-logs/recovery-origin.json').write_text(json.dumps(report, sort_keys=True, indent=2) + '\n')
        print(json.dumps(report, sort_keys=True, indent=2))


def check_graph(root):
    """Fail if regenerated executable build graph still refers to a stale mount."""
    root = Path(root)
    metal = command('xcrun', '--find', 'metal')
    stable_metal_path(metal)  # Require the same narrowly recognized layout.
    current_mount = str(Path(metal).parents[3]).encode()
    for path in (root / 'build/src/out/Default').rglob('*'):
        if path.is_file() and (path.suffix in {'.ninja', '.rsp', '.gn', '.star'} or path.name == 'build.ninja.d'):
            for mount in MOUNT_IN_GRAPH.findall(path.read_bytes()):
                require(mount == current_mount, f'Stale Metal mount remains in build graph: {path}')


def invalidate_legacy_metal(root):
    require(is_legacy_request(), 'Metal invalidation is only for the pinned migration')
    root = Path(root)
    gn = root / 'build/src/buildtools/mac/gn'
    value = subprocess.check_output([str(gn), 'args', str(root / 'build/src/out/Default'),
                                     '--list=angle_metal_toolchain_dir', '--short'],
                                    cwd=root / 'build/src', text=True)
    print(f'GN angle_metal_toolchain_dir raw: {value!r}', flush=True)
    require(value.strip() == 'angle_metal_toolchain_dir = ""', 'Legacy recovery requires the default Metal toolchain')
    # Chromium declares use_system_xcode="" then computes its effective boolean
    # outside declare_args. `gn args --list` therefore cannot prove that value.
    # Inspect the real generated actions instead of guessing from an argument.
    target_root = '//third_party/angle/src/libANGLE/renderer/metal'
    wrapper = target_root + '/shaders/metal_wrapper.py'
    actions = [('angle_metal_internal_shaders_to_air', 'metal', 'mtl_internal_shaders_autogen.air'),
               ('angle_metal_internal_shaders_to_mtllib', 'metallib', 'mtl_internal_shaders_autogen.metallib')]
    for target, tool, output in actions:
        label = target_root + ':' + target
        raw = subprocess.check_output([str(gn), 'desc', str(root / 'build/src/out/Default'),
                                       label, '--format=json'], cwd=root / 'build/src', text=True)
        print(f'GN desc {label} raw: {raw!r}', flush=True)
        description = json.loads(raw)
        require(set(description) == {label}, 'Unexpected Metal action target description')
        action = description[label]
        require(action.get('type') == 'action' and action.get('script') == wrapper,
                'Unexpected Metal action type/wrapper')
        require(isinstance(action.get('args'), list) and action['args'][:2] == ['xcrun', tool],
                'Generated Metal action does not use the verified system xcrun route')
        require(action.get('outputs') == ['//out/Default/gen/angle/' + output],
                'Unexpected generated Metal action output')
    folder = root / 'build/src/out/Default/gen/angle'
    for name in ['mtl_internal_shaders_autogen.air', 'mtl_internal_shaders_autogen.metallib',
                 'mtl_internal_shaders_metallib.h']:
        path = folder / name
        if path.exists() or path.is_symlink():
            path.unlink()
            print(f'Invalidated legacy generated Metal output: {path}')


if __name__ == '__main__':
    action, root = sys.argv[1:]
    if action == 'record':
        print(json.dumps(record(root), sort_keys=True, indent=2))
    elif action == 'checkpoint-gate':
        checkpoint_gate(root)
    elif action == 'verify':
        verify(root)
    elif action == 'check-graph':
        check_graph(root)
    elif action == 'invalidate-legacy-metal':
        invalidate_legacy_metal(root)
    else:
        raise SystemExit('Unknown provenance action')
