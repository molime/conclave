"""
pipeline.py, the Conclave engine. Orchestrates the stage loop:

  PLAN (council/synthesize) -> IMPLEMENT (executor) -> GATE(post-impl)
  -> REVIEW (jury/vote) <-> FIX (executor)  [loop until quorum or max-iters]
  -> FINAL GATE -> [optional] FRONTIER REVIEW (reopen cheap loop on CHANGES_REQUESTED)
  -> summary.md / summary.json

Honesty rules (inherited from multi-model-dev-pipeline):
  - a stage/gate is "passed" only when the real exit code is 0
  - the review loop converges only on a real quorum verdict, never on the iter cap
  - everything is logged to .conclave-runs/<ts>/

2026-07-05 optimizations:
  S2, Gate-before-jury skip: when post-impl gates are RED skip the jury entirely and
       go straight to fix. Avoids firing 6 HTTP review calls for a broken build.
  S3, Stronger prompts: PLAN requires numbered ACs + files-table; IMPLEMENT requires
       explicit self-review checklist; FIX requires numbered response per request.
  S4, Per-stage timing: every summary.add(...) includes elapsed_s; _finish() emits
       a timing table in summary.md.
  S5, plan_override param: pass a pre-written plan to skip the PLAN council stage.
"""
from __future__ import annotations
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import council, gates, util
from .adapters import make_adapter


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

PLAN_PROMPT = """\
You are a principal/staff engineer PLANNING a code change. Repo context:
{context}

TASK:
{task}

DISCOVERY REQUIREMENT: If your backend is an agentic CLI (kimi, codex, claude), you MUST
open and read the actual relevant files before writing the plan, do not reason from the
context description alone. If your backend is chat-only (Ollama, OpenRouter), reason from
the provided context and state clearly that you have not inspected the files.

Produce a premium implementation plan with ALL of the following sections:

1. **Restated goal** and **numbered acceptance criteria** (AC-1, AC-2 …), the implementer
   will tick these off one by one. Be explicit; do not say "handle edge cases."

2. **Discovery notes**, files/seams/contracts you verified, or a note that repo inspection
   was unavailable. Cite the actual function/type names you found.

3. **Alternatives considered** and why the chosen approach is safest/simplest.

4. **Files to create/modify**, a table: | File | Change type | What changes |

5. **Data/auth/security/multi-tenant/PHI/idempotency edge cases** (as applicable).

6. **Test strategy**, specific unit/integration/e2e cases and gate commands to run.

7. **Rollback / verification notes**.

Do NOT write the full code. Keep the plan focused on ONLY what the task requires.
"""

IMPLEMENT_PROMPT = """\
You are IMPLEMENTING a code change in this repo. Repo context:
{context}

TASK:
{task}

PLAN TO FOLLOW (with numbered acceptance criteria, tick them all off):
{plan}

Implementation rules:
1. OPEN and READ each file before editing it. Do NOT assume its current content.
2. Implement the plan exactly. Touch ONLY the files the task requires.
3. Do NOT run a repo-wide format/lint-fix. Do NOT edit unrelated files.
4. The final diff must be feature-only.
5. Make the project's existing tests pass.

When done, output a brief self-review note:
- List each AC from the plan (AC-1, AC-2 …) and mark it DONE or SKIPPED (with reason).
- List every file you modified.
- Note any deviations from the plan and why.
"""

REVIEW_PROMPT = """\
You are REVIEWING a code change for correctness, security, and whether it satisfies
the task. Repo context:
{context}

TASK:
{task}

PLAN (with acceptance criteria):
{plan}

THE ACTUAL CODE CHANGES UNDER REVIEW (working-tree diff vs the base branch):
{diff}

LATEST REAL GATE RESULTS / LOG EXCERPTS:
{gate_excerpt}

Review the diff ABOVE (it is the real change, do not say "the diff is missing"; it is
included). Check each acceptance criterion from the plan. Look for:
- Missed AC items
- Bugs, security issues, missed edge cases (especially idempotency on retry/redelivery,
  multi-order/webhook races, additive writes not gated on success)
- On a health platform: PHI handling, paths where a result/document is saved but never
  delivered
- Missing or insufficient tests for touched behaviour

Be concrete, cite file names and line numbers from the diff. Request changes ONLY for
concrete blockers: missed ACs, regressions, money/security/PHI risks, broken contracts,
or missing tests for touched behaviour. Do NOT block on generic polish or tooling pedantry.

When done, output your review, then on the VERY LAST line output your verdict as a
single standalone line in this exact form (do not quote or repeat the options anywhere
else in your response, and write nothing after this line):

VERDICT: APPROVED

(use CHANGES_REQUESTED instead of APPROVED if changes are needed)
"""

