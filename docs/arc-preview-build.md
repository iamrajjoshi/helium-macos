# Isolated personal Mac preview candidate

This is a workflow candidate, not evidence that a Chromium compile or native
feature test has passed. Use only `iamrajjoshi/helium-macos`, with reviewed source
`be567e1c7535cdf2b12381b9d283336469181834` in `iamrajjoshi/helium` as the required
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

Checkpoints use the Actions build-output cache. The workflow reads the cache
setting and requires a known limit <=10 GiB without modifying it. Compressed
checkpoints are <=9 GiB; unrelated usage plus checkpoint plus 128 MiB envelope
reserve must fit the configured free cap. Unrelated caches are never deleted.
Only this run's consumed predecessor is deleted, after a successor archive is
complete and hashed locally, immediately before saving the successor. This
prevents two multi-GB copies accumulating, but means a failed successor upload
can force rebuilding from scratch. Cache eviction can also prevent resume.

Restore requires an exact cache hit, SHA-256 validation, and matching source
and packaging commits, GN args hash, Xcode/SDK/Metal, Python, clang/GN/Siso
hashes, architecture and targets. A changed toolchain deliberately blocks
reuse. Saving is confirmed through the API, not inferred from action success.

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

## Primary evidence

- [Upstream platform build instructions](https://github.com/imputnet/helium-macos/blob/main/docs/building.md)
- [Prepared archive filtering](https://github.com/imputnet/chromium-tarballs/blob/a74ff1b1c46185744eb73eb69e7a9b587261797c/scripts/package.py)
- [Hosted six-hour job limit](https://docs.github.com/en/actions/reference/limits)
- [Actions billing and included cache versus artifact storage](https://docs.github.com/en/billing/concepts/product-billing/github-actions)
- [Cache policy](https://docs.github.com/en/actions/reference/workflows-and-actions/dependency-caching)
- [Read-only cache-limit API](https://docs.github.com/en/rest/actions/cache#get-github-actions-cache-storage-limit-for-a-repository)
- [Release-asset quotas](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases#storage-and-bandwidth-quotas)
- [Pinned ThinLTO argument](https://github.com/chromium/chromium/blob/156.0.8078.47/build/config/compiler/compiler.gni)
- [Pinned CFI defaults](https://github.com/chromium/chromium/blob/156.0.8078.47/build/config/sanitizers/sanitizers.gni)
- [Pinned link concurrency](https://github.com/chromium/chromium/blob/156.0.8078.47/build/toolchain/concurrent_links.gni)
- [Mac build/test commands](https://chromium.googlesource.com/chromium/src/+/main/docs/mac_build_instructions.md)
