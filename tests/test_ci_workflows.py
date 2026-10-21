"""Guard the CI contract by parsing the workflow YAML.

A CI regression is expensive: a dropped coverage gate, a matrix leg that
lost 3.13, a lint step turned into a no-op or a secret scan that quietly
became advisory.  These tests read `.github/workflows/*.yml` and assert the
properties that must hold, so the suite fails before the pipeline does.

They also exercise the operator-facing scripts in `scripts/ci/`, because a
preflight script that always exits 0 is worse than no script at all.
"""

import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"
CI_SCRIPT_DIR = REPO_ROOT / "scripts" / "ci"

PYTEST_WORKFLOW = WORKFLOW_DIR / "pytest.yml"
SECURITY_WORKFLOW = WORKFLOW_DIR / "security.yml"

COVERAGE_PACKAGES = ("api", "agent", "etl", "analysis", "warehouse", "config")
REQUIRED_PREFLIGHT_VARS = (
    "DATAAGENT_BOOTSTRAP_TOKEN",
    "BILLING_EXTERNAL_URL",
    "TRUSTED_HOSTS",
    "TRUSTED_PROXY_NETWORKS",
)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _load_workflow(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def _jobs(workflow: dict) -> dict:
    return workflow.get("jobs") or {}


def _steps(job: dict):
    return job.get("steps") or []


def _step_text(step: dict) -> str:
    parts = [str(step.get("name") or ""), str(step.get("uses") or "")]
    run = step.get("run")
    if isinstance(run, str):
        parts.append(run)
    return "\n".join(parts)


def _job_text(job: dict) -> str:
    return "\n".join(_step_text(step) for step in _steps(job))


def _workflow_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _configured_fail_under() -> int:
    with (REPO_ROOT / "pyproject.toml").open("rb") as handle:
        config = tomllib.load(handle)
    return int(config["tool"]["coverage"]["report"]["fail_under"])


def _is_continue_on_error(container: dict) -> bool:
    return bool(container.get("continue-on-error"))


def _posix_shell() -> str | None:
    if sys.platform.startswith("win"):
        candidates = [
            r"C:\Program Files\Git\bin\bash.exe",
            r"C:\Program Files\Git\usr\bin\sh.exe",
        ]
        for candidate in candidates:
            if Path(candidate).exists():
                return candidate
    return shutil.which("sh") or shutil.which("bash")


def _powershell() -> str | None:
    return shutil.which("powershell") or shutil.which("pwsh")


def _scrubbed_env(**overrides: str) -> dict:
    """A minimal environment with every preflight-relevant variable removed."""
    keep = (
        "PATH",
        "SystemRoot",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
        "TEMP",
        "TMP",
        "HOME",
        "USERPROFILE",
        "LANG",
        "LC_ALL",
        "PYTHONPATH",
    )
    env = {key: os.environ[key] for key in keep if key in os.environ}
    for name in REQUIRED_PREFLIGHT_VARS + (
        "REDIS_URL",
        "AUDIT_SINK",
        "DATAAGENT_USER_DB",
        "DATAAGENT_USER_STORE",
        "DUCKDB_DATABASE_PATH",
        "LLM_PROVIDER",
        "LLM_OPENAI_KEY",
        "LLM_ANTHROPIC_KEY",
        "LLM_GEMINI_KEY",
        "LLM_GROQ_KEY",
        "LLM_XAI_KEY",
        "LLM_OPENROUTER_KEY",
    ):
        env.pop(name, None)
    env.update(overrides)
    return env


def _run_script(script: Path, env: dict) -> subprocess.CompletedProcess:
    if script.suffix == ".ps1":
        command = [
            _powershell() or "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
        ]
    else:
        command = [_posix_shell() or "sh", str(script)]
    return subprocess.run(
        command,
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


# --------------------------------------------------------------------------
# pytest workflow
# --------------------------------------------------------------------------


def test_pytest_workflow_exists():
    assert PYTEST_WORKFLOW.is_file(), "the test workflow is missing"


def test_pytest_workflow_runs_a_python_matrix_with_313():
    workflow = _load_workflow(PYTEST_WORKFLOW)
    matrices = []
    for job in _jobs(workflow).values():
        matrix = ((job.get("strategy") or {}).get("matrix") or {})
        versions = matrix.get("python-version") or []
        if isinstance(versions, str):
            versions = [versions]
        matrices.append({str(v) for v in versions})
    assert matrices, "no job in pytest.yml declares a python-version matrix"
    flat = set().union(*matrices)
    for expected in ("3.11", "3.12", "3.13"):
        assert expected in flat, f"python {expected} is missing from the CI matrix"


def test_pytest_workflow_runs_the_same_suite_on_every_matrix_leg():
    workflow = _load_workflow(PYTEST_WORKFLOW)
    runners = [
        (name, job)
        for name, job in _jobs(workflow).items()
        if "pytest" in _job_text(job)
    ]
    assert runners, "no job actually runs pytest"
    for name, job in runners:
        text = _job_text(job)
        assert "--cov=api" in text, f"job '{name}' does not use the package coverage scope"
        assert "--cov-fail-under" in text, f"job '{name}' does not enforce the coverage gate"


def test_coverage_is_scoped_to_the_real_packages():
    text = _workflow_text(PYTEST_WORKFLOW)
    assert "--cov=." not in text, "--cov=. measures the whole repo, not the packages"
    for package in COVERAGE_PACKAGES:
        assert f"--cov={package}" in text, f"--cov={package} is missing"


def test_coverage_gate_matches_the_configured_fail_under():
    configured = _configured_fail_under()
    text = _workflow_text(PYTEST_WORKFLOW)
    values = [
        token.split("=", 1)[1]
        for token in text.split()
        if token.startswith("--cov-fail-under=")
    ]
    assert values, "the workflow does not enforce a coverage gate"
    for value in values:
        assert int(value) == configured, (
            f"--cov-fail-under={value} does not match the configured "
            f"fail_under={configured}"
        )


def test_lint_step_is_present_and_blocking():
    workflow = _load_workflow(PYTEST_WORKFLOW)
    linters = ("ruff", "flake8")
    found = False
    for job_name, job in _jobs(workflow).items():
        for step in _steps(job):
            if any(linter in _step_text(step) for linter in linters):
                found = True
                assert not _is_continue_on_error(step), (
                    f"lint step in job '{job_name}' is continue-on-error"
                )
                assert not _is_continue_on_error(job), (
                    f"lint job '{job_name}' is continue-on-error"
                )
    assert found, "no lint step (ruff/flake8) found in pytest.yml"


def test_dependency_scan_step_is_present():
    workflow = _load_workflow(PYTEST_WORKFLOW)
    scanners = ("pip-audit", "safety")
    found = False
    for job_name, job in _jobs(workflow).items():
        for step in _steps(job):
            if any(scanner in _step_text(step) for scanner in scanners):
                found = True
                assert not _is_continue_on_error(step), (
                    f"dependency scan step in job '{job_name}' is continue-on-error"
                )
    assert found, "no dependency scan step (pip-audit/safety) found in pytest.yml"


def test_every_job_has_a_timeout():
    workflow = _load_workflow(PYTEST_WORKFLOW)
    for job_name, job in _jobs(workflow).items():
        timeout = job.get("timeout-minutes")
        assert timeout, f"job '{job_name}' has no timeout-minutes"
        assert int(timeout) > 0, f"job '{job_name}' has a non-positive timeout"


def test_identity_store_is_isolated_in_ci():
    text = _workflow_text(PYTEST_WORKFLOW)
    assert "DATAAGENT_USER_DB" in text, (
        "CI must point DATAAGENT_USER_DB at a throwaway path so the "
        "developer's data/users.db is never touched"
    )


def test_workflow_caches_pip_and_pytest():
    text = _workflow_text(PYTEST_WORKFLOW)
    assert "cache: pip" in text or "cache: 'pip'" in text, "pip is not cached"
    assert "actions/cache" in text, "the pytest cache is not cached"


# --------------------------------------------------------------------------
# security workflow
# --------------------------------------------------------------------------


def test_security_workflow_exists():
    assert SECURITY_WORKFLOW.is_file(), "the security workflow is missing"


def test_security_workflow_has_codeql():
    text = _workflow_text(SECURITY_WORKFLOW)
    assert "github/codeql-action" in text, "CodeQL is not configured"
    assert "analyze" in text or "upload-sarif" in text, (
        "CodeQL results are never uploaded to code scanning"
    )


def test_security_workflow_has_a_secret_scan():
    text = _workflow_text(SECURITY_WORKFLOW)
    assert "gitleaks" in text.lower(), "no secret scan (gitleaks) configured"


def test_security_workflow_has_dependency_review():
    text = _workflow_text(SECURITY_WORKFLOW)
    assert "dependency-review" in text, "no dependency review job configured"


def test_security_workflow_mentions_agentshield():
    text = _workflow_text(SECURITY_WORKFLOW)
    assert "agentshield" in text.lower(), "agentshield is not wired into CI"


# --------------------------------------------------------------------------
# scripts/ci
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "run_tests.ps1",
        "run_tests.sh",
        "preflight.ps1",
        "preflight.sh",
        "agentshield_scan.sh",
    ],
)
def test_ci_script_exists(name):
    script = CI_SCRIPT_DIR / name
    assert script.is_file(), f"scripts/ci/{name} is missing"