DEEP_REVIEW_PROMPT = """\
You are a senior staff engineer doing a DEEP FINAL REVIEW of a code change. Your job is
rigorous architecture, security, and correctness review. Repo context:
{context}

TASK:
{task}

PLAN (with acceptance criteria):
{plan}

THE ACTUAL CODE CHANGES UNDER REVIEW (working-tree diff vs the base branch):
{diff}

LATEST REAL GATE RESULTS / LOG EXCERPTS:
{gate_excerpt}

Focus areas (in priority order):
1. Architecture fit, does the change follow existing repo patterns and seams?
2. Idempotency / retry / redelivery / webhook races / duplicate events.
3. Security: authz, multi-tenancy, PHI exposure, injection, secrets handling.
4. Contract correctness: types, API shapes, DB migrations additive only.
5. Test coverage for the touched behaviour and any new edge cases.
6. Performance / N+1 / unnecessary DB or network calls.

Be concrete, cite file names and line numbers. Request changes ONLY for real blockers.

When done, output your review, then on the VERY LAST line output your verdict as a
single standalone line in this exact form (do not quote or repeat the options anywhere
else in your response, and write nothing after this line):

VERDICT: APPROVED

(use CHANGES_REQUESTED instead of APPROVED if changes are needed)
"""

FIX_PROMPT = """\
You are FIXING a code change to address reviewer feedback. Repo context:
{context}

TASK:
{task}

CURRENT WORKING-TREE DIFF (what has been changed so far):
{diff}

REVIEWER CHANGE-REQUESTS (numbered for clarity):
{changes}

For EACH change request above, respond with:
  Change Request N: [one-line quote of the request]
  Fix applied: [brief description of what you changed and in which file]

Then implement ALL fixes by editing the repository files. Rules:
- Use the diff above to understand what was already changed.
- Touch only what is needed to address the change requests.
- Keep the diff feature-only.
- Make the project's tests pass.
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tail_file(path: str | None, lines: int = 80) -> str:
    if not path:
        return ""
    p = Path(path)
    if not p.exists():
        return ""
    text = p.read_text(encoding="utf-8", errors="replace")
    return "\n".join(text.splitlines()[-lines:])


def _gate_excerpt(results: list, max_chars: int = 12000) -> str:
    parts = []
    for r in results or []:
        parts.append(f"## gate {r.get('name')} status={r.get('status')} rc={r.get('rc')}")
        tail = _tail_file(r.get("log"), 60)
        if tail:
            parts.append(tail)
    text = "\n\n".join(parts).strip() or "(no gate output captured yet)"
    if len(text) > max_chars:
        text = "[gate excerpt truncated to last %d chars]\n" % max_chars + text[-max_chars:]
    return text


def _number_changes(changes: str) -> str:
    """
    Prepend "Change Request N: " to each reviewer block so the fixer can
    reference them by number. Blocks are separated by "---" lines.
    """
    if not changes:
        return changes
    blocks = [b.strip() for b in changes.split("---") if b.strip()]
    numbered = []
    for i, block in enumerate(blocks, 1):
        numbered.append(f"Change Request {i}:\n{block}")
    return "\n\n---\n\n".join(numbered) if numbered else changes


@dataclass
class RunSummary:
    project: str
    task: str
    result: str = "UNKNOWN"
    stages: list = field(default_factory=list)
    run_dir: str = ""

    def add(self, **kw):
        self.stages.append(kw)


# ---------------------------------------------------------------------------
# Executor stage (implement / fix)
# ---------------------------------------------------------------------------

def _act_stage(cfg, role, prompt, project, run, log_name) -> bool:
    """
    Run an editing stage (implement/fix) with the role's primary executor.
    On rate-limit, retries once after a short backoff, then falls back to the
    role's configured `fallback` backend (if any) before giving up.
    """
    team = cfg.team(role)
    if not team:
        util.log(f"  {role}: no team configured", "WARN")
        return False
    backend = team[0]  # single-executor strategy (best_of_n is Stage 3)
    adapter = make_adapter(backend)
    if not adapter.can_act:
        util.log(f"  {role}: backend {backend.name} cannot edit files", "ERROR")
        return False
    util.log(f"  {role}: {backend.name} ({backend.kind}) editing working tree")
    res = adapter.act(prompt, cwd=project.repo, log_path=run / log_name)
    if res.ok:
        return True

    # Rate-limit handling: try a brief backoff then fallback backend
    if util.is_rate_limited(res):
        util.log(f"  {role}: {backend.name} is rate-limited", "WARN")
        backoff = int(cfg.role(role).get("rate_limit_backoff_s", 60))
        if backoff > 0:
            util.log(f"  {role}: waiting {backoff}s before retry/fallback...")
            time.sleep(backoff)
        # Try primary again after backoff
        res2 = adapter.act(prompt, cwd=project.repo, log_path=run / (log_name + ".retry"))
        if res2.ok:
            util.log(f"  {role}: retry after backoff succeeded")
            return True
        # Switch to fallback backend if configured
        fallback_name = cfg.role(role).get("fallback")
        if fallback_name:
            try:
                fb_backend = cfg.backend(fallback_name)
                fb_adapter = make_adapter(fb_backend)
                if fb_adapter.can_act:
                    util.log(f"  {role}: falling back to {fallback_name} ({fb_backend.kind})")
                    fb_res = fb_adapter.act(prompt, cwd=project.repo,
                                            log_path=run / (log_name + ".fallback"))
                    if fb_res.ok:
                        util.log(f"  {role}: fallback {fallback_name} succeeded")
                        return True
                    util.log(f"  {role}: fallback {fallback_name} also failed (rc={fb_res.rc})", "WARN")
                else:
                    util.log(f"  {role}: fallback {fallback_name} cannot act (chat-only)", "WARN")
            except Exception as e:
                util.log(f"  {role}: fallback error: {e}", "WARN")
        else:
            util.log(f"  {role}: no fallback configured; stage failed", "WARN")

    return False


def _capture_diff(repo: str, max_chars: int = 60000) -> str:
    """
    Capture the working-tree diff so chat-only reviewers can actually SEE the change.
    Tries `git diff` (unstaged+staged vs HEAD) plus a stat header. Truncates to keep the
    prompt within model context. This is the fix for the diff-blind cheap-jury problem.
    """
    rc, stat = util.sh("git --no-pager diff --stat HEAD; echo '---'; "
                       "git --no-pager diff HEAD; "
                       "git --no-pager diff --stat --cached HEAD 2>/dev/null; "
                       "git --no-pager diff --cached HEAD 2>/dev/null", cwd=repo, timeout=120)
    diff = stat.strip()
    if not diff:
        # fall back to diff vs the upstream base branch
        rc, diff = util.sh("git --no-pager diff origin/dev...HEAD 2>/dev/null || "
                           "git --no-pager diff origin/main...HEAD 2>/dev/null",
                           cwd=repo, timeout=120)
        diff = diff.strip()
    if not diff:
        return "(no diff captured, reviewer should note the change set could not be read)"
    if len(diff) > max_chars:
        diff = diff[:max_chars] + f"\n... [diff truncated at {max_chars} chars; review the portion shown]"
    return diff


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run_pipeline(cfg, project_name, task, plan_mode=None, max_review_iters=None,
                 skip_final=False, frontier=None, dry_run=False,
                 plan_override=None) -> RunSummary:
    """
    plan_override: if provided, skip the PLAN council stage and use this string
    directly as the plan. Useful with --plan-file to speed up re-runs.
    """
    project = cfg.project(project_name)
    run = util.run_dir(project.repo)
    summary = RunSummary(project=project_name, task=task, run_dir=str(run))
    ctx = project.context

    # Inject shared cross-agent memory so stateless OSS council benefits from
    # everything the other agents have learned.
    try:
        from . import memory as _mem
        mem_block = _mem.render_memory_block()
        if mem_block:
            ctx = mem_block + "\n" + ctx
            util.log(f"Injected shared memory ({len(mem_block)} chars) into stage context")
    except Exception as e:  # noqa: BLE001
        util.log(f"shared memory unavailable: {e}", "WARN")

    max_iters = max_review_iters if max_review_iters is not None else cfg.defaults.get("max_review_iters", 3)

    util.log(f"Conclave run -> project={project_name} repo={project.repo}")
    util.log(f"Run dir: {run}")

    if dry_run:
        return _dry_run(cfg, project, task, run, summary, max_iters, frontier)

    # ---- PLAN ----
    if plan_override:
        plan_text = plan_override
        how = "provided (--plan-file / plan_override)"
        util.log(f"STAGE: plan SKIPPED, using provided plan ({len(plan_text)} chars)")
        util.write(run / "plan_final.md", plan_text)
        summary.add(stage="plan", how=how, chars=len(plan_text), elapsed_s=0.0)
    else:
        util.log("STAGE: plan (council)")
        t0 = time.time()
        plan_team = cfg.team("plan")
        plan_prompt = PLAN_PROMPT.format(context=ctx, task=task)
        # Parallel drafter calls; synthesizer runs sequentially after (it needs the drafts)
        drafts = council.run_council_chat(plan_team, plan_prompt, project.repo, run, "plan",
                                          parallel=True)
        plan_text, how = council.synthesize(cfg, cfg.role("plan"), drafts, project.repo, run, plan_prompt)
        elapsed = round(time.time() - t0, 1)
        util.write(run / "plan_final.md", plan_text)
        util.log(f"  plan ready ({how}), {len(plan_text)} chars in {elapsed}s")
        summary.add(stage="plan", how=how, chars=len(plan_text), elapsed_s=elapsed)

    _finish(summary, run, partial=True)
    if not plan_text.strip():
        summary.result = "PLAN_FAILED"
        _finish(summary, run)
        return summary

    # ---- IMPLEMENT ----
    util.log("STAGE: implement")
    t0 = time.time()
    impl_prompt = IMPLEMENT_PROMPT.format(context=ctx, task=task, plan=plan_text)
    impl_ok = _act_stage(cfg, "implement", impl_prompt, project, run, "implement.log")
    elapsed = round(time.time() - t0, 1)
    summary.add(stage="implement", ok=impl_ok, elapsed_s=elapsed)
    _finish(summary, run, partial=True)

    # ---- post-impl GATE ----
    util.log("GATE: post-implement (lint/typecheck/unit)")
    t0 = time.time()
    gate_ok, gres = gates.run_gate_set(gates.POST_IMPL, project, run, "postimpl")
    gate_excerpt = _gate_excerpt(gres)
    elapsed = round(time.time() - t0, 1)
    summary.add(stage="gate_postimpl", passed=gate_ok, results=gres, elapsed_s=elapsed)

    # ---- REVIEW <-> FIX loop ----
    review_cfg = cfg.role("review")
    quorum = review_cfg.get("quorum", "majority")
    converged = False

    for it in range(1, max_iters + 1):
        # S2: Gate-before-jury skip. When gates are RED there is no point sending a
        # broken build to 6 jury reviewers, they will all CHANGES_REQUESTED for
        # mechanical reasons, wasting 6 HTTP calls and ~30s per model. Go straight
        # to fix and let the fixer address gate failures directly.
        if not gate_ok:
            util.log(f"STAGE: review iter {it}/{max_iters} SKIPPED, gates RED, going straight to fix")
            verdicts = {}
            passed = False
            changes = "(Gates are RED, fix all failing gates before review.)"
            summary.add(stage=f"review_iter{it}", verdicts=verdicts, passed=passed,
                        jury_skipped=True, reason="gates_red", elapsed_s=0.0)
        else:
            util.log(f"STAGE: review (jury) iteration {it}/{max_iters}")
            t0 = time.time()
            rev_prompt = REVIEW_PROMPT.format(context=ctx, task=task, plan=plan_text,
                                              diff=_capture_diff(project.repo),
                                              gate_excerpt=gate_excerpt)
            reviews = council.run_council_chat(cfg.team("review"), rev_prompt,
                                               project.repo, run, f"review_iter{it}",
                                               parallel=True)
            passed, verdicts, changes = council.vote(reviews, quorum=quorum)
            elapsed = round(time.time() - t0, 1)
            util.log(f"  verdicts: {verdicts} -> quorum({quorum})={'PASS' if passed else 'CHANGES'} in {elapsed}s")
            summary.add(stage=f"review_iter{it}", verdicts=verdicts, passed=passed, elapsed_s=elapsed)

        if passed and gate_ok:
            converged = True
            break

        # ---- FIX ----
        util.log(f"STAGE: fix iteration {it}")
        t0 = time.time()
        merged_changes = _number_changes(changes) if changes else "Gate failures present; make all gates green."
        if not gate_ok:
            merged_changes += "\n\n(Build/test gates are RED, fix them first.)"
        fix_prompt = FIX_PROMPT.format(context=ctx, task=task,
                                       diff=_capture_diff(project.repo),
                                       changes=merged_changes)
        fix_ok = _act_stage(cfg, "fix", fix_prompt, project, run, f"fix_iter{it}.log")
        gate_ok, gres = gates.run_gate_set(gates.POST_IMPL, project, run, f"postfix{it}")
        gate_excerpt = _gate_excerpt(gres)
        elapsed = round(time.time() - t0, 1)
        summary.add(stage=f"fix_iter{it}", ok=fix_ok, gate_passed=gate_ok,
                    results=gres, elapsed_s=elapsed)

    if not converged:
        summary.result = "NOT_CONVERGED"
        util.log("Review loop did not converge within iter cap.", "WARN")
        _finish(summary, run)
        return summary

    # ---- FINAL GATE ----
    if not skip_final:
        util.log("GATE: final (unit/e2e/build)")
        t0 = time.time()
        final_ok, fres = gates.run_gate_set(gates.FINAL, project, run, "final")
        if project.stress_cmd:
            sres = gates.run_gate("stress", project.stress_cmd, project.repo, run, "final")
            fres.append(sres)
            final_ok = final_ok and (sres["status"] != "FAIL")
        elapsed = round(time.time() - t0, 1)
        summary.add(stage="gate_final", passed=final_ok, results=fres, elapsed_s=elapsed)
        if not final_ok:
            summary.result = "FINAL_GATE_FAILED"
            _finish(summary, run)
            return summary

    # ---- FINAL DEEP REVIEW (optional) ----
    fr_cfg = cfg.role("final_review")
    use_frontier = frontier if frontier is not None else fr_cfg.get("enabled", False)
    if use_frontier and cfg.team("final_review"):
        util.log("STAGE: final deep review")
        t0 = time.time()
        rev_prompt = DEEP_REVIEW_PROMPT.format(context=ctx, task=task, plan=plan_text,
                                          diff=_capture_diff(project.repo),
                                          gate_excerpt=gate_excerpt)
        freviews = council.run_council_chat(cfg.team("final_review"), rev_prompt,
                                            project.repo, run, "frontier", parallel=False)
        fpassed, fverdicts, fchanges = council.vote(freviews, quorum=fr_cfg.get("quorum", "all"))
        elapsed = round(time.time() - t0, 1)
        summary.add(stage="final_deep_review", verdicts=fverdicts, passed=fpassed, elapsed_s=elapsed)
        util.log(f"  final deep review verdicts: {fverdicts}")
        if not fpassed:
            # one corrective fix pass on the cheap executor, then re-run the deep reviewer once.
            util.log("STAGE: deep-review-triggered fix")
            t0 = time.time()
            fix_prompt = FIX_PROMPT.format(context=ctx, task=task,
                                           diff=_capture_diff(project.repo),
                                           changes=_number_changes(fchanges))
            _act_stage(cfg, "fix", fix_prompt, project, run, "fix_frontier.log")
            gate_ok2, gres2 = gates.run_gate_set(gates.POST_IMPL, project, run, "postfrontier")
            gate_excerpt = _gate_excerpt(gres2)
            elapsed = round(time.time() - t0, 1)
            summary.add(stage="fix_frontier", gate_passed=gate_ok2, results=gres2, elapsed_s=elapsed)
            if not gate_ok2:
                summary.result = "FINAL_DEEP_REVIEW_FIX_GATE_FAILED"
                _finish(summary, run)
                return summary
            util.log("STAGE: deep review re-review")
            t0 = time.time()
            rev_prompt = DEEP_REVIEW_PROMPT.format(context=ctx, task=task, plan=plan_text,
                                              diff=_capture_diff(project.repo),
                                              gate_excerpt=gate_excerpt)
            freviews2 = council.run_council_chat(cfg.team("final_review"), rev_prompt,
                                                 project.repo, run, "frontier_rereview",
                                                 parallel=False)
            fpassed2, fverdicts2, _ = council.vote(freviews2, quorum=fr_cfg.get("quorum", "all"))
            elapsed = round(time.time() - t0, 1)
            summary.add(stage="final_deep_rereview", verdicts=fverdicts2, passed=fpassed2, elapsed_s=elapsed)
            if not fpassed2:
                summary.result = "FINAL_DEEP_REVIEW_NOT_APPROVED"
                _finish(summary, run)
                return summary

    summary.result = "CONVERGED"
    _finish(summary, run)
    return summary


# ---------------------------------------------------------------------------
# Dry-run
# ---------------------------------------------------------------------------

def _dry_run(cfg, project, task, run, summary, max_iters, frontier):
    util.log("DRY RUN, resolved configuration (no execution):")
    lines = [f"# Conclave dry-run\nproject: {project.name}\nrepo: {project.repo}\ntask: {task}\n"]
    for role in ["plan", "implement", "review", "fix", "final_review"]:
        team = cfg.team(role)
        rc = cfg.role(role)
        names = ", ".join(f"{b.name}[{b.kind}{':'+b.model if b.model else ''}]" for b in team) or "(none)"
        agg = rc.get("aggregate") or rc.get("strategy") or "-"
        extra = f" quorum={rc.get('quorum')}" if rc.get("quorum") else ""
        line = f"role {role:13} team=[{names}] aggregate={agg}{extra}"
        util.log("  " + line)
        lines.append(line)
    lines.append(f"\nmax_review_iters: {max_iters}")
    lines.append("gates:")
    for g, c in project.gates.items():
        lines.append(f"  {g:10} -> {c if c else 'SKIP'}")
    lines.append(f"  stress     -> {project.stress_cmd if project.stress_cmd else 'SKIP'}")
    util.write(run / "dry_run.md", "\n".join(lines))
    summary.result = "DRY_RUN"
    summary.add(stage="dry_run", file=str(run / "dry_run.md"))
    print("\n".join(lines))
    return summary


# ---------------------------------------------------------------------------
# Summary writer
# ---------------------------------------------------------------------------

def _finish(summary: RunSummary, run: Path, partial: bool = False):
    util.write(run / "summary.json", json.dumps(summary.__dict__, indent=2, default=str))
    status_line = f"**{summary.result}**" + (" _(in progress)_" if partial else "")
    md = [f"# Conclave run summary",
          f"- project: {summary.project}",
          f"- result: {status_line}",
          f"- run dir: {summary.run_dir}",
          f"- task: {summary.task}",
          "",
          "## Stages"]
    timing_rows = []
    for s in summary.stages:
        md.append(f"- {s}")
        if "elapsed_s" in s and s.get("stage"):
            timing_rows.append((s["stage"], s["elapsed_s"]))

    # S4: timing table
    if timing_rows:
        md.append("")
        md.append("## Stage timing")
        md.append("| Stage | Elapsed (s) |")
        md.append("|---|---|")
        total = 0.0
        for stage_name, elapsed in timing_rows:
            md.append(f"| {stage_name} | {elapsed} |")
            total += elapsed or 0
        md.append(f"| **TOTAL** | **{round(total, 1)}** |")

    util.write(run / "summary.md", "\n".join(md))
    if not partial:
        util.log(f"RESULT: {summary.result}")
        util.log(f"Summary: {run / 'summary.md'}")
