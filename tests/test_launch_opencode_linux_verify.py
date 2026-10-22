"""The Linux opencode install must be pinned AND hash-verified - before it extracts.

ADR-012 pins every fetched artefact to a concrete version and verifies it
against a committed hash. The Windows build has done that since the ADR;
the Linux install path had not, and it was the WORSE of the two:

* the expected SHA256 came from ``OPENCODE_SHA256_LINUX_X64`` - a variable
  nothing in the repository ever set, so the postinst always took the
  "not verified" branch and then installed the binary AS ROOT;
* the tarball was extracted BEFORE the hash was checked, so a crafted
  archive could write outside the temp dir before the mismatch aborted
  the install.

The contract pinned here:

1. the release tarball's SHA256 is verified BEFORE it is extracted;
2. with no pinned hash for the target the install REFUSES (non-zero exit,
   nothing installed as root) - a warning is not enough;
3. the pins live next to the Windows one in ``installers/`` and the Linux
   build produces and exports them.

The behavioural half of this file really runs the installer with stubbed
tools (curl/sha256sum/tar/install), so "verified before extracted" and
"refuses without a pin" are observed, not merely asserted on the source.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

INSTALLER = ROOT / "packaging" / "linux" / "opencode-install.sh"
BUILD_SH = ROOT / "scripts" / "build_linux.sh"
BUILD_PS1 = ROOT / "scripts" / "build_windows.ps1"
INSTALLERS = ROOT / "installers"

#: The Linux release targets that ship a pin.
LINUX_TARGETS = ("linux-x64", "linux-arm64", "linux-x64-baseline")

#: The SHA256 the stubbed `sha256sum` reports for the release tarball. It
#: is the real pin of the linux-x64 tarball, so a pin file that carries
#: this digest matches and any other digest is a mismatch.
STUB_ARCHIVE_HASH = "13a8fb2978302071b86f9d7e00da9c8895f9cd846978379c3bdc6a5333536c9b"

#: A SHA256 that matches nothing (used to force a mismatch).
WRONG_HASH = "0" * 64


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _read(path: Path) -> str:
    assert path.exists(), f"{path} is missing"
    return path.read_text(encoding="utf-8")


def _code_only(text: str) -> str:
    """The shell code, without the comment lines."""
    return "\n".join(line for line in text.splitlines()
                     if not line.lstrip().startswith("#"))


def _linux_pinned_version() -> str:
    """The version the Linux installer pins (read from the installer)."""
    m = re.search(r'OPENCODE_VERSION="([^"]+)"', _read(INSTALLER))
    assert m, "packaging/linux/opencode-install.sh must pin OPENCODE_VERSION"
    return m.group(1)


def _windows_pinned_version() -> str:
    m = re.search(r'\$OpenCodeVersion\s*=\s*"([^"]+)"', _read(BUILD_PS1))
    assert m, "scripts/build_windows.ps1 must define $OpenCodeVersion"
    return m.group(1)


def _bash() -> str:
    """A bash that actually runs here (Windows' WSL relay does not)."""
    candidates = [
        shutil.which("bash"),
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files (x86)\Git\bin\bash.exe",
    ]
    for cand in candidates:
        if not cand or not Path(cand).exists():
            continue
        try:
            probe = subprocess.run([cand, "-c", "exit 0"],
                                   capture_output=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            continue
        if probe.returncode == 0:
            return cand
    return ""


# Stubbed tools: each one records that it ran, so the test can observe the
# ORDER of the install (verify -> extract -> install) instead of trusting
# the source. `sha256sum` reports the pinned hash for the ARCHIVE and a
# wrong hash for anything else - the extracted binary.
_STUBS = {
    "curl": """#!/usr/bin/env bash
echo "curl $*" >> "$STUB_LOG"
out=""
prev=""
for a in "$@"; do
  if [ "$prev" = "-o" ]; then out="$a"; fi
  prev="$a"
done
[ -n "$out" ] && printf 'FAKE-TARBALL-CONTENT' > "$out"
exit 0
""",
    "sha256sum": """#!/usr/bin/env bash
echo "sha256sum $*" >> "$STUB_LOG"
target="${@: -1}"
case "$target" in
  *.tar.gz) echo "$PINNED_HASH  $target" ;;
  *) echo "WRONG_HASH_PLACEHOLDER  $target" ;;
esac
exit 0
""",
    "tar": """#!/usr/bin/env bash
echo "tar $*" >> "$STUB_LOG"
dir=""
prev=""
for a in "$@"; do
  if [ "$prev" = "-C" ]; then dir="$a"; fi
  prev="$a"
done
[ -n "$dir" ] && printf 'FAKE-BINARY' > "$dir/opencode"
exit 0
""",
    "install": """#!/usr/bin/env bash
echo "install $*" >> "$STUB_LOG"
exit 0
""",
    "uname": """#!/usr/bin/env bash
echo "x86_64"
""",
}

_DRIVER = """#!/usr/bin/env bash
HERE="$(cd "$(dirname "$0")" && pwd)"
export PATH="$HERE/stubs:/usr/bin:/bin"
export STUB_LOG="$HERE/stubs.log"
export PINNED_HASH="{pinned_hash}"
bash "$HERE/opencode-install.sh"
echo "EXIT=$?"
"""


def _run_installer(bash: str, tmp_path: Path, pin: tuple | None = None):
    """Run a COPY of the installer with stubbed tools.

    The copy matters: the installer also looks for the pin in the source
    checkout, and a test that must exercise the "no pin" path cannot see
    the repository's committed pin.
    """
    work = tmp_path / "work"
    stubs = work / "stubs"
    stubs.mkdir(parents=True, exist_ok=True)
    for name, body in _STUBS.items():
        stub = stubs / name
        stub.write_text(body.replace("WRONG_HASH_PLACEHOLDER", WRONG_HASH),
                        encoding="utf-8", newline="\n")
        stub.chmod(0o755)

    script = work / "opencode-install.sh"
    script.write_text(_read(INSTALLER), encoding="utf-8", newline="\n")
    script.chmod(0o755)

    if pin is not None:
        pin_name, pin_body = pin
        (work / pin_name).write_text(pin_body, encoding="utf-8", newline="\n")

    driver = work / "driver.sh"
    driver.write_text(
        _DRIVER.format(pinned_hash=STUB_ARCHIVE_HASH),
        encoding="utf-8", newline="\n")

    result = subprocess.run([bash, str(driver).replace("\\", "/")],
                            capture_output=True, text=True, timeout=180)
    log_file = work / "stubs.log"
    log = log_file.read_text(encoding="utf-8") if log_file.exists() else ""
    return result, log


@pytest.fixture(scope="module")
def bash_bin():
    bash = _bash()
    if not bash:
        pytest.skip("no usable bash on this machine - the behavioural "
                    "contract cannot be observed here")
    return bash


def _pin_file_name(version: str, target: str) -> str:
    return f"opencode-{version}-{target}.sha256"


# ---------------------------------------------------------------------------
# the pins exist and are committed next to the Windows one
# ---------------------------------------------------------------------------

class TestCommittedPins:
    def test_the_linux_pins_are_committed_next_to_the_windows_one(self):
        version = _linux_pinned_version()
        windows = INSTALLERS / f"opencode-{version}.sha256"
        assert windows.exists(), (
            f"the Windows pin {windows.name} must be committed - the Linux "
            "pins live next to it"
        )
        for target in LINUX_TARGETS:
            path = INSTALLERS / _pin_file_name(version, target)
            assert path.exists(), (
                f"{path.name} must be committed next to {windows.name} - "
                "without it the Linux install has nothing to verify against"
            )

    def test_the_linux_pins_hold_a_valid_sha256_of_the_release_tarball(self):
        version = _linux_pinned_version()
        for target in LINUX_TARGETS:
            path = INSTALLERS / _pin_file_name(version, target)
            body = path.read_text(encoding="utf-8").strip()
            digest, _, name = body.partition(" ")
            assert re.fullmatch(r"[0-9a-f]{64}", digest), (
                f"{path.name} must hold a 64-char lowercase hex SHA256, "
                f"got {digest!r}"
            )
            assert name.strip() == f"opencode-{target}.tar.gz", (
                f"{path.name} must name the artefact it pins "
                f"(opencode-{target}.tar.gz), got {name.strip()!r}"
            )

    def test_every_pinned_opencode_version_agrees(self):
        version = _linux_pinned_version()
        assert version == _windows_pinned_version(), (
            "the Linux installer and the Windows build must pin the same "
            f"opencode release ({version} vs {_windows_pinned_version()})"
        )
        build = _read(BUILD_SH)
        m = re.search(r'OPENCODE_VERSION="([^"]+)"', build)
        assert m, "scripts/build_linux.sh must pin OPENCODE_VERSION"
        assert m.group(1) == version, (
            "scripts/build_linux.sh must pin the same opencode release as the "
            "installer and the Windows build"
        )
        import agent.opencode_bootstrap as ob

        assert ob.OPENCODE_VERSION == version, (
            "agent/opencode_bootstrap.py must pin the same opencode release "
            "as the installers"
        )


# ---------------------------------------------------------------------------
# the installer refuses to install an unverified binary
# ---------------------------------------------------------------------------

class TestInstallerRefuses:
    def test_it_exits_non_zero_without_a_pinned_hash(self, bash_bin, tmp_path):
        result, log = _run_installer(bash_bin, tmp_path)

        assert "EXIT=0" not in result.stdout, (
            "the installer must exit non-zero when no pinned hash is "
            f"configured:\n{result.stdout}\n{result.stderr}"
        )
        assert "install " not in log, (
            "the installer must NOT install the binary as root when the "
            f"download was not verified:\n{log}"
        )

    def test_a_mismatch_stops_before_the_archive_is_extracted(
        self, bash_bin, tmp_path
    ):
        version = _linux_pinned_version()
        pin = (
            _pin_file_name(version, "linux-x64"),
            f"{WRONG_HASH}  opencode-linux-x64.tar.gz\n",
        )
        result, log = _run_installer(bash_bin, tmp_path, pin=pin)

        assert "EXIT=0" not in result.stdout, (
            "a hash mismatch must abort the install:\n"
            f"{result.stdout}\n{result.stderr}"
        )
        assert "tar " not in log, (
            "the archive must be verified BEFORE it is extracted - an "
            f"unverified tarball can write outside the temp dir:\n{log}"
        )
        assert "install " not in log, "a hash mismatch must install nothing"

    def test_a_verified_tarball_is_installed(self, bash_bin, tmp_path):
        version = _linux_pinned_version()
        pin = (
            _pin_file_name(version, "linux-x64"),
            f"{STUB_ARCHIVE_HASH}  opencode-linux-x64.tar.gz\n",
        )
        result, log = _run_installer(bash_bin, tmp_path, pin=pin)

        assert "EXIT=0" in result.stdout, (
            f"a verified download must install:\n{result.stdout}\n{result.stderr}"
        )
        assert "verified" in result.stdout.lower(), (
            f"the installer must say that it verified the download:\n{result.stdout}"
        )
        assert "install " in log, "the verified binary must be installed"
        assert "/usr/local/bin/opencode" in log, (
            f"the CLI must land on PATH:\n{log}"
        )


# ---------------------------------------------------------------------------
# the source contract (runs everywhere, even without bash)
# ---------------------------------------------------------------------------

class TestInstallerSource:
    def test_it_downloads_the_pinned_release_never_latest(self):
        sh = _read(INSTALLER)
        assert "releases/latest" not in _code_only(sh), (
            "the Linux install must download the PINNED release, never the "
            "moving 'latest' one"
        )
        assert "releases/download" in sh, (
            "the download URL must be the version-pinned "
            "releases/download/<version>/<asset> URL"
        )
        assert "${OPENCODE_VERSION}" in sh, (
            "the download URL must be built from the pinned version"
        )

    def test_it_verifies_the_tarball_before_it_extracts_it(self):
        sh = _read(INSTALLER)
        assert "tar -xzf" in sh, "the installer must extract the tarball"
        verify_at = sh.index("sha256sum")
        extract_at = sh.index("tar -xzf")
        install_at = sh.index("install -m 0755")
        assert verify_at < extract_at, (
            "the SHA256 of the tarball must be computed BEFORE the tarball "
            "is extracted"
        )
        assert extract_at < install_at, (
            "the binary must be extracted before it is installed"
        )

    def test_a_missing_pin_is_a_refusal_not_a_warning(self):
        sh = _read(INSTALLER)
        assert "was NOT verified" not in sh, (
            "the old script only WARNED when no hash was pinned and then "
            "installed the binary as root - a missing pin must refuse"
        )
        assert re.search(r"(?i)no pinned sha256", sh), (
            "the script must say that the pin for this target is missing"
        )

    def test_the_missing_pin_branch_aborts_before_the_install(self):
        sh = _read(INSTALLER)
        marker = re.search(r"(?i)no pinned sha256", sh)
        assert marker, "the missing-pin branch must exist"
        window = sh[marker.start(): marker.start() + 800]
        assert re.search(r"exit\s+1", window), (
            "a missing pin must abort the install (exit 1) - a warning is "
            "not enough"
        )
        assert sh.index("install -m 0755") > marker.start(), (
            "the refusal must come BEFORE the install - otherwise the "
            "binary is installed as root anyway"
        )


# ---------------------------------------------------------------------------
# the Linux build produces and exports the pins
# ---------------------------------------------------------------------------

class TestLinuxBuild:
    def test_the_build_produces_and_exports_the_pinned_hashes(self):
        sh = _read(BUILD_SH)
        assert "installers/opencode-" in sh, (
            "the build must work with the committed pins in installers/"
        )
        for var in ("OPENCODE_SHA256_LINUX_X64",
                    "OPENCODE_SHA256_LINUX_ARM64"):
            assert var in sh, f"the build must export {var}"

    def test_the_build_never_downloads_the_moving_latest(self):
        sh = _read(BUILD_SH)
        assert "releases/latest" not in _code_only(sh), (
            "the build must never download the moving 'latest' release"
        )
        assert "releases/download" in sh, (
            "the build must download the PINNED release"
        )

    def test_the_build_no_longer_claims_the_cli_is_unpinned(self):
        sh = _read(BUILD_SH)
        assert "unpinned" not in sh.lower(), (
            "the build still prints that the CLI is an unpinned 'latest' - "
            "the note is false now that the CLI is pinned and verified"
        )
        assert re.search(r"(?i)(pinned|hash-verified|verified)", sh), (
            "the build must describe the pinned + hash-verified behaviour"
        )

    def test_the_build_stages_the_pins_into_the_package(self):
        sh = _read(BUILD_SH)
        staging = [line for line in sh.splitlines()
                   if ".sha256" in line and "$STAGE" in line]
        assert staging, (
            "the build must COPY the pinned hash files into the staged "
            "payload - the install-time helper has to find them next to "
            "itself"
        )

    def test_the_pin_function_prints_only_the_hash_on_stdout(self):
        """The build captures the hash with $(...) - progress goes to stderr.

        A progress line on stdout would end up inside the exported
        OPENCODE_SHA256_* variable.
        """
        sh = _read(BUILD_SH)
        start = sh.index("pin_opencode_target() {")
        body = sh[start:sh.index("\n}\n", start)]
        # Join the line continuations, so a multi-line echo is one command.
        joined = body.replace("\\\n", " ")
        for line in joined.splitlines():
            stripped = line.strip()
            if not stripped.startswith("echo ") or '"$actual"' in stripped:
                continue
            assert stripped.endswith(">&2"), (
                "this progress line would pollute the captured hash: "
                f"{stripped}"
            )


# ---------------------------------------------------------------------------
# the ADR must describe the behaviour the repo now has
# ---------------------------------------------------------------------------

def test_adr_012_no_longer_describes_the_linux_install_as_unverified():
    adr = _read(
        ROOT / "docs" / "decisions"
        / "ADR-012-pinned-and-hash-verified-downloads.md"
    )
    assert "not hash-verifiable" not in adr, (
        "ADR-012 must not describe the Linux install as unverified any more"
    )
    assert "installers/opencode-<version>-<target>.sha256" in adr, (
        "ADR-012 must record that the Linux release tarballs are pinned and "
        "hash-verified with committed pins"
    )