def _preflight_variants():
    variants = []
    powershell = _powershell()
    posix = _posix_shell()
    if powershell:
        variants.append(("preflight.ps1", powershell))
    if posix:
        variants.append(("preflight.sh", posix))
    return variants


@pytest.mark.skipif(not _preflight_variants(), reason="no script interpreter available")
def test_preflight_exits_non_zero_when_a_required_variable_is_missing(tmp_path):
    for name, _interpreter in _preflight_variants():
        script = CI_SCRIPT_DIR / name
        result = _run_script(script, _scrubbed_env())
        output = (result.stdout or "") + (result.stderr or "")
        assert result.returncode != 0, (
            f"{name} exited 0 with DATAAGENT_BOOTSTRAP_TOKEN missing"
        )
        assert "DATAAGENT_BOOTSTRAP_TOKEN" in output, (
            f"{name} did not report the missing variable"
        )
        assert "FAIL" in output, f"{name} printed no FAIL row"


@pytest.mark.skipif(not _preflight_variants(), reason="no script interpreter available")
def test_preflight_passes_when_the_hosted_configuration_is_complete(tmp_path):
    identity_store = tmp_path / "users.db"
    identity_store.write_text("")
    warehouse = tmp_path / "warehouse.duckdb"
    warehouse.write_bytes(b"duckdb")
    env = _scrubbed_env(
        DATAAGENT_BOOTSTRAP_TOKEN="t",
        BILLING_EXTERNAL_URL="https://billing.example.com",
        TRUSTED_HOSTS="analytics.example.com",
        TRUSTED_PROXY_NETWORKS="10.0.0.0/8",
        DATAAGENT_USER_DB=str(identity_store),
        DUCKDB_DATABASE_PATH=str(warehouse),
        AUDIT_SINK="sqlite",
        LLM_PROVIDER="openai",
    )
    for name, _interpreter in _preflight_variants():
        script = CI_SCRIPT_DIR / name
        result = _run_script(script, env)
        output = (result.stdout or "") + (result.stderr or "")
        assert result.returncode == 0, (
            f"{name} exited {result.returncode} on a complete configuration:\n{output}"
        )
        assert "FAIL" not in output, f"{name} reported a FAIL row:\n{output}"


