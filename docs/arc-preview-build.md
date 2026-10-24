# Isolated personal Mac preview candidate

This is a workflow candidate, not evidence that a Chromium compile or native
feature test has passed. Use only `iamrajjoshi/helium-macos`, with reviewed source
`0b1af54b49c343484035c0977a85c4cc8642595a` in `iamrajjoshi/helium` as the required
`source_sha` dispatch input. There is no moving branch/tag default.

## Initial build and verification boundary

The initial capacity attempt uses the upstream prepared Chromium archive and
normal release preparation. It compiles the complete **chrome, chromedriver and
Mac installer**, not a short sanity/lint build. This gets a real app candidate
without a much larger full Chromium checkout on the 7 GiB standard runner.

**Authored native browser_tests and unit_tests are explicitly UNRUN.** The
prepared archives remove broad native-test fixture trees before download, and
the ordinary platform release pruning removes them again. Simply preserving a
single title1.html fixture, or silently running zero tests, would not fix this.
A `native-test-status.json` asset records UNRUN and the missing coverage.

After a successful complete compile, the workflow packages a DMG, verifies its
integrity and ad-hoc signature, mounts it read-only, checks ARM64 and the actual
app's Info.plist identity, and launches that mounted app through GUI ChromeDriver.
The deterministic local HTTP smoke checks:

- Browser launch, navigation, scripts and editable-input state.
- Back/forward navigation.
- Cookies/localStorage separation between two temporary data directories.
- The first temporary profile's state after quit/relaunch.
- A real macOS URL event using `open -a` and the exact mounted app path, without
  changing the default handler or using AppleScript/TCC permissions.
- A new roughly 720x640 Little Arc window at the expected fixture URL, its
  same-profile storage, closing it, and preserving the original browser window.

The OS-event command also supplies the same temporary data path in `--args`,
so a Launch Services cold-start fallback cannot touch a normal profile. The
feature opt-in flags are explicit. The mock Keychain is used during smoke,
not the user's real Keychain. All web fixtures are loopback-only. The browser
sandbox is not disabled. Page and native macOS desktop screenshots are saved.

This is **not full native interactive E2E**. In particular, the real profile
menu switching, Little Arc promotion shortcut/menu, and Live Folder native
right-click flow are not exercised by this initial smoke. Cold-start/multiple
external URL events, fullscreen/Spaces, rapid switching, unsaved-form prompts,
concurrent/pending refresh, offline recovery, group moves, restore/relaunch,
and all three features together still require validation. Do not claim all
three features are fully tested, or publish/install-ready status, from a green
workflow alone. A GUI capability/permission failure is a blocker, never a reason
to bypass security or treat headless page rendering as native UI verification.

## Safe compiled identity

The final platform patch changes the compiled product to **Helium Arc
Preview.app**, bundle ID `io.github.iamrajjoshi.helium-arc-preview`, with the same
separate directory under `~/Library/Application Support` and `~/Library/Caches`.
It uses a separate Keychain service/account and removes the upstream developer
team ID. This is not an after-build app rename and does not rely on the user
remembering a CLI flag. Info.plist identity is verified before upload. The
native default-data/cache-path unit test is authored but, like the other native
unit tests, remains UNRUN in this initial build.

The preview returns before first-run automatic import and unattended
default-browser takeover. It preserves the AUTO_IMPORT_CALLED diagnostic marker;
manual user-initiated import remains available. Both Chromium updater and
Sparkle are disabled. No ordinary browser data is imported, no default-handler
setting is changed, and no signing/notarization credential is requested.

Compile mode remains `is_official_build=true`, `is_debug=false`. Symbols are
zero, ThinLTO and PGO are off, compile parallelism is two and link parallelism
one. The pinned CFI default does not enable CFI on macOS; no CFI/security flag is
overridden. Chromium's assertion that CFI requires ThinLTO remains active. The
existing Helium security configuration and runtime sandbox are not weakened.

## Time, checkpoint and cost boundaries

Only the fixed standard `macos-26` ARM64 label in this public personal fork is
accepted. No configurable runner, Depot, remote/larger/self-hosted build,
billing change, personal secret, paid cache expansion or Actions artifact upload
exists in this workflow.

