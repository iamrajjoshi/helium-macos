#!/usr/bin/env bash
# Personal-fork standard ARM64 runner build. Never changes host security settings.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
export DEVELOPER_DIR=/Applications/Xcode_26.5.app/Contents/Developer
export HELIUM_APP_NAME="Helium Arc Preview"
export SCCACHE_DISABLE=1
export SISO_REAPI_ADDRESS=""
export SISO_REAPI_INSTANCE=""
LOG="$ROOT/preview-logs"
mkdir -p "$LOG"

fail() { echo "::error::$*" >&2; exit 1; }
validate_inputs() {
  [[ "${SOURCE_SHA:-}" =~ ^[0-9a-f]{40}$ ]] || fail 'source_sha must be a lowercase full 40-character commit SHA.'
  [[ "${GITHUB_REPOSITORY:-}" == iamrajjoshi/helium-macos ]] || fail 'Only the personal packaging fork is allowed.'
  [[ "$(git rev-parse HEAD)" == "$GITHUB_SHA" ]] || fail 'Packaging checkout does not match workflow SHA.'
  [[ "$(git -C helium-chromium rev-parse HEAD)" == "$SOURCE_SHA" ]] || fail 'Source checkout is not the requested SHA.'
  [[ "$(uname -m)" == arm64 ]] || fail 'This build requires the standard ARM64 runner.'
  test -d "$DEVELOPER_DIR" || fail 'Required Xcode 26.5 is missing.'
  xcrun --find metal
}
record_provenance() {
  python3 "$ROOT/.github/scripts/mac_preview_provenance.py" record "$ROOT"
}

case "${1:-}" in
  setup)
    validate_inputs
    date +%s > "$LOG/job-start.txt"
    brew install coreutils zstd
    python3 -m pip install requests pillow
    {
      sw_vers; sysctl hw.memsize hw.logicalcpu; df -h .
      xcodebuild -version; xcrun --sdk macosx --show-sdk-version
      echo "Image: ${ImageOS:-unknown} ${ImageVersion:-unknown}"
      python3 --version; python3 -m pip freeze
    } | tee "$LOG/environment.txt"
    free_kib=$(df -k . | awk 'END {print $4}')
    (( free_kib >= 50 * 1024 * 1024 )) || fail 'Less than 50 GiB free disk before checkout restoration.'
    ;;
  prepare)
    source "$ROOT/devutils/shared.sh"
    echo '[preview:prepare] Prepare pinned source tree'
    prepare_sources 2>&1 | tee "$LOG/prepare.log"
    # Only task-owned downloaded archives are removed; installed host tools are untouched.
    rm -rf "$ROOT/build/download_cache"
    write_gn_args arm64 ci
    cat >> "$_out_dir/args.gn" <<'GN'
# Lower peak memory without changing sandbox, CFI, runtime security, or official mode.
cc_wrapper=""
symbol_level=0
blink_symbol_level=0
v8_symbol_level=0
use_thin_lto=false
chrome_pgo_phase=0
concurrent_links=1
enable_updater=false
enable_sparkle=false
GN
    echo '[preview:prepare] Generate build graph'
    gn_gen 2>&1 | tee "$LOG/gn.log"
    echo '[preview:prepare] Resolve GN arguments from the Chromium source root'
    # gn_gen's cd occurs in a pipeline subshell; it cannot set this shell's cwd.
    (
      cd "$_src_dir"
      "$_gn_path" args "$_out_dir" --list --short
    ) 2>&1 | tee "$LOG/resolved-gn-args.txt"
    echo '[preview:prepare] Record source/configuration/toolchain provenance'
    record_provenance > "$LOG/provenance.json"
    cp "$_out_dir/args.gn" "$LOG/args.gn"
    echo '[preview:prepare] Preparation complete'
    ;;
  restore)
    # Exact same-run cache, or the single explicitly pinned schema-2 bridge.
    # Verify the independent pinned checkpoint hashes before extracting any data.
    python3 .github/scripts/mac_preview_provenance.py checkpoint-gate "$ROOT"
    (cd preview-checkpoint && shasum -a 256 -c SHA256SUMS)
    test ! -e build/src || fail 'Refusing to extract over an existing source tree.'
    mkdir -p build
    zstd -dc preview-checkpoint/build.tar.zst | tar -xf - -C build
    record_provenance > "$LOG/provenance-current.json"
    python3 .github/scripts/mac_preview_provenance.py verify "$ROOT"
    if [[ -f preview-checkpoint/recovery-origin.json && "${RECOVER_PHASE_ONE:-false}" != true ]]; then
      cp preview-checkpoint/recovery-origin.json "$LOG/recovery-origin.json"
    fi
    # Refresh generated host/SDK paths without cleaning objects or changing args.
    source "$ROOT/devutils/shared.sh"
    gn_gen 2>&1 | tee "$LOG/gn-restore.log"
    python3 .github/scripts/mac_preview_provenance.py check-graph "$ROOT"
    record_provenance > "$LOG/provenance.json"
    cmp "$LOG/provenance-current.json" "$LOG/provenance.json" || fail 'Configuration/toolchain changed while refreshing restored graph.'
    cp build/src/out/Default/args.gn "$LOG/args.gn"
    # Keep only metadata after successful validation so packing the successor fits disk.
    rm preview-checkpoint/build.tar.zst
    ;;
  build)
    source "$ROOT/devutils/shared.sh"
    start=$(cat "$LOG/job-start.txt")
    # 4.5 h from environment setup, leaving 1.5 h for hashing, compression, upload.
    remaining=$((270 * 60 - $(date +%s) + start))
    (( remaining >= 60 )) || fail 'Setup consumed the compile window.'
    status=0
    gtimeout -k 2m -s TERM "${remaining}s" \
      bash "$ROOT/devutils/shared.sh" build -j 2 chrome/installer/mac \
      > >(tee "$LOG/build.log") 2>&1 || status=$?
    if [[ "$status" == 124 ]]; then
      echo 'status=running' >> "$GITHUB_OUTPUT"
    elif [[ "$status" == 0 ]]; then
      for binary in chromedriver; do
        test -x "$_out_dir/$binary" || fail "Missing completed target: $binary"
      done
      test -x "$_out_dir/$HELIUM_APP_NAME.app/Contents/MacOS/$HELIUM_APP_NAME" || fail 'Helium Arc Preview.app executable is missing.'
      echo 'status=built' >> "$GITHUB_OUTPUT"
    else
      fail "Compilation failed with exit code $status; this is not a resumable timeout."
    fi
    ;;
  checkpoint)
    mkdir -p preview-checkpoint
    cp "$LOG/provenance.json" preview-checkpoint/
    # zstd -3 / two threads is intentionally lower-memory than upstream -11 / all cores.
    # BSD tar's default restricted pax drops nanosecond mtimes for ordinary
    # entries. Siso records those timestamps and can invalidate unchanged
    # objects after restore. Full pax is required, not just a content hash.
    tar --format pax -C build -cf - src | zstd -3 -T2 -o preview-checkpoint/build.tar.zst
    python3 - <<'PY'
