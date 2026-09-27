"""
council.py, aggregation strategies for multi-model roles.

synthesize(): planners each draft; a synthesizer backend merges into one plan.
vote():       reviewers each emit a trailing VERDICT line; quorum decides pass/fail.

S1 (2026-07-05): run_council_chat() now executes all backends in PARALLEL using
ThreadPoolExecutor. With 6 jury models this cuts review stage wall-clock from
~3-4 min (sequential) to ~40 s (bounded by the slowest single model). The
sequential path is preserved as parallel=False for callers that need ordering
guarantees (e.g. a synthesizer waiting for all drafts-though in practice even the
synthesize path benefits from parallelizing the *drafter* calls).
"""
from __future__ import annotations
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

from .adapters import make_adapter
from . import util

# A standalone verdict line: the line's ENTIRE content (after optional markdown
# bold/space) is "VERDICT: X". This avoids catching menu-echoes embedded in prose
# like "...end with VERDICT: APPROVED or VERDICT: CHANGES_REQUESTED" (a chatty
# reviewer reasoning aloud), which is a known reviewer false-negative class.
_STANDALONE_RE = re.compile(
    r"^\s*\**\s*VERDICT:\s*(APPROVED|CHANGES_REQUESTED)\s*\**\s*$",
    re.IGNORECASE,
)
_INLINE_RE = re.compile(r"VERDICT:\s*(APPROVED|CHANGES_REQUESTED)", re.IGNORECASE)


def parse_verdict(text: str) -> str:
    """
    Return APPROVED / CHANGES_REQUESTED / UNKNOWN.

    Strategy (most-reliable first):
      1. The LAST line that is ENTIRELY a verdict line (anchored), the intended
         machine-readable signal. Ignores menu-echoes buried in sentences.
      2. If a line contains BOTH options (a quoted menu), skip it.
      3. Fallback: last inline match on a line that doesn't contain both options.
      4. Final fallback: find "APPROVED" or "CHANGES_REQUESTED" as whole words in the
         last non-empty line (handles rare cases where the model omits the "VERDICT:" prefix
         but still emits the intended verdict word at the end).
    """
    if not text:
        return "UNKNOWN"
    lines = text.splitlines()
    for line in reversed(lines):
        m = _STANDALONE_RE.match(line)
        if m:
            return m.group(1).upper()
    # fallback: last inline verdict on a line that isn't a both-options menu
    for line in reversed(lines):
        ups = line.upper()
        if "APPROVED" in ups and "CHANGES_REQUESTED" in ups:
            continue  # menu echo
        m = _INLINE_RE.search(line)
        if m:
            return m.group(1).upper()
    # final fallback: whole-word verdict word on the last non-empty line
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        ups = line.upper()
        if re.search(r"\bAPPROVED\b", ups) and not re.search(r"\bCHANGES_REQUESTED\b", ups):
            return "APPROVED"
        if re.search(r"\bCHANGES_REQUESTED\b", ups) and not re.search(r"\bAPPROVED\b", ups):
            return "CHANGES_REQUESTED"
    return "UNKNOWN"


def _call_one(backend, prompt, cwd, run, stage_tag):
    """Call a single backend's chat(), designed to run inside a thread."""
    adapter = make_adapter(backend)
    log_path = run / f"{stage_tag}_{backend.name}.md"
    util.log(f"  · {stage_tag}: {backend.name} ({backend.kind}) [parallel]")
    try:
        res = adapter.chat(prompt, cwd=cwd, log_path=log_path)
    except Exception as e:  # noqa: BLE001
        from .adapters import AdapterResult
        res = AdapterResult(ok=False, text=f"[exception in thread: {e}]", rc=1)
    return backend, res