Ten serial phases each stop compiling at 4.5 hours from setup, reserving up to
90 minutes of the hosted six-hour limit for checkpoint hashing and transfer.
The total compile allowance is therefore at most approximately 45 hours, less
setup overhead: a capacity attempt, not a completion promise. Real compile
errors fail. Exhausting every phase without a completed browser is a failure,
never a green workflow without a DMG.

Checkpoints use the Actions build-output cache. GitHub includes a separate
10 GiB per-repository cache allowance on every plan (binary GiB, not decimal
GB). The workflow enforces that **fixed 10 GiB aggregate ceiling**, without
reading or changing the configured storage limit. Every unfiltered inventory
page must have valid counts, unique IDs, keys and nonnegative integer sizes;
denied, malformed, incomplete or changing inventories fail closed. Existing
total usage must already fit the ceiling. Compressed checkpoints are <=9 GiB;
unrelated usage plus checkpoint plus a 128 MiB envelope reserve must fit.
Unrelated caches are never deleted.
Only this run's consumed predecessor is deleted, after a successor archive is
complete and hashed locally, immediately before saving the successor. This
prevents two multi-GB copies accumulating, but means a failed successor upload
can force rebuilding from scratch. Cache eviction can also prevent resume.
Unrelated inventory must be unchanged after deletion; total usage is checked again after
saving. The preview workflow is serialized, but API checks are not an atomic
storage reservation: another workflow writing caches concurrently can race
the guard. An overshoot is reported as failure, not claimed to be retroactively
free. Do not run other cache-producing workflows concurrently with this build.

Restore requires an exact cache hit, SHA-256 validation, and matching source
and packaging commits, GN args hash, Xcode/SDK, Python, clang/GN/Siso hashes,
architecture and targets. Schema 2 records the Metal and metallib executable
SHA-256 hashes and Metal version output. Only the six-character randomized
cryptex mount suffix is normalized; toolchain version and all other path parts
remain exact. A changed toolchain deliberately blocks reuse. Saving is confirmed through the API, not inferred from action success.

Checkpoint creation explicitly uses **full pax** (`tar --format pax`) to preserve
nanosecond modification times as well as bytes, modes, symlinks and hidden Siso
state. BSD tar's default restricted-pax format can round ordinary entries to
whole seconds. The pinned Siso stores nanosecond timestamps; after restoration,
it can discard output command records and rebuild unchanged objects. This was
reproduced with the real pinned GN and Siso: restricted-pax restored builds
repeated actions both with and without an explicit GN regeneration, while
full-pax restored builds ran zero actions in both cases. Skipping regeneration
is therefore not a fix, and host-path refresh and strict provenance stay enabled.

Older restricted-pax checkpoints still validate their content hashes, but are
**not proven to retain incremental reuse**. Merely saving their successor in
full pax cannot restore timestamp precision already lost in an older archive;
an old-checkpoint bridge can incur another rebuild. The cross-run bridge must
pin the exact newly observed cache key, archive/provenance hashes and packaging
commit after a verified save, allow only reviewed helper changes and the exact InstalledDir normalization
documented below, and preserve all other schema-2 metadata exactly. No arbitrary checkpoint input is permitted.
No running job is changed by editing this repository. Do not cancel a phase or
claim its successor is protected until its cache save and the reviewed bridge
are both verified.

Successful final draft upload removes this run's cache. Failed runs retain at
most their bounded checkpoint subject to ordinary cache retention/eviction
(normally seven inactive days). No per-entry one-day retention input exists in
the pinned cache action, so repository-wide retention settings are not changed.
Every phase keeps its full Actions console logs and always adds a summary.

The verified DMG, SHA-256 sums, provenance, UNRUN native status, actual packaged
smoke results and <=20 MiB validation evidence go to an **unpublished draft
release** on the user's packaging fork. Each file must be <2 GiB and combined
assets <=2 GiB. GitHub documents no total release-asset size/bandwidth limit;
these are not Actions artifacts, Packages, LFS or custom images. There is no
automatic release publication. Owner/collaborator access is needed to download
the draft. Ad-hoc signing is not Developer ID signing or notarization, and no
Gatekeeper bypass is provided.

7 GiB RAM, disk capacity, the checkpoint limit, GUI availability, or the phase
budget may still prevent completion. Nothing in source inspection establishes
a completed build or reliable installable release.

## One-time bridge of the verified schema-2 checkpoint