from pathlib import Path
p = Path('preview-checkpoint/build.tar.zst')
assert 0 < p.stat().st_size <= 9*1024**3, 'Checkpoint exceeds the 9 GiB free-cache safety cap; do not upload.'
PY
    if [[ -f "$LOG/recovery-origin.json" ]]; then
      cp "$LOG/recovery-origin.json" preview-checkpoint/recovery-origin.json
    fi
    (
      cd preview-checkpoint
      files=(build.tar.zst provenance.json)
      if [[ -f recovery-origin.json ]]; then files+=(recovery-origin.json); fi
      shasum -a 256 "${files[@]}" > SHA256SUMS
    )
    cat preview-checkpoint/SHA256SUMS
    ;;
  test)
    # Chromium test launchers create their own per-test profiles. TMPDIR is task-only.
    export TMPDIR
    TMPDIR=$(mktemp -d "${RUNNER_TEMP:?}/helium-native-tests.XXXXXX")
    trap 'rm -rf "$TMPDIR"' EXIT
    python3 .github/scripts/mac_preview_tests.py native 2>&1 | tee "$LOG/native-tests.log"
    ;;
  package)
    mkdir -p preview-dist
    export OUT_DMG_PATH="$ROOT/preview-dist/helium-arc-preview-${SOURCE_SHA:0:12}-arm64.dmg"
    unset MACOS_CERTIFICATE_NAME PROD_MACOS_SPECIAL_ENTITLEMENTS_PROFILE_PATH
    (
      cd build/src
      "$ROOT/sign_and_package_app.sh"
    ) 2>&1 | tee "$LOG/package.log"
    test -s "$OUT_DMG_PATH" || fail 'Packaging reported success but no nonempty DMG exists.'
    hdiutil verify "$OUT_DMG_PATH" | tee "$LOG/dmg-verify.txt"
    mountpoint=$(mktemp -d "${RUNNER_TEMP:?}/helium-dmg.XXXXXX")
    trap 'hdiutil detach "$mountpoint" || true; rmdir "$mountpoint" || true' EXIT
    hdiutil attach -readonly -nobrowse -mountpoint "$mountpoint" "$OUT_DMG_PATH"
    app="$mountpoint/$HELIUM_APP_NAME.app"
    test -x "$app/Contents/MacOS/$HELIUM_APP_NAME"
    python3 - "$app/Contents/Info.plist" <<'PYIDENTITY'
import plistlib, sys
with open(sys.argv[1], 'rb') as f:
    info = plistlib.load(f)
