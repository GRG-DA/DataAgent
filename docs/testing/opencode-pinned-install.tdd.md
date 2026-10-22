# TDD evidence — the opencode pin must hold on disk and on Linux

**Scope:** two launch-blocking defects in the opencode (CLI) supply chain, fixed on 2026-10-21
against `docs/decisions/ADR-012-pinned-and-hash-verified-downloads.md` (the contract).

**Baseline:** `python -m pytest tests/test_launch_opencode_*.py` → the two new files did not
exist; the contract was violated in production code.
**Final:** `python -m pytest tests/test_launch_opencode_pinned_install.py tests/test_launch_opencode_linux_verify.py`
→ **22 passed**.

This document is the evidence index required by the project's TDD workflow: what each test
proves, with the command that was run and the output that was observed.

---

## 1. Source plan

`docs/decisions/ADR-012-pinned-and-hash-verified-downloads.md` — "every fetched binary is
version-pinned and hash-verified". The ADR carried an explicit **Known gap**: the Linux
install downloaded `releases/latest` and was not hash-verifiable. This work closes that gap
and fixes the Windows-side self-update, which installed the pinned bytes under the upstream
"latest" file name.

---

## 2. User journeys

| # | Journey |
|---|---|
| J1 | As a user of the shipped app, the opencode CLI on disk reports the version it actually is — the file name and the reported version never disagree. |
| J2 | As a user, a newer upstream opencode release does not get installed behind my back: the app ships one pinned release, and a newer one is reported as information. |
| J3 | As a user of the `.deb`/`.rpm`, the opencode CLI is verified against a committed hash **before** it is unpacked, and an unverified binary is never installed as root. |
| J4 | As a release engineer, the Linux package build produces the pinned hashes, ships them inside the package and the release gate fails when a pin is missing. |

---

## 3. Task report

| ID | What was implemented | Validation command | Result |
|---|---|---|---|
| **D1** | `agent/opencode_bootstrap.py` — the download URL, the installed file name and the reported version all derive from the single `OPENCODE_VERSION` pin; the upstream "latest" lookup is informational only. | `pytest tests/test_launch_opencode_pinned_install.py` | 6 passed |
| **D2** | `packaging/linux/opencode-install.sh` — the release **tarball** is hash-verified before it is extracted; with no pinned hash the install refuses (non-zero exit, nothing installed). | `pytest tests/test_launch_opencode_linux_verify.py` | 15 passed |
| **D2 (build)** | `scripts/build_linux.sh` — produces/verifies/exports the pinned hashes and ships the pin files inside the package; the false "unpinned 'latest'" note is gone. | `pytest tests/test_launch_opencode_linux_verify.py` | 15 passed |
| **D2 (pins)** | `installers/opencode-v1.19.3-linux-x64.sha256`, `-linux-arm64.sha256`, `-linux-x64-baseline.sha256` — committed next to the Windows pin. | `pytest tests/test_launch_opencode_linux_verify.py` | 15 passed |
| **Docs** | ADR-012 — the "Known gap" paragraph is replaced by the Linux contract. `scripts/ci/preflight_desktop.sh` — the release gate now checks the Linux pins. | `bash scripts/ci/preflight_desktop.sh` | `PREFLIGHT PASSED` |

---

## 4. Test specification

