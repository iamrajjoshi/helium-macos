#!/usr/bin/env python3
"""Strict checkpoint identity plus one explicitly pinned schema-2 bridge."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

BRIDGE_KEY = 'helium-preview-40051201468-1-1'
BRIDGE_SOURCE = '0b1af54b49c343484035c0977a85c4cc8642595a'
BRIDGE_PACKAGING = '1f1cc956e4c094f43dad5f3b1fd6e211ca3cb004'
BRIDGE_ARCHIVE_SHA256 = 'b2169963676226e1580379471ffee31b0ad531d26f4a4eb72e615e0e7c38608c'
BRIDGE_PROVENANCE_SHA256 = '0b85d0d5f225daaae3744c750eebbc426ee278a76c8f519f20b308c4975e5c99'
BRIDGE_ORIGIN_SHA256 = '4458ead84e320cef30263461afcd18c3be72fba0835e33fa39f303dbdd842d24'
BRIDGE_METAL_RAW_PATH = '/var/run/com.apple.security.cryptexd/mnt/com.apple.MobileAsset.MetalToolchain-v17.6.42.0.f8TSG3/Metal.xctoolchain/usr/bin/metal'
BRIDGE_METAL_RAW_VERSION = 'Apple metal version 32023.883 (metalfe-32023.883)\nTarget: air64-apple-darwin25.6.0\nThread model: posix\nInstalledDir: /private/var/run/com.apple.security.cryptexd/mnt/com.apple.MobileAsset.MetalToolchain-v17.6.42.0.f8TSG3/Metal.xctoolchain/usr/metal/current/bin'
# Every other Git tree entry (including executable modes and submodule pointers)
# must equal the audited saved packaging commit. These are CI-only changes.
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
BRIDGE_BUILD_TREE_SHA256 = 'af6bed91b8c6bffcea50a4d10768b23f34e9a518ec2955f9094fc3db51979fa9'
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


def stable_metal_version(path, version):
    stable = stable_metal_path(path)
    require(bool(version), 'Empty Metal version output')
    # The actual Metal version reports a /private/var alias and a different
    # internal bin directory. Normalize only a recognized InstalledDir line
    # using this xcrun path's exact version AND nonce. All other text survives.
    actual_bin = str(Path(path).parent)
    stable_bin = str(Path(stable).parent)
    allowed = {
        actual_bin: stable_bin,
        '/private' + actual_bin.removesuffix('/usr/bin') + '/usr/metal/current/bin':
            '/private' + stable_bin.removesuffix('/usr/bin') + '/usr/metal/current/bin',
    }
    lines = version.split('\n')
    installed = [index for index, line in enumerate(lines) if line.startswith('InstalledDir:')]
    require(len(installed) <= 1, 'Repeated Metal InstalledDir lines')
    for index in installed:
        line = lines[index]
        require(line.startswith('InstalledDir: '), 'Unexpected Metal InstalledDir format')
        directory = line.removeprefix('InstalledDir: ')
        require(directory in allowed, 'Metal InstalledDir differs from the verified xcrun toolchain')
        lines[index] = 'InstalledDir: ' + allowed[directory]
    return '\n'.join(lines)


def metal_identity(path, version, metallib):
    stable = stable_metal_path(path)
    version = stable_metal_version(path, version)
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
    require(build_tree_digest(tree) == BRIDGE_BUILD_TREE_SHA256,
            'Packaging build inputs differ from the exact saved commit')


def is_bridge_request():
    return os.environ.get('RECOVER_PHASE_ONE') == 'true'


def checkpoint_gate(root):
    """Run before extraction. No free-form cache key or digest input is accepted."""
    key = os.environ.get('RESTORE_KEY', '')
    if not is_bridge_request():
        run = os.environ.get('GITHUB_RUN_ID', '')
        require(re.fullmatch(r'[1-9][0-9]*', run) is not None, 'Invalid current run ID')
        require(re.fullmatch(r'helium-preview-' + run + r'-(?:[1-9]|10)-[1-9][0-9]*', key) is not None,
                'Normal restore must use this run\'s exact checkpoint')
        return
    require(key == BRIDGE_KEY and os.environ.get('PHASE') == '1', 'Checkpoint bridge key/phase mismatch')
    require(os.environ.get('SOURCE_SHA') == BRIDGE_SOURCE, 'Checkpoint bridge source mismatch')
    checkpoint = Path(root) / 'preview-checkpoint'
    require(digest(checkpoint / 'build.tar.zst') == BRIDGE_ARCHIVE_SHA256, 'Bridge archive hash mismatch')
    require(digest(checkpoint / 'provenance.json') == BRIDGE_PROVENANCE_SHA256, 'Bridge provenance hash mismatch')
    require(digest(checkpoint / 'recovery-origin.json') == BRIDGE_ORIGIN_SHA256, 'Bridge prior recovery-origin hash mismatch')
    audit_packaging(root)


def verify_metadata(saved, current, bridge=False):
    require(current.get('schema') == 2, 'Current provenance schema must be 2')
    require(set(current.get('metal', {})) == {'path', 'version', 'sha256', 'metallib_sha256'}, 'Invalid Metal identity fields')
    require(all(re.fullmatch(r'[0-9a-f]{64}', current['metal'][key]) for key in ['sha256', 'metallib_sha256']), 'Invalid Metal executable hash')
    require(bool(current['metal']['version']), 'Missing Metal version')
    expected = saved
    if bridge:
        require(saved.get('schema') == 2, 'Bridge requires saved schema-2 provenance')
        require(saved.get('source_sha') == BRIDGE_SOURCE and saved.get('packaging_sha') == BRIDGE_PACKAGING,
                'Bridge source/packaging identity mismatch')
        require(saved['metal']['version'] == BRIDGE_METAL_RAW_VERSION,
                'Bridge saved Metal version differs from the exact recorded value')
        require(saved['metal']['path'] == stable_metal_path(BRIDGE_METAL_RAW_PATH),
                'Bridge saved Metal path mismatch')
        # Only the independently audited CI-helper packaging commit and this
        # exact saved InstalledDir mount normalization can differ.
        # In particular, Metal hashes/version, SDK paths and every unknown field
        # retain exact equality. No schema-1 migration or tool-output deletion.
        metal = dict(saved['metal'], version=stable_metal_version(BRIDGE_METAL_RAW_PATH, saved['metal']['version']))
        expected = dict(saved, packaging_sha=current['packaging_sha'], metal=metal)
    differences = sorted(key for key in expected.keys() | current.keys() if expected.get(key) != current.get(key))
    require(expected == current, 'Source/configuration/toolchain provenance changed: ' + ', '.join(differences))


def verify(root):
    root = Path(root)
    saved = json.loads((root / 'preview-checkpoint/provenance.json').read_text())
    current = json.loads((root / 'preview-logs/provenance-current.json').read_text())
    bridge = is_bridge_request()
    verify_metadata(saved, current, bridge)
    if bridge:
        prior_origin = root / 'preview-checkpoint/recovery-origin.json'
        require(digest(prior_origin) == BRIDGE_ORIGIN_SHA256, 'Bridge prior recovery-origin hash mismatch')
        report = {
            'checkpoint_key': BRIDGE_KEY, 'archive_sha256': BRIDGE_ARCHIVE_SHA256,
            'saved_provenance_sha256': BRIDGE_PROVENANCE_SHA256,
            'saved_packaging_sha': BRIDGE_PACKAGING,
            'prior_recovery_origin_sha256': BRIDGE_ORIGIN_SHA256,
            'new_packaging_sha': current['packaging_sha'],
            'unchanged_build_tree_sha256': BRIDGE_BUILD_TREE_SHA256,
            'prior_recovery_origin': json.loads(prior_origin.read_text()),
            'bridge_scope': 'Saved and current schema-2 metadata match exactly except the audited CI-only packaging commit and the exact recorded Metal InstalledDir mount nonce normalization. Metal version/target/thread text and both executable hashes remain exact. No object output is deleted. The saved restricted-pax archive may cause rebuilds; successors use full pax to retain nanosecond mtimes.',
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
    else:
        raise SystemExit('Unknown provenance action')
