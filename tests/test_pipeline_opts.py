"""
Tests for pipeline optimizations:
  S2, gate-before-jury skip
  S4, per-stage timing fields in RunSummary
  S5, plan_override parameter
"""
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conclave.pipeline import (  # noqa: E402
    RunSummary, _gate_excerpt, _number_changes, _finish,
)


# ---------------------------------------------------------------------------
# _number_changes
# ---------------------------------------------------------------------------

def test_number_changes_single_block():
    changes = "Fix the null check in routes.ts"
    result = _number_changes(changes)
    assert "Change Request 1:" in result
    assert "Fix the null check" in result


def test_number_changes_multi_block():
    changes = "Fix A\n\n---\n\nFix B\n\n---\n\nFix C"
    result = _number_changes(changes)
    assert "Change Request 1:" in result
    assert "Change Request 2:" in result
    assert "Change Request 3:" in result


def test_number_changes_empty():
    assert _number_changes("") == ""
    assert _number_changes(None) is None


# ---------------------------------------------------------------------------
# RunSummary elapsed_s fields
# ---------------------------------------------------------------------------

def test_run_summary_elapsed_s_stored():
    """Stage dicts with elapsed_s are preserved correctly."""
    s = RunSummary(project="demo", task="test task")
    s.add(stage="plan", how="single planner kimi", chars=500, elapsed_s=12.3)
    s.add(stage="implement", ok=True, elapsed_s=97.1)
    s.add(stage="gate_postimpl", passed=True, results=[], elapsed_s=45.0)

    assert s.stages[0]["elapsed_s"] == 12.3
    assert s.stages[1]["elapsed_s"] == 97.1
    assert s.stages[2]["elapsed_s"] == 45.0


def test_finish_timing_table(tmp_path):
    """_finish() writes a timing table to summary.md when elapsed_s fields are present."""
    s = RunSummary(project="demo", task="run timing test", run_dir=str(tmp_path))
    s.add(stage="plan", how="single", chars=100, elapsed_s=10.0)
    s.add(stage="implement", ok=True, elapsed_s=50.0)
    s.add(stage="gate_postimpl", passed=True, results=[], elapsed_s=15.0)
    s.result = "CONVERGED"

    _finish(s, tmp_path)

    md = (tmp_path / "summary.md").read_text()
    assert "## Stage timing" in md
    assert "| plan |" in md
    assert "| implement |" in md
    assert "TOTAL" in md


def test_finish_no_timing_table_when_no_elapsed(tmp_path):
    """_finish() skips the timing table when no elapsed_s fields are present."""
    s = RunSummary(project="demo", task="no timing", run_dir=str(tmp_path))
    s.add(stage="plan", how="single", chars=100)  # no elapsed_s
    s.result = "PLAN_FAILED"

    _finish(s, tmp_path)

    md = (tmp_path / "summary.md").read_text()
    assert "## Stage timing" not in md


# ---------------------------------------------------------------------------
# gate_excerpt helper
# ---------------------------------------------------------------------------

def test_gate_excerpt_empty():
    assert _gate_excerpt([]) == "(no gate output captured yet)"
    assert _gate_excerpt(None) == "(no gate output captured yet)"


def test_gate_excerpt_formats_results():
    results = [
        {"name": "lint", "status": "PASS", "rc": 0, "log": None},
        {"name": "unit", "status": "FAIL", "rc": 1, "log": None},
    ]
    out = _gate_excerpt(results)
    assert "gate lint status=PASS" in out
    assert "gate unit status=FAIL" in out