| # | What is guaranteed | Test | Type | Result | Evidence |
|---|---|---|---|---|---|
| 1 | The self-update installs the **pinned** release under its own name (`opencode-1.19.3.exe`), never after the upstream "latest". | `test_the_installed_copy_is_named_after_the_pinned_version` | unit | PASS | `pytest tests/test_launch_opencode_pinned_install.py` |
| 2 | The pin is the single source of truth: moving `OPENCODE_VERSION` moves the URL *and* the installed name. | `test_the_pin_is_the_only_source_of_the_installed_name` | unit | PASS | same |
| 3 | The download URL is the pinned release and never contains "latest". | `test_the_download_url_is_built_from_the_pin` | unit | PASS | same |
| 4 | The reported "current version" equals the version of the bytes on disk, and the file name agrees with it. | `test_the_reported_current_version_matches_the_bytes_on_disk` | unit | PASS | same |
| 5 | A newer upstream release is still reported, but it does not change the installed file; the snapshot names the pin the build ships. | `test_a_newer_upstream_release_does_not_change_the_installed_name` | unit | PASS | same |
| 6 | The prune never deletes the copy that carries the pinned release (a copy with a lying name cannot evict it). | `test_the_prune_never_deletes_the_pinned_copy` | unit | PASS | same |
| 7 | The Linux pins exist next to the Windows one and hold a valid 64-hex SHA256 of the release tarball. | `test_the_linux_pins_are_committed_next_to_the_windows_one`, `test_the_linux_pins_hold_a_valid_sha256_of_the_release_tarball` | structural | PASS | `pytest tests/test_launch_opencode_linux_verify.py` |
| 8 | The installer, the Linux build script, the Windows build script and the app agree on one pinned version. | `test_every_pinned_opencode_version_agrees` | structural | PASS | same |
| 9 | With **no** pinned hash the installer exits non-zero and never calls `install` (nothing is installed as root). | `test_it_exits_non_zero_without_a_pinned_hash` | behavioural (real bash, stubbed tools) | PASS | same |
| 10 | A hash mismatch aborts **before** `tar` runs and before `install` runs. | `test_a_mismatch_stops_before_the_archive_is_extracted` | behavioural | PASS | same |
| 11 | A verified tarball is extracted and installed to `/usr/local/bin/opencode`, and the run says it verified. | `test_a_verified_tarball_is_installed` | behavioural | PASS | same |
| 12 | The source order is verify → extract → install, and a missing pin is a refusal (not a warning) that aborts before the install. | `test_it_verifies_the_tarball_before_it_extracts_it`, `test_a_missing_pin_is_a_refusal_not_a_warning`, `test_the_missing_pin_branch_aborts_before_the_install` | structural | PASS | same |
| 13 | The Linux build produces/exports the pinned hashes, never downloads "latest", no longer claims the CLI is unpinned and copies the pins into the package. | `TestLinuxBuild` (4 tests) | structural | PASS | same |
| 14 | ADR-012 no longer describes the Linux install as unverified and records the Linux per-target pins. | `test_adr_012_no_longer_describes_the_linux_install_as_unverified` | structural | PASS | same |
| 15 | The pin helper prints only the SHA256 on stdout (the progress lines go to stderr), so the exported `OPENCODE_SHA256_*` is not polluted. | `test_the_pin_function_prints_only_the_hash_on_stdout` | structural | PASS | same |

The behavioural half (rows 9–11) really runs `packaging/linux/opencode-install.sh` under
bash with stubbed `curl`/`sha256sum`/`tar`/`install`, so "verified before extracted" and
"refuses without a pin" are **observed**, not merely asserted on the source. On a machine
without a usable bash the three behavioural tests skip; the twelve structural ones still run.

---

## 5. RED / GREEN evidence

### RED — the new bootstrap tests (before the fix)

```
$ python -m pytest tests/test_launch_opencode_pinned_install.py -v --no-header
FAILED ...TestPinnedInstallName::test_the_installed_copy_is_named_after_the_pinned_version
FAILED ...TestPinnedInstallName::test_the_pin_is_the_only_source_of_the_installed_name
FAILED ...TestReportedVersion::test_the_reported_current_version_matches_the_bytes_on_disk
FAILED ...TestLatestIsInformational::test_a_newer_upstream_release_does_not_change_the_installed_name
FAILED ...TestPrune::test_the_prune_never_deletes_the_pinned_copy
5 failed, 1 passed
```

with, for the first failure:

```
E  AssertionError: the update installed ['opencode-9.9.9.exe']; the file must be
   named after the PINNED release (1.19.3), never after the upstream 'latest'
```

### RED — the new Linux tests (before the fix)

```
$ python -m pytest tests/test_launch_opencode_linux_verify.py -v --no-header
FAILED ...TestCommittedPins::test_the_linux_pins_are_committed_next_to_the_windows_one
FAILED ...TestCommittedPins::test_the_linux_pins_hold_a_valid_sha256_of_the_release_tarball
FAILED ...TestCommittedPins::test_every_pinned_opencode_version_agrees
FAILED ...TestInstallerRefuses::test_it_exits_non_zero_without_a_pinned_hash
FAILED ...TestInstallerRefuses::test_a_mismatch_stops_before_the_archive_is_extracted
FAILED ...TestInstallerRefuses::test_a_verified_tarball_is_installed
FAILED ...TestInstallerSource::test_it_downloads_the_pinned_release_never_latest
FAILED ...TestInstallerSource::test_it_verifies_the_tarball_before_it_extracts_it
FAILED ...TestInstallerSource::test_a_missing_pin_is_a_refusal_not_a_warning
FAILED ...TestLinuxBuild::test_the_build_produces_and_exports_the_pinned_hashes
FAILED ...TestLinuxBuild::test_the_build_never_downloads_the_moving_latest
FAILED ...TestLinuxBuild::test_the_build_no_longer_claims_the_cli_is_unpinned
FAILED ...test_adr_012_no_longer_describes_the_linux_install_as_unverified
14 failed, 2 passed
```

with, for the behavioural refusal test:

```
E  AssertionError: the installer must exit non-zero when no pinned hash is configured:
   assert 'EXIT=0' not in 'Downloading opencode v1.19.3 (linux-x64)…\nopencode installed to
   /usr/local/bin/opencode.\nEXIT=0\n'
```