assert info['CFBundleIdentifier'] == 'io.github.iamrajjoshi.helium-arc-preview'
assert info['CFBundleExecutable'] == 'Helium Arc Preview'
assert info['CFBundleDisplayName'] == 'Helium Arc Preview'
assert info['CrProductDirName'] == 'io.github.iamrajjoshi.helium-arc-preview'
assert not info.get('SUFeedURL'), 'Official update feed must not be present'
print('Packaged app identity and isolated default data directory verified.')
PYIDENTITY
    codesign --verify --deep --strict --verbose=2 "$app" 2>&1 | tee "$LOG/signature-verify.txt"
    codesign -d --verbose=4 "$app" 2>&1 | tee "$LOG/signature-details.txt"
    lipo -archs "$app/Contents/MacOS/$HELIUM_APP_NAME" | tee "$LOG/app-architecture.txt"
    [[ "$(lipo -archs "$app/Contents/MacOS/$HELIUM_APP_NAME")" == arm64 ]] || fail 'Wrong application architecture.'
    python3 .github/scripts/mac_preview_tests.py smoke "$app/Contents/MacOS/$HELIUM_APP_NAME" \
      2>&1 | tee "$LOG/packaged-smoke.log"
    cp "$LOG/provenance.json" preview-dist/provenance.json
    python3 - <<'PYNATIVE'
import json
from pathlib import Path
report = {
    'status': 'UNRUN',
    'reason': 'The prepared release archive omits broad native-test fixture trees. This initial build compiles browser/driver/installer, not browser_tests or unit_tests.',
    'browser_suites': ['ProfileMenuViewWorkspaceTest.*', 'LittleArcBrowserTest.*', 'LittleArcDisabledBrowserTest.*', 'AppControllerLittleArcBrowserTest.*', 'AppControllerLittleArcStartupBrowserTest.*'],
    'unit_suites': ['GitHubLiveFolderControllerTest.*', 'HeliumArcPreviewIdentity.*'],
    'required_next': 'Complete pinned test sources and compile/run the authored native tests; inspect and exercise native feature UI before any readiness claim.',
}
Path('preview-dist/native-test-status.json').write_text(json.dumps(report, indent=2) + '\n')
PYNATIVE
    cp "$LOG/packaged-smoke.json" preview-dist/packaged-smoke.json
    # Debug evidence only, no actual user profiles or credentials. Build-phase
    # console logs stay in Actions; this small archive covers final validation.
    tar -czf preview-dist/validation-evidence.tar.gz -C "$ROOT" preview-logs
    python3 - <<'PYLOG'
from pathlib import Path
assert Path('preview-dist/validation-evidence.tar.gz').stat().st_size <= 20*1024**2, 'Validation evidence exceeds the 20 MiB cap.'
PYLOG
    cat > preview-dist/README.txt <<NOTICE
Personal ARM64 Helium Arc-style preview; draft, not approved for daily use.
Source: https://github.com/iamrajjoshi/helium/commit/$SOURCE_SHA
Packaging: https://github.com/iamrajjoshi/helium-macos/commit/$GITHUB_SHA
Run: https://github.com/$GITHUB_REPOSITORY/actions/runs/$GITHUB_RUN_ID
Ad-hoc signed, NOT Developer-ID signed or notarized. No Gatekeeper bypass is provided.
The app name, bundle ID, default data/cache directory and Keychain are isolated from Helium.
Use only disposable profiles. Profile switching and Little Arc remain opt-in flags.
Packaged application GUI smoke passed. Authored native browser/component tests
are UNRUN; native menu/promotion/switching/live-folder visual and interrupted-flow
QA remains required before an install-ready claim.
NOTICE
    (cd preview-dist && shasum -a 256 ./*.dmg provenance.json native-test-status.json packaged-smoke.json README.txt validation-evidence.tar.gz > SHA256SUMS)
    python3 - <<'PY'
from pathlib import Path
files = list(Path('preview-dist').iterdir())
assert len(list(Path('preview-dist').glob('*.dmg'))) == 1
assert all(0 < f.stat().st_size < 2*1024**3 for f in files), 'Every release asset must be nonempty and smaller than 2 GiB.'
assert sum(f.stat().st_size for f in files) <= 2*1024**3, 'Total draft assets exceed the task 2 GiB cap.'
PY
    cat preview-dist/SHA256SUMS
    ;;
  summary)
    {
      echo '## Personal ARM64 preview'
      echo "Source: $SOURCE_SHA"
      echo "Packaging: $GITHUB_SHA"
      echo "Phase: ${PHASE:-validation}"
      echo 'Standard macos-26 only. No signing secrets, billing changes, paid runner, or Actions artifact uploads.'
      echo 'See full step console logs for all commands, compile errors, and test output.'
      if test -f "$LOG/provenance.json"; then cat "$LOG/provenance.json"; fi
      if test -f preview-dist/native-test-status.json; then cat preview-dist/native-test-status.json; fi
      if test -f preview-dist/SHA256SUMS; then cat preview-dist/SHA256SUMS; fi
    } >> "$GITHUB_STEP_SUMMARY"
    df -h .
    ;;
  *) fail 'Usage: mac_preview.sh setup|prepare|restore|build|checkpoint|test|package|summary' ;;
esac
