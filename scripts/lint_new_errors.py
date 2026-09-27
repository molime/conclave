#!/usr/bin/env python3
"""
lint_new_errors.py, baseline-aware ESLint gate.

Runs ESLint only on files changed (or added) relative to a base ref and fails
(rc=1) only when a file has MORE eslint errors than its baseline version.
Pre-existing errors elsewhere in the repo are reported but never fail the gate.

Usage (cwd = target repo):
    python lint_new_errors.py [--base main] [--ext .ts,.tsx,.js,.jsx,.mts,.cts]

Baseline content is linted via `git show <base>:<path> | npx eslint --stdin
--stdin-filename <path>`, so no worktree or npm install is needed.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys


def run(cmd: list[str], cwd: str | None = None, stdin: str | None = None) -> tuple[int, str]:
    """Run a command; on Windows .cmd shims (npx) need shell=True with a list."""
    proc = subprocess.run(
        cmd,
        cwd=cwd,
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=True,
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def git(args: list[str]) -> str:
    rc, out = run(["git"] + args)
    if rc != 0:
        return ""
    return out


def eslint_errors_for_paths(paths: list[str]) -> dict[str, list[dict]]:
    """Lint current working-tree content of the given files. Returns {path: [errors]}."""
    if not paths:
        return {}
    _, out = run(["npx", "eslint", "--format", "json", "--no-error-on-unmatched-pattern"] + paths)
    return _parse_eslint_json(out)


def eslint_errors_for_content(path: str, content: str) -> list[dict]:
    """Lint arbitrary content as if it were `path` (for baseline versions)."""
    _, out = run([
        "npx", "eslint", "--format", "json", "--stdin",
        "--stdin-filename", path,
    ], stdin=content)
    parsed = _parse_eslint_json(out)
    return parsed.get(path, next(iter(parsed.values()), []))


def _parse_eslint_json(out: str) -> dict[str, list[dict]]:
    """Extract {path: [severity-2 messages]} from eslint JSON output (tolerates noise)."""
    start = out.find("[")
    if start < 0:
        return {}
    try:
        results = json.loads(out[start:])
    except json.JSONDecodeError:
        return {}
    errors: dict[str, list[dict]] = {}
    for f in results:
        msgs = [m for m in f.get("messages", []) if m.get("severity") == 2]
        # normalize path separators for comparison
        key = f.get("filePath", "").replace("\\", "/")
        errors[key] = msgs
    return errors


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="main")
    ap.add_argument("--ext", default=".ts,.tsx,.js,.jsx,.mts,.cts")
    args = ap.parse_args()
    exts = tuple(e.strip() for e in args.ext.split(",") if e.strip())

    changed = git(["diff", "--name-only", args.base]).splitlines()
    untracked = git(["ls-files", "--others", "--exclude-standard"]).splitlines()
    candidates = [
        p.strip() for p in changed + untracked
        if p.strip().endswith(exts)
    ]
    # only files that still exist on disk (skip deletions)
    import os
    existing = [p for p in candidates if os.path.isfile(p)]

    print(f"[lint-gate] base={args.base}; {len(existing)} changed/new lintable file(s)")
    if not existing:
        print("[lint-gate] PASS (no changed lintable files)")
        return 0

    current = eslint_errors_for_paths(existing)

    failed = False
    total_new = 0
    for path in existing:
        norm = path.replace("\\", "/")
        cur_msgs = []
        for k, v in current.items():
            if k.endswith(norm) or k == norm:
                cur_msgs = v
                break
        base_rc, base_content = run(["git", "show", f"{args.base}:{norm}"])
        if base_rc != 0 or not base_content:
            base_msgs: list[dict] = []  # brand-new file -> baseline is zero errors
        else:
            base_msgs = eslint_errors_for_content(norm, base_content)
        cur_n, base_n = len(cur_msgs), len(base_msgs)
        status = "ok" if cur_n <= base_n else "NEW ERRORS"
        print(f"[lint-gate] {norm}: {cur_n} error(s) now, {base_n} at {args.base} -> {status}")
        if cur_n > base_n:
            failed = True
            total_new += cur_n - base_n
            for m in cur_msgs:
                print(
                    f"    {m.get('line')}:{m.get('column')} "
                    f"{m.get('ruleId') or 'fatal'}: {m.get('message')}"
                )

    if failed:
        print(f"[lint-gate] FAIL ({total_new} new eslint error(s) vs {args.base})")
        return 1
    print("[lint-gate] PASS (no new eslint errors vs base)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
