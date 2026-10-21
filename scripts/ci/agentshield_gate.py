"""Drift gate for the agentshield scan.

`agentshield scan --path .` reports every finding in the agent configuration.
The tree has open findings today, so the CI gate is a *drift* gate: a
critical or high finding that is not in the committed baseline fails the
build, while a finding that merely moved inside its file does not.

Usage:
    python scripts/ci/agentshield_gate.py --report report.json --baseline base.json
    python scripts/ci/agentshield_gate.py --report report.json --save-baseline base.json

Exit codes:
    0 - no new critical/high finding
    1 - new critical/high finding(s)
    2 - usage or I/O error
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

BLOCKING_SEVERITIES = ("critical", "high")


def finding_key(finding: dict) -> str:
    """A stable identity for a finding.

    The line number and the evidence are deliberately excluded: editing a
    document above a finding must not look like a new finding.
    """
    return "|".join(
        [
            str(finding.get("severity", "")).lower(),
            str(finding.get("category", "")).lower(),
            str(finding.get("title", "")).strip(),
            str(finding.get("file", "")).replace("\\", "/"),
        ]
    )


def load_findings(report_path: Path) -> list[dict]:
    with report_path.open("r", encoding="utf-8", errors="replace") as handle:
        report = json.load(handle)
    findings = report.get("findings")
    if not isinstance(findings, list):
        raise ValueError(f"{report_path} has no 'findings' list")
    return findings


def count_findings(findings: list[dict]) -> Counter:
    return Counter(finding_key(finding) for finding in findings)


def write_baseline(baseline_path: Path, findings: list[dict]) -> int:
    counts = count_findings(findings)
    payload = {
        "tool": "ecc-agentshield",
        "note": "Open agentshield findings accepted as the starting point. "
                "A new critical/high finding fails the CI gate.",
        "counts": dict(sorted(counts.items())),
    }
    baseline_path.parent.mkdir(parents=True, exist_ok=True)
    with baseline_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=False)
        handle.write("\n")
    return len(counts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True, help="agentshield JSON report")
    parser.add_argument("--baseline", help="committed baseline JSON")
    parser.add_argument("--save-baseline", help="write the baseline and exit")
    args = parser.parse_args(argv)

    report_path = Path(args.report)
    if not report_path.is_file():
        print(f"agentshield report not found: {report_path}", file=sys.stderr)
        return 2

    try:
        findings = load_findings(report_path)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"cannot read the agentshield report: {error}", file=sys.stderr)
        return 2

    if args.save_baseline:
        baseline_path = Path(args.save_baseline)
        written = write_baseline(baseline_path, findings)
        print(f"baseline written to {baseline_path} ({written} distinct findings)")
        return 0

    if not args.baseline:
        print("--baseline or --save-baseline is required", file=sys.stderr)
        return 2

    baseline_path = Path(args.baseline)
    try:
        with baseline_path.open("r", encoding="utf-8") as handle:
            baseline = json.load(handle)
    except (OSError, json.JSONDecodeError) as error:
        print(f"cannot read the baseline {baseline_path}: {error}", file=sys.stderr)
        return 2

    known = Counter(baseline.get("counts") or {})
    current = count_findings(findings)

    regressions = []
    for key, count in current.items():
        severity = key.split("|", 1)[0]
        if severity not in BLOCKING_SEVERITIES:
            continue
        if count > known.get(key, 0):
            regressions.append((key, count - known.get(key, 0)))

    if not regressions:
        print(
            f"agentshield: no new critical/high finding "
            f"({len(current)} distinct findings, {len(regressions)} regressions)"
        )
        return 0

    print("agentshield: NEW critical/high findings:", file=sys.stderr)
    for key, delta in sorted(regressions):
        print(f"  +{delta}  {key}", file=sys.stderr)
    print(
        "Fix them, or extend scripts/ci/agentshield-baseline.json deliberately "
        "(the baseline is the only accepted exception).",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
