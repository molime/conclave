"""
cli.py, `conclave` command line.

  conclave doctor                 health-check every backend (CLI/HTTP/keys)
  conclave backends               list configured backends and roles
  conclave dry-run --project X --task "..."   print resolved plan, no execution
  conclave run --project X --task "..."   [--task-file PATH] [--plan-file PATH]
               [--frontier] [--plan-mode ...] [--max-review-iters N]

2026-07-05 S5: added --task-file and --plan-file flags so Conclave can be
launched fully headless without keeping Hermes chat open during the run.

  --task-file PATH   Read the task from a file instead of the command line.
                     Avoids shell quoting failures on Windows with long prompts.
                     Mutually exclusive with --task.

  --plan-file PATH   Skip the PLAN council stage and use this pre-written plan.
                     Useful for re-running fix iterations on a known-good plan or
                     for injecting a plan written interactively in Hermes chat.
                     Mutually exclusive with the internal plan council run.
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

from . import config, pipeline, util
from .adapters import make_adapter


def cmd_doctor(cfg, args):
    print("Conclave doctor, backend reachability\n")
    seen = {}
    for name in cfg.backends:
        b = cfg.backend(name)
        try:
            ok, detail = make_adapter(b).health()
        except Exception as e:  # noqa: BLE001
            ok, detail = False, f"error: {e}"
        mark = "OK " if ok else "XX "
        print(f"  [{mark}] {name:16} {b.kind:13} {detail}")
        seen[name] = ok
    print("\nRoles:")
    for role in ["plan", "implement", "review", "fix", "final_review"]:
        team = [b.name for b in cfg.team(role)]
        states = ["OK" if seen.get(n) else "XX" for n in team]
        usable = all(seen.get(n) for n in team) if team else False
        if team:
            print(f"  {role:13} {'usable' if usable else 'DEGRADED'}: "
                  + ", ".join(f"{n}({s})" for n, s in zip(team, states)))
        else:
            print(f"  {role:13} (no team)")
    print("\nLegend: OK reachable / XX unreachable-or-missing-key.")
    return 0


def cmd_backends(cfg, args):
    print("Backends:")
    for name in cfg.backends:
        b = cfg.backend(name)
        print(f"  {name:16} kind={b.kind:13} model={b.model or '-'}")
    print("\nRoles:")
    for role, rc in cfg.roles.items():
        print(f"  {role:13} {rc}")
    print("\nProjects:")
    for p in cfg.projects:
        print(f"  {p}")
    return 0


def _resolve_task_and_plan(args) -> tuple[str, str | None]:
    """
    Return (task_string, plan_override_or_None).
    Validates mutual-exclusion of --task / --task-file and reads files as needed.
    """
    # ---- task ----
    if args.task and args.task_file:
        print("ERROR: --task and --task-file are mutually exclusive.", file=sys.stderr)
        sys.exit(2)
    if args.task_file:
        p = Path(args.task_file)
        if not p.exists():
            print(f"ERROR: --task-file not found: {p}", file=sys.stderr)
            sys.exit(2)
        task = p.read_text(encoding="utf-8", errors="replace").strip()
        util.log(f"Task loaded from file: {p} ({len(task)} chars)")
    elif args.task:
        task = args.task
    else:
        print("ERROR: either --task or --task-file is required.", file=sys.stderr)
        sys.exit(2)

    # ---- plan override ----
    plan_override = None
    if getattr(args, "plan_file", None):
        p = Path(args.plan_file)
        if not p.exists():
            print(f"ERROR: --plan-file not found: {p}", file=sys.stderr)
            sys.exit(2)
        plan_override = p.read_text(encoding="utf-8", errors="replace").strip()
        util.log(f"Plan loaded from file: {p} ({len(plan_override)} chars)")

    return task, plan_override


def cmd_run(cfg, args):
    task, plan_override = _resolve_task_and_plan(args)
    summary = pipeline.run_pipeline(
        cfg,
        project_name=args.project,
        task=task,
        plan_mode=args.plan_mode,
        max_review_iters=args.max_review_iters,
        skip_final=args.skip_final,
        frontier=args.frontier,
        dry_run=args.dry_run,
        plan_override=plan_override,
    )
    return 0 if summary.result in ("CONVERGED", "DRY_RUN", "FRONTIER_CHANGES_APPLIED") else 2


def main(argv=None):
    p = argparse.ArgumentParser(prog="conclave", description="Multi-model agent orchestration")
    p.add_argument("--config", help="path to conclave.yaml")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("doctor", help="health-check backends")
    sub.add_parser("backends", help="list backends/roles/projects")

    def add_run_args(sp):
        sp.add_argument("--project", required=True)
        # Task: one of --task (inline string) or --task-file (path to file)
        task_group = sp.add_mutually_exclusive_group()
        task_group.add_argument("--task", default=None,
                                help="task description (inline string)")
        task_group.add_argument("--task-file", default=None, metavar="PATH",
                                help="path to file containing the task description")
        sp.add_argument("--plan-file", default=None, metavar="PATH",
                        help="skip PLAN stage; use this pre-written plan file instead")
        sp.add_argument("--plan-mode", default=None)
        sp.add_argument("--max-review-iters", type=int, default=None)
        sp.add_argument("--skip-final", action="store_true")
        sp.add_argument("--frontier", dest="frontier", action="store_true", default=None,
                        help="force-enable frontier final review")
        sp.add_argument("--no-frontier", dest="frontier", action="store_false",
                        help="force-disable frontier final review")

    rp = sub.add_parser("run", help="run the full pipeline")
    add_run_args(rp)
    rp.set_defaults(dry_run=False)

    dp = sub.add_parser("dry-run", help="print resolved plan without executing")
    add_run_args(dp)
    dp.set_defaults(dry_run=True)

    args = p.parse_args(argv)
    cfg = config.load(args.config)

    if args.cmd == "doctor":
        return cmd_doctor(cfg, args)
    if args.cmd == "backends":
        return cmd_backends(cfg, args)
    if args.cmd in ("run", "dry-run"):
        return cmd_run(cfg, args)
    p.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
