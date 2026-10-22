"""Release-engineering contracts for the desktop launch (Agent 5 work items).

These are *static* contract checks over the release tooling: the PowerShell
build script, the compose file, the Dockerfile, the CI workflows and the
preflight / smoke scripts. They are the same kind of assertion the existing
``tests/test_packaging.py`` makes about the version string and the installer
branding: the artefacts under test are PowerShell / YAML / Docker files that
cannot be executed inside the pytest process, so the contract is asserted on
their source.

Covered work items
------------------
D4   the build script pins an opencode version instead of downloading "latest"
D5   code signing fails loudly; an unsigned build says so at the end
R2   every build path uses the same Python version
R3   the Docker ``config`` mount is writable (three routes write there)
R4   the CI security scan is a real gate and the image is smoke tested
R5   a clean-room smoke test of the installed app exists
R6   the stray repo-root artefacts are gone
R7   a desktop preflight script exists
C10  the support/diagnostics path (log dir + support bundle) is documented
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent

BUILD_PS1 = ROOT / "scripts" / "build_windows.ps1"
BUILD_SH = ROOT / "scripts" / "build_linux.sh"
COMPOSE = ROOT / "docker-compose.yml"
DOCKERFILE = ROOT / "Dockerfile"
WORKFLOW_DIR = ROOT / ".github" / "workflows"
DOCKER_WORKFLOW = WORKFLOW_DIR / "docker-prod.yml"
WINDOWS_WORKFLOW = WORKFLOW_DIR / "build-windows.yml"
PREFLIGHT_PS1 = ROOT / "scripts" / "ci" / "preflight_desktop.ps1"
PREFLIGHT_SH = ROOT / "scripts" / "ci" / "preflight_desktop.sh"
SMOKE_PS1 = ROOT / "scripts" / "ci" / "smoke_desktop.ps1"

#: The release interpreter. Every build path (Dockerfile, CI workflows, the
#: local dev environment) must agree on it: the release artefact must be built
#: by the same interpreter *and* the same dependency set that CI tests.
#: ``requirements.txt`` gates ``fastparquet`` on ``python_version < "3.13"``,
#: so a 3.11 build installs a different dependency set than a 3.13 build.
RELEASE_PYTHON = "3.13"

#: The config seeds the container must always be able to read.
SEED_CONFIG_FILES = ("settings.yaml", "model_config.yaml", "db_config.yaml")

#: Runtime credential state that must never end up in a release bundle.
CREDENTIAL_FILES = (
    "config/users.json",
    "config/db_connections.json",
    "config/google_login_pending.json",
    "config/llm_runtime.json",
    "config/revoked_jtis.json",
    ".env",
    "data/.jwt_secret",
    "data/.grant_key",
    "data/.grant_migrated",
)

#: Files that a support bundle may contain.
SUPPORT_COLLECT_FILES = (
    "logs/app.log",
    "logs/audit.log",
    "logs/runtime-stdout.log",
    "config/llm_runtime.json",
)

#: Files that a support bundle must never contain.
SUPPORT_NEVER_COLLECT = (
    "data/.jwt_secret",
    "data/.grant_key",
    "config/users.json",
    "data/users.db",
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _read(path: Path) -> str:
    assert path.exists(), f"{path} is missing"
    return path.read_text(encoding="utf-8")


def _pinned_opencode_version() -> str:
    m = re.search(r'\$OpenCodeVersion\s*=\s*"([^"]+)"', _read(BUILD_PS1))
    assert m, (
        'scripts/build_windows.ps1 must define $OpenCodeVersion = "vX.Y.Z" '
        "(a pinned release, never \"latest\")"
    )
    return m.group(1)


def _compose() -> dict:
    return yaml.safe_load(_read(COMPOSE))


def _api_volume_entries() -> list[str]:
    compose = _compose()
    entries = compose["services"]["api"].get("volumes") or []
    return [str(e) for e in entries]


def _mount_target(entry: str) -> str:
    """Container path of a compose volume entry (``src:dst[:mode]``)."""
    parts = entry.split(":")
    return parts[1] if len(parts) > 1 else ""


def _mount_mode(entry: str) -> str:
    parts = entry.split(":")
    return parts[2] if len(parts) > 2 else ""


# ---------------------------------------------------------------------------
# D4 — the opencode CLI version is pinned, never "latest"
# ---------------------------------------------------------------------------

def test_build_windows_pins_a_concrete_opencode_version():
    version = _pinned_opencode_version()
    assert re.fullmatch(r"v\d+\.\d+\.\d+", version), (
        f"$OpenCodeVersion must be a concrete release tag, got {version!r}"
    )


def test_build_windows_downloads_the_pinned_opencode_release():
    ps1 = _read(BUILD_PS1)
    assert "releases/download" in ps1, (
        "the opencode download must use the version-pinned "
        "releases/download/<version>/<asset> URL"
    )
    assert "$OpenCodeVersion" in ps1, (
        "the download URL must be built from $OpenCodeVersion"
    )
    assert "releases/latest" not in ps1, (
        "scripts/build_windows.ps1 still downloads the moving 'latest' "
        "release - the pinned SHA256 can then never match"
    )


def test_pinned_opencode_sha256_file_is_committed_and_valid():
    version = _pinned_opencode_version()
    sha_file = ROOT / "installers" / f"opencode-{version}.sha256"
    assert sha_file.exists(), (
        f"the pinned SHA256 for {version} must be committed at {sha_file.name} "
        "(it is the second half of the pin: version constant + hash file)"
    )
    digest = sha_file.read_text(encoding="utf-8").strip().split()[0]
    assert re.fullmatch(r"[0-9a-f]{64}", digest), (
        f"{sha_file.name} must contain a 64-char lowercase hex SHA256"
    )
    assert sha_file.read_text(encoding="utf-8").strip(), "sha256 file is empty"


def test_build_windows_verifies_the_pinned_hash_and_never_skips_silently():
    ps1 = _read(BUILD_PS1)
    assert "SHA256 mismatch" in ps1, "the SHA256 comparison must still abort"
    assert re.search(r"throw[^\n]*SHA256 mismatch", ps1), (
        "a hash mismatch must throw (abort the build), not warn"
    )
    # The missing-hash-file path must be loud, not a silent skip.
    assert re.search(r"(?i)warn", ps1), (
        "the missing-hash-file path must print a warning"
    )
    assert re.search(r"(?i)(out[- ]of[- ]band|manually verify|verify it out)", ps1), (
        "the missing-hash-file path must tell the maintainer to verify the "
        "hash out of band and commit it"
    )


def test_build_windows_prints_the_pinned_opencode_summary():
    ps1 = _read(BUILD_PS1)
    assert re.search(r"(?i)opencode.*(version|pinned)", ps1), (
        "the build log must record the pinned opencode version"
    )


# ---------------------------------------------------------------------------
# D5 — code signing must fail loudly, and an unsigned build must say so
# ---------------------------------------------------------------------------

def test_sign_without_signtool_fails_the_build():
    ps1 = _read(BUILD_PS1)
    assert "unsigned build kept in dist/" not in ps1, (
        "the old message kept an unsigned release after -Sign was requested; "
        "the build must fail instead"
    )
    # The signtool lookup must be followed by a hard failure, not a warning.
    m = re.search(r"signtool\.exe not found", ps1)
    assert m, "the signtool-missing branch must be present"
    window = ps1[max(0, m.start() - 200): m.start() + 400]
    assert re.search(r"(?i)(throw|exit\s+1)", window), (
        "a missing signtool under -Sign must abort the build (throw/exit 1)"
    )


def test_signing_verifies_the_signature_after_signing():
    ps1 = _read(BUILD_PS1)
    m = re.search(r"[^\n]*\bsigntool\b[^\n]*\bverify\b[^\n]*", ps1)
    assert m, "the build must verify the signature after signing"
    assert "/sha1" in m.group(0), "verification must pin the signer's sha1"
    tail = ps1[m.start(): m.start() + 500]
    assert "LASTEXITCODE" in tail, (
        "the verification step must check the exit code and fail the build"
    )


def test_signing_covers_the_three_release_artefacts():
    ps1 = _read(BUILD_PS1)
    for artefact in (
        "dist\\DataAgent.exe",
        "dist\\DataAgentShell\\DataAgentShell.exe",
        "dist\\DataAgent-Setup.exe",
    ):
        assert artefact in ps1, f"the signing step must cover {artefact}"


def test_signing_uses_an_rfc3161_timestamp():
    ps1 = _read(BUILD_PS1)
    assert "/tr" in ps1 and "/td SHA256" in ps1, (
        "signing must use an RFC3161 timestamp with a SHA256 digest"
    )
    assert "timestamp.digicert.com" in ps1, (
        "the default RFC3161 timestamp endpoint must be digicert"
    )


def test_signing_keeps_the_documented_env_var_contract():
    ps1 = _read(BUILD_PS1)
    for var in (
        "DATAAENT_CODESIGN_THUMBPRINT",
        "DATAAENT_CODESIGN_PFX",
        "DATAAENT_CODESIGN_PASSWORD",
        "DATAAENT_CODESIGN_TIMESTAMP",
    ):
        assert var in ps1, f"the signing contract must keep ${var}"


def test_unsigned_build_prints_a_prominent_warning():
    ps1 = _read(BUILD_PS1)
    assert re.search(r"UNSIGNED", ps1), (
        "a build without -Sign must print an UNSIGNED warning"
    )
    assert "Unknown publisher" in ps1, (
        "the warning must name the SmartScreen 'Unknown publisher' symptom"
    )
    # The warning must be printed at the END of the build, after the artefacts
    # exist - and must point at the exact signing command.
    assert re.search(r"(?i)signtool\s+sign", ps1), (
        "the warning must print the exact signtool invocation"
    )
    assert ps1.rindex("UNSIGNED") > ps1.index("Checksums"), (
        "the UNSIGNED warning belongs at the very end of the build, after the "
        "artefacts have been produced and hashed"
    )


# ---------------------------------------------------------------------------
# R2 — one Python version for the release
# ---------------------------------------------------------------------------

def test_windows_workflow_builds_on_the_release_python_and_runs_the_preflight():
    text = _read(WINDOWS_WORKFLOW)
    assert f'python-version: "{RELEASE_PYTHON}"' in text, (
        "the Windows release job must build on the release Python version"
    )
    assert "scripts/ci/preflight_desktop.ps1" in text, (
        "the Windows release job must run the desktop preflight"
    )


def test_linux_packaging_fetches_only_the_pinned_opencode_tarball():
    """The .deb/.rpm build does not BUND opencode - it pins the tarball.

    The Linux package ships an installer helper that downloads the CLI at
    install time, so the ~180 MB binary is never bundled. The build does
    download the PINNED release tarball - to produce and verify the
    committed hash pin that the helper verifies the download against
    (ADR-012). It must never touch the moving 'latest' release.
    """
    sh = _read(BUILD_SH)
    assert "releases/latest" not in sh
    assert "github.com/anomalyco/opencode/releases/download" in sh, (
        "the build must download the PINNED release tarball to produce the "
        "hash pin the installer verifies the download against"
    )
    assert "releases/download/${OPENCODE_VERSION}" in sh, (
        "the download URL must be built from the pinned version"
    )
    assert "installers/opencode/" not in sh, (
        "the Linux build must not pull the CLI binary out of the gitignored "
        "installers/opencode/ directory - it is fetched per release"
    )


def test_build_never_clobbers_the_hand_maintained_spec():
    """PyInstaller regenerates the spec from the CLI flags - the tracked one
    must not be written.

    The regenerated DataAgent.spec drops the version/icon wiring AND the
    credential-exclusion logic (config/users.json, .env, ... would ship in
    the onefile bundle), so a build that is killed between the PyInstaller
    run and the restore leaves the tree in a dangerous state.
    """
    ps1 = _read(BUILD_PS1)
    assert "--specpath" in ps1, (
        "the generated spec must go to a temp dir (--specpath); otherwise the "
        "CLI overwrites the hand-maintained DataAgent.spec"
    )
    assert re.search(r"(?s)try\s*\{.*PyInstaller.*?finally\s*\{", ps1), (
        "the spec restore must sit in a finally block, so a failed build can "
        "never leave the tracked spec regenerated"
    )


def test_dockerfile_uses_the_release_python_version():
    dockerfile = _read(DOCKERFILE)
    assert f"python:{RELEASE_PYTHON}-slim" in dockerfile, (
        f"the Dockerfile must build on Python {RELEASE_PYTHON}"
    )
    assert "python:3.11-slim" not in dockerfile, (
        "the Dockerfile still builds on Python 3.11"
    )
    assert "python3.11" not in dockerfile, (
        "the Dockerfile still points PYTHONPATH at Python 3.11"
    )
    assert f"python{RELEASE_PYTHON}/site-packages" in dockerfile, (
        "PYTHONPATH must point at the release interpreter's site-packages"
    )


def test_ci_workflows_use_the_release_python_version():
    """The RELEASE is built with one interpreter; the TEST matrix covers all.

    Two different contracts, and they must not be confused:

    * the release artefact (the Docker image and the Windows installer) is
      built with the canonical version, so the shipped binary and the CI
      build cannot drift apart;
    * the test matrix still runs every interpreter the app supports, because
      ``requirements.txt`` gates ``fastparquet`` on
      ``python_version < "3.13"`` - a 3.11 leg installs a different
      dependency set and can catch a break the release leg never sees.
    """
    # 1. The release workflows pin the canonical version and nothing else.
    for name in ("build-windows.yml", "docker-prod.yml"):
        wf = WORKFLOW_DIR / name
        if not wf.exists():
            continue
        text = _read(wf)
        assert not re.search(r"python-version:\s*\[?[^\n]*3\.11", text), (
            f"{name} builds the release on Python 3.11; the release is built "
            f"on {RELEASE_PYTHON}"
        )
        assert "python:3.11" not in text, (
            f"{name} still references the Python 3.11 image"
        )

    # 2. The test matrix covers every supported interpreter, including the
    #    canonical release version.
    pytest_wf = WORKFLOW_DIR / "pytest.yml"
    if pytest_wf.exists():
        text = _read(pytest_wf)
        for version in ("3.11", "3.12", RELEASE_PYTHON):
            assert f'"{version}"' in text, (
                f"pytest.yml does not test Python {version}"
            )


def test_dockerfile_states_the_canonical_python_version():
    dockerfile = _read(DOCKERFILE)
    assert re.search(r"(?i)(canonical|release)\s+python", dockerfile), (
        "the Dockerfile must state the canonical Python version in one obvious "
        "place"
    )


# ---------------------------------------------------------------------------
# R3 — the Docker config mount must be writable
# ---------------------------------------------------------------------------

def test_compose_config_mount_is_not_read_only():
    entries = _api_volume_entries()
    config_mounts = [e for e in entries if _mount_target(e) == "/app/config"]
    assert config_mounts, "the api service must mount /app/config"
    for entry in config_mounts:
        assert _mount_mode(entry) != "ro", (
            f"/app/config is mounted read-only ({entry}); three routes write "
            "there (POST /llm/providers/update, POST /pipeline/save, "
            "POST /pipeline/schedule) so the Settings page cannot change the "
            "LLM provider"
        )


def test_compose_still_exposes_the_seed_config_to_the_container():
    entries = _api_volume_entries()
    dir_mount = next(
        (e for e in entries
         if _mount_target(e) == "/app/config" and _mount_mode(e) != "ro"),
        None,
    )
    for seed in SEED_CONFIG_FILES:
        if dir_mount is not None:
            continue  # a writable /app/config bind exposes the seeds
        binds = [
            e for e in entries
            if _mount_target(e) == f"/app/config/{seed}"
        ]
        assert binds, (
            f"the container must still see the seed config {seed} "
            "(bind it read-only over the writable volume)"
        )
        assert all(_mount_mode(e) == "ro" for e in binds), (
            f"the seed config {seed} must stay read-only"
        )


def test_compose_declares_the_runtime_config_volume():
    compose = _compose()
    volumes = compose.get("volumes") or {}
    assert volumes, "the named runtime volume must be declared"
    named = set(volumes)
    used = {
        e.split(":")[0] for e in _api_volume_entries()
        if not e.startswith(".") and not e.startswith("/")
    }
    assert used & named, (
        "the writable runtime config volume must be a declared named volume"
    )


# ---------------------------------------------------------------------------
# R4 — the CI security scan is a real gate and the image is smoke tested
# ---------------------------------------------------------------------------

def test_trivy_gate_can_fail():
    text = _read(DOCKER_WORKFLOW)
    assert 'exit-code: "1"' in text, (
        "the Trivy step must be able to fail the build (exit-code: \"1\")"
    )
    assert 'exit-code: "0"' not in text, (
        "exit-code: \"0\" means the scan never gates anything"
    )


def test_trivy_severity_matches_the_step_name():
    text = _read(DOCKER_WORKFLOW)
    assert re.search(r"severity:\s*[\"']?HIGH,CRITICAL", text), (
        "the scan must cover the HIGH/CRITICAL set the step name claims"
    )


def test_ci_smoke_tests_the_built_image():
    text = _read(DOCKER_WORKFLOW)
    assert re.search(r"(?i)smoke", text), (
        "the workflow must smoke test the built image"
    )
    assert re.search(r"(?i)(/health|/ready)", text), (
        "the smoke test must probe a health endpoint"
    )


def test_ci_has_no_fake_deploy_job():
    text = _read(DOCKER_WORKFLOW)
    assert "deploy" not in text.lower() or "future deploy" in text.lower(), (
        "there is no registry or deploy target yet - the workflow must not "
        "pretend to deploy"
    )


# ---------------------------------------------------------------------------
# R5 / R7 — preflight + smoke scripts
# ---------------------------------------------------------------------------

def test_preflight_desktop_script_exists():
    assert PREFLIGHT_PS1.exists(), "scripts/ci/preflight_desktop.ps1 is missing"
    assert PREFLIGHT_SH.exists(), "scripts/ci/preflight_desktop.sh is missing"
    assert SMOKE_PS1.exists(), "scripts/ci/smoke_desktop.ps1 is missing"


def test_preflight_checks_the_required_toolchain():
    ps1 = _read(PREFLIGHT_PS1)
    for needle in ("PyInstaller", "ISCC.exe", "signtool", "OpenCodeVersion"):
        assert needle in ps1, f"the preflight must check {needle}"


def test_preflight_checks_the_opencode_pin():
    ps1 = _read(PREFLIGHT_PS1)
    assert "sha256" in ps1.lower(), "the preflight must check the sha256 file"
    assert "installers" in ps1, "the preflight must look for the pin file"


def test_preflight_asserts_no_credential_state_is_bundled():
    ps1 = _read(PREFLIGHT_PS1)
    assert "DataAgent.spec" in ps1, "the preflight must read DataAgent.spec"
    assert "DataAgentShell.spec" in ps1, (
        "the preflight must read scripts/DataAgentShell.spec"
    )
    for needle in ("users.json", ".jwt_secret", ".grant_key", ".env"):
        assert needle in ps1, f"the preflight must exclude {needle}"


def test_preflight_checks_version_agreement():
    ps1 = _read(PREFLIGHT_PS1)
    for needle in (
        "version_info.txt",
        "settings.yaml",
        "MyAppVersion",
    ):
        assert needle in ps1, f"the preflight must compare {needle}"


def test_preflight_probes_webview2_and_disk_space():
    ps1 = _read(PREFLIGHT_PS1)
    assert "WebView2" in ps1, "the preflight must probe the WebView2 runtime"
    assert re.search(r"(?i)(disk|free space)", ps1), (
        "the preflight must check the disk has room for the installer"
    )


def test_preflight_never_prints_secret_values():
    for script in (_read(PREFLIGHT_PS1), _read(PREFLIGHT_SH)):
        for line in script.splitlines():
            mentions_secret = any(
                name.rsplit("/", 1)[-1] in line
                for name in CREDENTIAL_FILES
            )
            reads_content = any(
                reader in line
                for reader in ("Get-Content", "ReadAllText", "ReadAllBytes",
                               "OpenText", "cat ", "more ", "type ")
            )
            assert not (mentions_secret and reads_content), (
                f"the preflight must never read the contents of a credential "
                f"file: {line.strip()!r}"
            )


def test_smoke_script_covers_the_persistence_regressions():
    smoke = _read(SMOKE_PS1)
    assert ".jwt_secret" in smoke, "the smoke test must check the JWT secret"
    assert ".grant_key" in smoke, "the smoke test must check the grant key"
    assert re.search(r"(?i)(launch|start).{0,40}1", smoke), (
        "the smoke test must launch the app twice to prove persistence"
    )
    assert "LOCALAPPDATA" in smoke, (
        "the smoke test must look in the per-user runtime dir"
    )


def test_smoke_script_marks_the_manual_browser_steps():
    smoke = _read(SMOKE_PS1)
    assert "MANUAL" in smoke.upper(), (
        "steps that need a browser must be marked MANUAL, not faked"
    )


def test_smoke_script_never_prints_secret_values():
    for line in _read(SMOKE_PS1).splitlines():
        mentions_secret = any(
            name.rsplit("/", 1)[-1] in line for name in CREDENTIAL_FILES
        )
        reads_content = any(
            reader in line
            for reader in ("Get-Content", "ReadAllText", "ReadAllBytes",
                           "OpenText", "cat ")
        )
        assert not (mentions_secret and reads_content), (
            f"the smoke test must never print a secret value: {line.strip()!r}"
        )


# ---------------------------------------------------------------------------
# R6 — stray repo-root artefacts
# ---------------------------------------------------------------------------

def test_stray_repo_root_artefacts_are_gone():
    for stray in ("out.html", "ollama"):
        assert not (ROOT / stray).exists(), f"{stray} is committed junk"
    assert not (ROOT / "lib").exists(), (
        "the empty lib/ directory is committed junk"
    )
    stale = ROOT / "installers" / "DataAgent-Setup-verify2.iss"
    assert not stale.exists(), (
        "installers/DataAgent-Setup-verify2.iss is a stale, diverged "
        "duplicate of DataAgent-Setup.iss"
    )


def test_secure_zip_excludes_fetched_binaries_and_strays():
    """The source zip must not carry the downloaded opencode binary."""
    import importlib.util
    import sys

    path = ROOT / "scripts" / "build_secure_zip.py"
    spec = importlib.util.spec_from_file_location("_scripts_build_secure_zip", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    assert "opencode" in module.EXCLUDE_DIRS, (
        "the opencode CLI (~180 MB, downloaded at build time) must not go "
        "into the source zip"
    )
    assert "lib" in module.EXCLUDE_DIRS, "the stray lib/ directory must not ship"
    assert "out.html" in module.EXCLUDE_FILES, (
        "the stale repo-root out.html must not ship"
    )


def test_ci_lint_paths_do_not_reference_a_deleted_directory():
    """ruff fails the job when a path in its argument list does not exist."""
    text = _read(WORKFLOW_DIR / "pytest.yml")
    m = re.search(
        r"- name: Ruff \(critical rules\)\n\s+run: >\n((?:\s+[^\n]*\n)+)",
        text,
    )
    assert m, "the ruff invocation must still be present"
    block = m.group(1)
    for token in block.split():
        if token in {"lib", "lib/"}:
            raise AssertionError(
                "ruff is pointed at lib/, which no longer exists - ruff exits "
                "non-zero and the lint job fails"
            )


# ---------------------------------------------------------------------------
# C10 — support / diagnostics
# ---------------------------------------------------------------------------

def test_tray_exposes_the_logs_folder():
    tray = _read(ROOT / "shell" / "tray.py")
    assert "Open logs folder" in tray, (
        "the tray must keep the 'Open logs folder' item"
    )
    assert "logs" in tray, "the tray item must resolve the logs directory"


def test_preflight_checks_the_support_log_directory():
    ps1 = _read(PREFLIGHT_PS1)
    assert re.search(r"(?i)log", ps1), (
        "the preflight must check the support log directory"
    )
    assert "LOCALAPPDATA" in ps1, (
        "the log directory lives under the per-user runtime dir"
    )


def test_preflight_documents_the_support_bundle_procedure():
    ps1 = _read(PREFLIGHT_PS1)
    normalised = ps1.replace("\\", "/")
    for needle in SUPPORT_COLLECT_FILES:
        assert needle in normalised, (
            f"the support bundle procedure must name {needle} as collectable"
        )
    for needle in SUPPORT_NEVER_COLLECT:
        assert needle in normalised, (
            f"the support bundle procedure must name {needle} as never-collect"
        )
