"""Tests verifying CI wiring of the check-scanner-suppressions job."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

WORKFLOW_PATH = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "test.yml"
JOB = "check-scanner-suppressions"


def _load_workflow() -> dict[str, Any]:
    """Parse the CI workflow; a plain dict because the YAML has no fixed schema here."""
    with open(WORKFLOW_PATH, encoding="utf-8") as f:
        data: dict[str, Any] = yaml.safe_load(f)
    return data


def test_job_has_no_needs_and_no_if() -> None:
    """No needs and no if, so no path filter can skip it, e.g. for a hindsight-system-tests/-only change."""
    workflow = _load_workflow()
    job = workflow["jobs"][JOB]
    assert "needs" not in job
    assert "if" not in job


def test_job_permissions_are_contents_read() -> None:
    """The job reads the checkout only, so its token gets contents: read and nothing else."""
    workflow = _load_workflow()
    job = workflow["jobs"][JOB]
    assert job.get("permissions") == {"contents": "read"}


def test_job_runs_scanner_suppressions_script() -> None:
    """The job runs the checker script itself, so it cannot pass while checking nothing."""
    workflow = _load_workflow()
    job = workflow["jobs"][JOB]
    steps: list[dict[str, Any]] = job.get("steps", [])
    run_commands = [step["run"].strip() for step in steps if "run" in step and isinstance(step["run"], str)]
    assert "./scripts/check-scanner-suppressions.sh" in run_commands


def test_job_listed_in_report_pr_status_needs() -> None:
    """The job stays in report-pr-status's needs like every other check.

    That reporter is skipped today (the workflow has no pull_request_review trigger since #1446),
    so the job blocks through its own check run; the entry keeps it covered if the reporter returns.
    """
    workflow = _load_workflow()
    report_job = workflow["jobs"]["report-pr-status"]
    needs = report_job.get("needs", [])
    assert JOB in needs