Leave `recover_phase_one=false` for ordinary fresh builds. The only recovery
option is `recover_phase_one=true`, with source commit
`0b1af54b49c343484035c0977a85c4cc8642595a`. It restores only
`helium-preview-40051201468-1-1`, saved and API-verified by run `40051201468`,
job `120744065130`, using packaging commit
`1f1cc956e4c094f43dad5f3b1fd6e211ca3cb004`. There is no arbitrary cache-key,
old-commit, digest, runner or toolchain override. The default-branch discovery
stub must describe the same boolean recovery input before web dispatch.

Before extraction, all three exact byte hashes must match:

- build.tar.zst: `b2169963676226e1580379471ffee31b0ad531d26f4a4eb72e615e0e7c38608c`
- provenance.json: `0b85d0d5f225daaae3744c750eebbc426ee278a76c8f519f20b308c4975e5c99`
- recovery-origin.json: `4458ead84e320cef30263461afcd18c3be72fba0835e33fa39f303dbdd842d24`

Every non-CI Git tree entry (content, mode and submodule pointer) must match the
saved packaging tree. Only the enumerated recovery helpers, workflow definitions,
regression tests and this document may differ. The saved provenance is already
schema 2: both Metal executable hashes, version, target and thread model remain
strict, as do all source/compiler/GN/Siso/SDK/args/Python/architecture fields.
The only permitted differences are the audited packaging commit and normalizing
the exact saved Metal InstalledDir mount nonce described below. Unknown, missing
or changed metadata fields fail, with differing top-level names logged.

The observed Metal version output uses
`/private/var/run/...MetalToolchain-v17.6.42.0.f8TSG3/Metal.xctoolchain/usr/metal/current/bin`,
whereas xcrun returns `/var/run/.../Metal.xctoolchain/usr/bin/metal`.
Current recording canonicalizes only an exact recognized `InstalledDir:` line
tied to xcrun's **same version and nonce**, allowing either its bin directory or
that specific observed private-path alias. Any other path/layout/nonce fails.
All other version text is retained. The bridge requires the exact saved version
string and normalizes only its known nonce; it never substitutes the current
Metal object or accepts changed executable hashes. Subsequent checkpoints use
the canonical string and strict equality. Schema-1 migration and special deletion
of Metal outputs are removed from this route.

GN still regenerates without cleaning objects, and the stale Metal mount graph
scan stays enabled. The pinned prior recovery report is embedded in the new
hashed recovery-origin.json to retain the history and its original limitation:
the first schema-1 checkpoint lacked historical Metal executable hashes.
That limitation is not repeated or broadened for this schema-2 bridge.

The saved archive used restricted pax, so one more round of unchanged work may
rebuild after this restore. No lost nanosecond timestamps are invented or
silently rewritten. Successor checkpoints use full pax; real local GN/Siso
round-trip tests demonstrate incremental reuse for the corrected format.
This is not a promise of build completion or a native feature-test result.

Only this exact saved cache is authorized as a cross-run predecessor. Prefer
retaining it until its successor is saved and verified. If both copies would
breach the fixed 10 GiB total, only that exact key may be consumed after validated
restoration and a complete, hashed local successor, immediately before upload.
The console warns that an upload failure then loses the resume point.
Invalid/missing bridge evidence or insufficient space even after that deletion
fails without touching other caches. Similar-prefix and former recovery keys
are never included. No billing setting or permission changes are made.

## Commands and later native-test route

Initial workflow compile (shared entrypoint always adds chrome/chromedriver):

```sh
bash devutils/shared.sh build -j 2 chrome/installer/mac
```

The retained `.github/scripts/mac_preview_tests.py native` runner is for a later
complete-source build, not invoked by this initial workflow. Once **all** needed
pinned upstream test sources/dependencies are restored or fetched through a
capacity-approved full gclient checkout, and release-only fixture pruning is
avoided, compile:

```sh
bash devutils/shared.sh build -j 2 chrome/installer/mac browser_tests unit_tests
python3 .github/scripts/mac_preview_tests.py native
```