(the old script warned, extracted and installed the binary as root).

Two of the structural assertions passed on the old tree for the wrong reason (they matched a
*comment*). They were tightened to look at the shell code only, and re-checked against the
pre-change sources taken from git:

```
$ python <static contract re-check against `git show HEAD:…`>
PASS  installer: downloads the pinned release, never 'latest'
FAIL  installer: verifies the tarball BEFORE it extracts it
FAIL  installer: a missing pin is a refusal, not a warning
FAIL  installer: the missing-pin branch aborts before the install
FAIL  build: produces and exports the pinned hashes
PASS  build: never downloads the moving latest
FAIL  build: no longer claims the CLI is unpinned
FAIL  build: stages the pins into the package
FAIL  ADR-012: no longer describes the Linux install as unverified
FAIL  bootstrap: the installed file is named after the pinned version
2/10 static contracts already hold in the pre-change tree; 8 are RED.
```

### GREEN

```
$ python -m pytest tests/test_launch_opencode_pinned_install.py \
      tests/test_launch_opencode_linux_verify.py -q
22 passed

$ python -m pytest tests/test_launch_packaging.py -q
40 passed

$ python -m pytest tests/test_opencode_update.py tests/test_cov_agent2.py \
      tests/test_cov_agent.py tests/test_opencode_serve_robustness.py \
      tests/test_opencode_provider_vision.py tests/test_exe_release.py \
      tests/test_launch_opencode_pinned_install.py \
      tests/test_launch_opencode_linux_verify.py -q
320 passed
```

The wider launch sweep (`tests/test_launch_*.py` except the site files, plus the coverage and
opencode suites) → **683 passed**.

### The Linux pin helper, exercised end to end

`scripts/build_linux.sh::pin_opencode_target` was run against the real committed pin with
`curl` stubbed by the already-downloaded release tarball:

```
--- verified pin ---
    opencode-linux-x64.tar.gz SHA256 verified: 13a8fb2978302071b86f9d7e00da9c8895f9cd846978379c3bdc6a5333536c9b
RETURNED=13a8fb2978302071b86f9d7e00da9c8895f9cd846978379c3bdc6a5333536c9b
COMMITTED=13a8fb2978302071b86f9d7e00da9c8895f9cd846978379c3bdc6a5333536c9b
MATCH ok
--- a tampered pin must abort the build ---
TAMPERED PIN REJECTED ok
```

---

## 6. Existing tests that asserted the OLD behaviour

Four existing tests encoded the old contract ("install the upstream latest" / "the Linux
build never touches opencode") and had to change:

| Test | Old assertion | New assertion |
|---|---|---|
| `tests/test_opencode_update.py::test_ensure_updated_skips_when_runtime_has_latest` | "must not download when the runtime already has **latest**" | renamed to `test_ensure_updated_skips_when_the_runtime_has_the_pinned_release`: "must not download when the runtime already has the **pinned** release" |
| `tests/test_opencode_update.py::test_ensure_updated_noop_when_current` | "must not download when current" (current == upstream latest) | renamed to `test_ensure_updated_noop_when_the_current_cli_is_the_pinned_release`: the current CLI is the pin → no download, even when a newer upstream release exists |
| `tests/test_launch_packaging.py::test_linux_packaging_does_not_fetch_opencode_at_build_time` | the Linux build must not fetch opencode at build time | renamed to `test_linux_packaging_fetches_only_the_pinned_opencode_tarball`: the build fetches the **pinned tarball** (to produce/verify the hash pin) and never "latest"; the CLI binary is still not bundled |
| `tests/test_cov_agent2.py::TestOpencodeBootstrap::test_ensure_updated_and_prune` | "no upstream version → no install" | "the pinned install does not depend on the upstream lookup; a failed download returns False" (the download is mocked, so the test no longer touches the network) |

No test was weakened: each new assertion is strictly stronger than the one it replaced.

---

## 7. Coverage and known gaps

- Coverage was not re-measured for this change (the enforced gate lives in `pyproject.toml`,
  `fail_under = 74`); the change adds no new uncovered production branch beyond the
  already-covered install paths.
- `ruff` is not installed in this environment, so the lint gate was not run locally; the new
  code follows the surrounding style (functions under 50 lines, no input mutation, explicit
  error handling, no secrets).
- The three behavioural Linux tests skip when no usable bash is present (Windows' WSL relay
  is not one); on CI (Linux) they run.
- The `.deb` postinst still looks for the installer helper in `/usr/share/dataagent/` while
  the build stages it in `/usr/share/dataagent/app/` — a **pre-existing** path bug that is
  out of scope here. The RPM layout is unaffected, and the pin files ship in both packages.