@pytest.mark.skipif(not _preflight_variants(), reason="no script interpreter available")
@pytest.mark.parametrize("missing", REQUIRED_PREFLIGHT_VARS)
def test_preflight_fails_for_each_missing_required_variable(tmp_path, missing):
    identity_store = tmp_path / "users.db"
    identity_store.write_text("")
    warehouse = tmp_path / "warehouse.duckdb"
    warehouse.write_bytes(b"duckdb")
    complete = {
        "DATAAGENT_BOOTSTRAP_TOKEN": "t",
        "BILLING_EXTERNAL_URL": "https://billing.example.com",
        "TRUSTED_HOSTS": "analytics.example.com",
        "TRUSTED_PROXY_NETWORKS": "10.0.0.0/8",
        "DATAAGENT_USER_DB": str(identity_store),
        "DUCKDB_DATABASE_PATH": str(warehouse),
        "AUDIT_SINK": "sqlite",
        "LLM_PROVIDER": "openai",
    }
    complete.pop(missing)
    env = _scrubbed_env(**complete)
    for name, _interpreter in _preflight_variants():
        script = CI_SCRIPT_DIR / name
        result = _run_script(script, env)
        output = (result.stdout or "") + (result.stderr or "")
        assert result.returncode != 0, f"{name} exited 0 with {missing} missing"
        assert missing in output, f"{name} did not report {missing}"


@pytest.mark.skipif(not _preflight_variants(), reason="no script interpreter available")
def test_preflight_never_prints_a_secret_value(tmp_path):
    secret = "s3cr3t-bootstrap-token-value"
    env = _scrubbed_env(DATAAGENT_BOOTSTRAP_TOKEN=secret)
    for name, _interpreter in _preflight_variants():
        script = CI_SCRIPT_DIR / name
        result = _run_script(script, env)
        output = (result.stdout or "") + (result.stderr or "")
        assert secret not in output, f"{name} printed the secret value"