That runner executes every authored case in five browser suites:
`ProfileMenuViewWorkspaceTest`, `LittleArcBrowserTest`,
`LittleArcDisabledBrowserTest`, `AppControllerLittleArcBrowserTest`, and
`AppControllerLittleArcStartupBrowserTest`; and two unit suites:
`GitHubLiveFolderControllerTest` and `HeliumArcPreviewIdentity`.
The current pinned source/platform patch set contains 12 browser and 22 unit
cases. Each runs three times, one launcher worker, zero retries. Expected cases
are extracted from the exact patches, and JSON summaries reject missing, zero,
skipped, failed or retried passes. This robust runner is **not evidence those
native tests have run**.

## Local GN and checkpoint regressions

Run all wrapper/provenance tests with `python3 -m unittest discover -s tests -p
"test_mac_preview_*.py"` and `node tests/test_mac_preview_cache.js`.
The exact saved schema-2 metadata and prior-origin fixtures reproduce their
logged SHA-256 hashes. Negative cases cover every metadata field, Metal hashes,
version text, InstalledDir layout/version/nonce, archive and origin hashes,
wrong source/key/phase, changed build inputs, and unrelated-cache preservation.

The checkpoint regression executes the production archive command and real
tar/zstd extraction, checking exact file bytes, modes, symlink target and
nanosecond mtimes, including hidden Siso state. With both
`HELIUM_TEST_GN_BINARY` (revision `510ec7992c30f172205792f190e96111a896ba8f`)
and `HELIUM_TEST_SISO_BINARY` supplied, it additionally
builds a temporary graph using actual GN and Siso, restores the production
checkpoint twice, and requires zero actions both before and after real GN
regeneration. A changed-source control must run the affected action and change
its output. Use Chromium's pinned Siso revision
`c574896b272b40aac6e44c228ee0b471ea7e3d7d` (`v1.6.3`); this is a local
incremental-build regression, not a Mac browser or native-feature test.

For an actual build, `out/Default/siso_explain` records dirty reasons even
without `-d explain`; `siso.INFO` and `siso_metrics.json` contain additional
execution details. Console `S`/`F` action lines are started/finished work, not
skipped steps. Siso subtracts skipped work from its progress counts, so those
counts alone must not be presented as proof of checkpoint reuse.

## Primary evidence

- [Upstream platform build instructions](https://github.com/imputnet/helium-macos/blob/main/docs/building.md)
- [Prepared archive filtering](https://github.com/imputnet/chromium-tarballs/blob/a74ff1b1c46185744eb73eb69e7a9b587261797c/scripts/package.py)
- [Hosted six-hour job limit](https://docs.github.com/en/actions/reference/limits)
- [Actions billing and included cache versus artifact storage](https://docs.github.com/en/billing/concepts/product-billing/github-actions)
- [Cache policy](https://docs.github.com/en/actions/reference/workflows-and-actions/dependency-caching)
- [Cache inventory API](https://docs.github.com/en/rest/actions/cache#list-github-actions-caches-for-a-repository)
- [Release-asset quotas](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases#storage-and-bandwidth-quotas)
- [Pinned ThinLTO argument](https://github.com/chromium/chromium/blob/156.0.8078.47/build/config/compiler/compiler.gni)
- [Pinned CFI defaults](https://github.com/chromium/chromium/blob/156.0.8078.47/build/config/sanitizers/sanitizers.gni)
- [Pinned link concurrency](https://github.com/chromium/chromium/blob/156.0.8078.47/build/toolchain/concurrent_links.gni)
- [Mac build/test commands](https://chromium.googlesource.com/chromium/src/+/main/docs/mac_build_instructions.md)

- [Computed Xcode selection](https://github.com/chromium/chromium/blob/156.0.8078.47/build_overrides/build.gni#L72-L104)
- [Pinned ANGLE Metal actions](https://github.com/google/angle/blob/3b89d84e9d59f75675ac4e2bef58fc068a9f0577/src/libANGLE/renderer/metal/BUILD.gn#L27-L120)
- [Restricted-pax timestamp behavior](https://github.com/libarchive/libarchive/blob/v3.8.1/libarchive/archive_write_set_format_pax.c#L1146-L1222)
- [Pinned Siso timestamp reconciliation](https://chromium.googlesource.com/build/+/c574896b272b40aac6e44c228ee0b471ea7e3d7d/siso/hashfs/state.go#694)
- [Pinned Siso dirty-reason checks](https://chromium.googlesource.com/build/+/c574896b272b40aac6e44c228ee0b471ea7e3d7d/siso/build/mtimecheck.go#73)
