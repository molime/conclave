"""
gates.py, quality gates. Exit-code is truth. A None command -> SKIP (counts as pass).

Mirrors multi-model-dev-pipeline's gate_or_skip semantics.
"""
from __future__ import annotations
from pathlib import Path

from . import util


def run_gate(name, cmd, repo, run, tag) -> dict:
    """Run one gate command. Returns {name, status, rc, log}."""
    if not cmd:
        util.log(f"  gate[{name}]: SKIP (not configured)")
        return {"name": name, "status": "SKIP", "rc": 0, "log": None}
    log_path = run / f"gate_{tag}_{name}.log"
    util.log(f"  gate[{name}]: running -> {cmd}")
    rc, _ = util.sh(cmd, cwd=repo, timeout=3600, log_path=log_path)
    status = "PASS" if rc == 0 else "FAIL"
    util.log(f"  gate[{name}]: {status} (rc={rc})")
    return {"name": name, "status": status, "rc": rc, "log": str(log_path)}


def run_gate_set(gate_names, project, run, tag) -> tuple[bool, list]:
    """
    Run a set of named gates from project.gates. Returns (all_passed, results).
    all_passed is True only if every non-skipped gate returned rc 0.
    """
    results = []
    ok = True
    for g in gate_names:
        cmd = project.gates.get(g)
        res = run_gate(g, cmd, project.repo, run, tag)
        results.append(res)
        if res["status"] == "FAIL":
            ok = False
    return ok, results


# Standard gate groupings
POST_IMPL = ["lint", "typecheck", "unit"]
FINAL = ["unit", "e2e", "build"]
