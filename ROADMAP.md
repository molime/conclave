# Conclave, Roadmap & per-stage TODO

Stages are incremental. Each ships something runnable. Stage 1 is the MVP that already
gives you "cheap council + OSS execution + gates" on a real repo. Later stages add
parallelism, frontier escalation, portability polish, and self-distillation.

Legend: [x] done  [ ] todo  [~] optional/advanced

---

## Stage 0, Foundations (scaffolding)  ✅ target: engine skeleton imports & runs

- [x] Repo scaffold, .gitignore, DESIGN.md, ROADMAP.md
- [x] `conclave/config.py`, load conclave.yaml, resolve roles→teams→backends, env expand
- [x] `conclave/util.py`, git-bash `sh()`, run-dir, logging, env helpers
- [x] `conclave/adapters/base.py`, Adapter ABC + AdapterResult
- [x] `conclave/adapters/cli.py`, cli_hermes / cli_codex / cli_kimi / cli_claude / cli_aider
- [x] `conclave/adapters/http.py`, http_openai (OpenRouter/LM Studio) + http_ollama (stdlib urllib)
- [x] `conclave/adapters/__init__.py`, registry + `make_adapter()`
- [x] `conclave/council.py`, synthesize() + vote() aggregation
- [x] `conclave/gates.py`, gate runner, exit-code truth, None→skip
- [x] `conclave/pipeline.py`, the stage loop engine
- [x] `conclave/cli.py`, `conclave run|doctor|backends|dry-run`
- [x] `bin/conclave` + `bin/conclave.cmd` launchers
- [x] `conclave.yaml` default model map
- [x] `.env.example`

## Stage 1, MVP: cheap council + single OSS/Kimi executor + gates  ← PRIMARY DELIVERABLE

Goal: on any wired repo, run plan(council)→implement(executor)→gate→review(jury)→fix→
loop→final-gate, entirely on cheap models, with real gates.

- [x] `doctor` reports which backends are reachable (CLI on PATH / HTTP ping / key present)
- [x] `dry-run` prints every resolved stage command + gate without executing
- [x] PROJECTS-style repo registry in conclave.yaml (repo path + gate commands)
- [x] examples/demo repo with a real `npm test`/python unit gate for smoke testing
- [ ] USER: provide OPENROUTER_API_KEY (or run fully local via Ollama), see README
- [ ] USER: `pipx install aider-chat` (or pip) for the OSS executor backend
- [x] Live smoke: full loop validated end-to-end on demo with all-Kimi config
      (plan→implement→gate→review⇄fix→final). Surfaced + fixed a verdict-parse
      false-negative (menu-echo). Parser hardened + unit-tested (tests/test_council.py).
      NOTE: validated using the already-authed Kimi CLI for every role, so the engine
      is proven WITHOUT the OpenRouter key, the two USER items above unlock the
      *cheap multi-model council + OSS execution*, but the machinery already works.

## Stage 2, Frontier escalation + enforcement parity

- [x] `final_review` role: opus + codex vote; CHANGES_REQUESTED reopens cheap loop
- [x] HERMES_PIPELINE_ACTIVE export so existing enforcement hooks don't block executors
- [ ] cost_report parity (reuse existing cost_report.py patterns; OpenRouter usage via API)
- [ ] Wire a real project (dumbo/atriax/traveltise) behind a feature branch & compare
      output quality vs the current opus/codex+kimi pipeline

## Stage 3, Parallel execution done correctly (best-of-N)

- [ ] `implement` strategy `best_of_n`: N executors each in an isolated `git worktree`
- [ ] judge backend scores the N candidate diffs against the plan + gate results
- [ ] winning worktree's diff is applied to the main tree; losers discarded
- [ ] concurrency guard: never two runs on the same worktree (lockfile)

## Stage 4, Self-distillation (optional, needs GPU)  [~]

- [ ] trace exporter: `.conclave-runs/*` → SFT JSONL (prompt→plan, diff→review)
- [ ] Unsloth fine-tune recipe for a small planner/reviewer on your traces
- [ ] serve tuned GGUF via Ollama; promote into the council; A/B vs baseline

## Stage 5, Portability & productization seams  [~]

- [x] bootstrap/install.sh, install.ps1, aws-userdata.sh
- [ ] one-command cloud bring-up validated on a fresh EC2 instance
- [ ] (later) hosted control plane, per-tenant secrets, run dashboard, billing

---

## Immediate next actions for YOU (the human)

1. Get an OpenRouter API key: https://openrouter.ai/keys → put it in `conclave/.env`
   as `OPENROUTER_API_KEY=...`  (this single key unlocks GLM, Qwen, DeepSeek, Kimi).
  , OR, install Ollama (https://ollama.com) and `ollama pull qwen2.5-coder:7b` to run
   100% local with no key.
2. Install the OSS executor: `pipx install aider-chat` (or `pip install aider-chat`).
3. (already have) codex, kimi, claude, hermes authed, reused as-is.
4. Tell me which you did; I'll run `conclave doctor` then a live smoke test.