def run_council_chat(team, prompt, cwd, run, stage_tag, parallel=True):
    """
    Run every backend in a team as a chat, return list of (backend, AdapterResult)
    in the original team order.

    parallel=True (default): fire all backends simultaneously via ThreadPoolExecutor.
      With 6 jury models this cuts review wall-clock by ~5x (bounded by slowest model).
      Max workers capped at min(len(team), 8) so we don't open hundreds of threads.

    parallel=False: original sequential behaviour, kept for cases where strict ordering
      matters or for debugging.
    """
    if not team:
        return []

    if not parallel or len(team) == 1:
        # Sequential path, original behaviour
        results = []
        for b in team:
            adapter = make_adapter(b)
            log_path = run / f"{stage_tag}_{b.name}.md"
            util.log(f"  · {stage_tag}: {b.name} ({b.kind})")
            res = adapter.chat(prompt, cwd=cwd, log_path=log_path)
            results.append((b, res))
        return results

    # Parallel path
    max_workers = min(len(team), 8)
    util.log(f"  · {stage_tag}: firing {len(team)} backends in parallel (max_workers={max_workers})")

    # Submit in order; collect futures keyed by index to preserve original order
    futures = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        for idx, b in enumerate(team):
            fut = pool.submit(_call_one, b, prompt, cwd, run, stage_tag)
            futures[idx] = fut
        # Wait for all (pool.__exit__ blocks)

    # Reconstruct ordered results list
    results = [None] * len(team)
    for idx, fut in futures.items():
        try:
            backend, res = fut.result()
            results[idx] = (backend, res)
        except Exception as e:  # noqa: BLE001
            from .adapters import AdapterResult
            b = team[idx]
            results[idx] = (b, AdapterResult(ok=False, text=f"[future error: {e}]", rc=1))

    util.log(f"  · {stage_tag}: all {len(team)} parallel calls returned")
    return results


def synthesize(cfg, role_cfg, drafts, cwd, run, base_prompt):
    """
    drafts: list of (backend, AdapterResult). Merge into one plan.
    If a synthesizer is configured and >1 draft, ask it to merge; else pass through
    the first OK draft.
    """
    ok_drafts = [(b, r) for b, r in drafts if r.ok and r.text.strip()]
    if not ok_drafts:
        return "", "no planner produced output"
    if len(ok_drafts) == 1:
        return ok_drafts[0][1].text, f"single planner {ok_drafts[0][0].name}"

    syn_name = role_cfg.get("synthesizer")
    if not syn_name:
        # naive concat fallback
        merged = "\n\n---\n\n".join(
            f"## Plan from {b.name}\n\n{r.text}" for b, r in ok_drafts
        )
        return merged, "concatenated (no synthesizer configured)"

    syn = make_adapter(cfg.backend(syn_name))
    joined = "\n\n=====\n\n".join(
        f"### Candidate plan from {b.name}:\n{r.text}" for b, r in ok_drafts
    )
    prompt = (
        f"{base_prompt}\n\nSeveral planners produced the candidate plans below. "
        "Synthesize them into ONE coherent, de-duplicated, best-of-all implementation "
        "plan. Resolve conflicts, keep the strongest ideas, and output only the final "
        f"plan as markdown.\n\n{joined}"
    )
    res = syn.chat(prompt, cwd=cwd, log_path=run / "plan_synthesis.md")
    return (res.text if res.ok else ok_drafts[0][1].text), f"synthesized by {syn_name}"


def vote(reviews, quorum="majority"):
    """
    reviews: list of (backend, AdapterResult).
    Returns (passed: bool, verdicts: dict, change_requests: str).
    A reviewer whose result is not ok or whose verdict is UNKNOWN counts as
    NOT-approved (conservative) but its prose is still surfaced.
    """
    verdicts = {}
    change_blocks = []
    approors = 0
    counted = 0
    for b, r in reviews:
        v = parse_verdict(r.text) if r.ok else "UNKNOWN"
        min_chars = int(getattr(b, "spec", {}).get("min_review_chars", 0) or 0)
        substantive = r.text.strip() if r.ok else ""
        # Some cheap reviewers occasionally emit a bare verdict or a 1-line non-review.
        # Do not let that count as an approval; it is not a real code review.
        if v == "APPROVED" and min_chars and len(substantive) < min_chars:
            v = "UNKNOWN"
            substantive += (
                f"\n\n[Conclave quality guard: approval ignored because review was "
                f"{len(r.text.strip())} chars; minimum is {min_chars}.]"
            )
        verdicts[b.name] = v
        counted += 1
        if v == "APPROVED":
            approors += 1
        else:
            change_blocks.append(f"## {b.name} ({v})\n\n{substantive}")
    if counted == 0:
        return False, verdicts, ""
    if quorum == "all":
        passed = approors == counted
    elif quorum == "any":
        passed = approors >= 1
    else:  # majority
        passed = approors * 2 > counted
    return passed, verdicts, "\n\n---\n\n".join(change_blocks)
