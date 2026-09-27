# Conclave, Multi-Model Agent Orchestration System

> A global, machine-portable system that reproduces (and aims to exceed) frontier-model
> output quality using a coordinated team of cheaper open / semi-open models -
> planners, executors, reviewers and a jury, looping until everything is green.

---

## 1. Vision & goal

You currently get high-quality results from a hand-driven loop: Opus/Codex plan →
Kimi implements → Opus/Codex review → Kimi fixes (the `multi-model-dev-pipeline`).
That works, but the *deciding* brains are expensive frontier models.

**Conclave** generalizes that proven loop into a configurable, model-agnostic engine
where:

- The **planner** and **reviewer** roles are filled by a **council/jury** of cheaper
  models (Kimi, GLM, Qwen, DeepSeek) that deliberate and converge on a decision.
- The **executor** role is filled by a **team of open-source coding models** (via
  aider + OpenRouter/Ollama/LM Studio, or Kimi), looping until gates pass.
- An optional **frontier escalation** step (Opus/Codex) does a final sanity review and,
  if it requests changes, re-triggers the whole cheap loop.
- **Quality gates** (lint, typecheck, unit, e2e, stress) are hard, non-fakeable
  checkpoints between every stage, identical philosophy to the existing pipeline.

The thesis: *a well-orchestrated council of cheap models + rigorous gates ≈ or >
a single frontier model, at a fraction of the cost.*

### Design principles

1. **Build on what works.** This reuses the exact stage/gate/loop architecture already
   proven in `multi-model-dev-pipeline` (native-CLI dispatch, VERDICT parsing, gate exit
   codes, working-tree edits). Conclave adds: pluggable backends, council aggregation,
   OSS-execution teams, and portable bootstrap.
2. **Backends, not providers.** A *role* (plan/implement/review/fix) maps to a *team* of
   *backends*. A backend is one concrete way to reach one model (a CLI or an HTTP
   endpoint). Adding a model = adding a backend entry in YAML. No code changes.
3. **Gates are sacred.** Nothing advances past a gate whose real exit code is non-zero.
   No stage is ever reported "passed" without a 0 exit. Nothing is faked.
4. **Zero hard dependencies.** Core engine is pure Python 3.10+ stdlib (urllib, json,
   yaml). HTTP adapters do not need `requests`. This keeps it trivially portable to a
   fresh box or cloud instance.
5. **Portable & replicable.** One `install.sh` (Linux/macOS) / `install.ps1` (Windows)
   provisions the whole system; one `aws-userdata.sh` stands it up on a fresh EC2 box.
   Config-driven so the same engine targets any repo on any machine.
6. **Honest about limits.** Parallel executors on one working tree collide, so "many
   agents executing at once" is implemented correctly as **best-of-N in isolated git
   worktrees with a judge** (Stage 3), not naive concurrent edits.

---

## 2. Core concepts

### 2.1 Backend
One concrete model access path. Two families:

| Family | `kind` values | Can edit files? | Used for |
|--------|---------------|-----------------|----------|
| **CLI agent** | `cli_hermes`, `cli_codex`, `cli_kimi`, `cli_claude`, `cli_aider` | yes (agentic) | implement, fix (also plan/review) |
| **HTTP chat** | `http_openai`, `http_ollama` | no (text only) | plan, review, jury, synthesize |

- `cli_aider` is the key to OSS *execution*: aider speaks any `--model`
  (`openrouter/...`, `ollama/...`, `lm_studio/...`) and edits the repo headlessly.
- `http_openai` covers OpenRouter, LM Studio, and any OpenAI-compatible base_url.
- `http_ollama` covers a local Ollama daemon.

### 2.2 Role → Team
Each role is filled by one or more backends:

```
plan:    [council of planners]        → aggregate: synthesize
implement: [one executor]             → strategy: single (Stage 1/2)
                                        best-of-N worktrees (Stage 3)
review:  [jury of reviewers]          → aggregate: vote (quorum)
fix:     [one executor]               → strategy: single
final_review (optional): [opus, codex]→ aggregate: vote
```

### 2.3 Aggregation strategies
- **synthesize** (planning): every planner drafts a plan; a synthesizer backend merges
  them into one canonical `plan_final.md`. Single planner → pass-through.
- **vote** (review/jury): every reviewer emits a trailing `VERDICT: APPROVED` or
  `VERDICT: CHANGES_REQUESTED`. Quorum `all` → pass only if unanimous; `majority` →
  pass if >50%. Collected change-requests feed the next fix stage.

### 2.4 The loop (engine)

```
ask plan-mode (or config)  ->  PLAN  (council synthesize)
                           ->  IMPLEMENT (executor edits working tree)
                           ->  GATE: lint + typecheck + unit      [must pass]
                           ->  REVIEW (jury vote)
                           ->  FIX (executor addresses every change-request)
                           ->  re-GATE + re-REVIEW  (loop until quorum APPROVES
                                                     or max-iters)
                           ->  FINAL GATE: unit + e2e + stress     [must pass]
                           ->  [optional] FRONTIER REVIEW (opus/codex)
                                  CHANGES_REQUESTED -> reopen cheap loop
                           ->  summary.md / summary.json
```

Every stage spawns a real subprocess (CLI) or HTTP call, captured to a per-stage log in
`<repo>/.conclave-runs/<timestamp>/`. Reviewers' verdicts are grepped from their last
`VERDICT:` line. The loop converges only on real quorum, never on iteration-cap.

---

## 3. The model map (default, editable in conclave.yaml)

| Role | Default team | Backend kind | Auth |
|------|--------------|--------------|------|
| plan | Kimi (chat) | `http_openai` via OpenRouter `moonshotai/kimi-k2` | OPENROUTER_API_KEY |
| implement | Kimi (CLI) | `cli_kimi` | `~/.kimi-code` OAuth |
| review (jury) | Kimi + GLM-4.6 + DeepSeek | `http_openai` via OpenRouter | OPENROUTER_API_KEY |
| fix | Kimi (CLI) | `cli_kimi` | `~/.kimi-code` OAuth |
| final_review (opt) | Opus + Codex | `cli_hermes` + `cli_codex` | Anthropic / ChatGPT login |

OSS execution alternatives for `implement`/`fix` (swap in via config):
`cli_aider` with `openrouter/qwen/qwen-2.5-coder-32b-instruct`,
`openrouter/deepseek/deepseek-chat`, `ollama/qwen2.5-coder:32b`, etc.

Rationale: matches your stated intent, **Kimi plans & reviews (as a council), OSS
models execute, frontier does a final pass that can re-trigger the cheap loop.**

---

## 4. Why these specific tools

- **aider**, the most scriptable headless agentic *coder* that natively speaks
  OpenRouter, Ollama and LM Studio. `aider --model X --yes --message "..."` is a
  one-shot non-interactive edit. This is how arbitrary OSS models "execute."
- **OpenRouter**, single API key → GLM, Qwen, DeepSeek, Kimi, Llama, etc. One key,
  many models, pay-per-token, cheap. Lowest-friction way to field a council.
- **Ollama / LM Studio**, fully local, zero marginal cost, for offline/private runs or
  when you have GPU. Same engine, just different backend `kind`.
- **Kimi / Codex / Claude CLIs**, already authed on this machine; reused as-is.

---

## 5. Distillation & fine-tuned models (Stage 4, optional advanced track)

You mentioned distillation (Unsloth) and pre-tuned GGUF models (e.g. the
gemma-coder-fable-composer build). Conclave treats these as **just another backend**:

- A fine-tuned GGUF served by Ollama or LM Studio → `http_ollama` / `http_openai`
  backend. Drop it into any role's team. No engine change.
- **Distillation pipeline** (Stage 4): capture the frontier loop's (prompt → plan →
  review) traces from `.conclave-runs/`, format as an SFT dataset, fine-tune a small
  OSS model with Unsloth so it learns *your* planning/review style, then serve it as a
  local backend and promote it into the council. This is how the council gets cheaper
  *and better over time*, it learns from its own frontier-supervised history.

This is deferred to Stage 4 because it needs GPU and earns its keep only after Stages
1–3 are generating good traces.

---

## 6. Repository layout

```
conclave/
  DESIGN.md                 # this file
  ROADMAP.md                # stages + detailed per-stage todos
  README.md                 # quickstart + user checklist
  conclave.yaml             # the model map / role-team config (the system's brain)
  .env.example              # keys you must provide
  bin/
    conclave                # POSIX launcher (bash)
    conclave.cmd            # Windows launcher
  conclave/                 # the Python package (stdlib-only engine)
    __init__.py
    config.py               # YAML load + backend/role resolution
    util.py                 # git-bash sh(), logging, env, paths
    adapters/
      __init__.py           # registry + factory
      base.py               # Adapter ABC, AdapterResult
      cli.py                # cli_hermes/codex/kimi/claude/aider
      http.py               # http_openai / http_ollama (urllib)
    council.py              # synthesize + vote aggregation
    gates.py                # gate runner (exit-code truth)
    pipeline.py             # the stage loop / engine
    cli.py                  # argparse entrypoint: run/doctor/backends/dry-run
  bootstrap/
    install.sh              # Linux/macOS provisioner
    install.ps1             # Windows provisioner
    aws-userdata.sh         # EC2 cloud-init provisioner
  examples/demo/            # tiny repo with a real unit gate, for smoke tests
```

---

## 7. What stays the same vs the existing pipeline (so we don't regress)

- Native-CLI dispatch through git-bash; `HERMES_PIPELINE_ACTIVE=1` exported so the
  enforcement hooks never block the executor's own edits.
- Codex: `codex exec --skip-git-repo-check` (+`--full-auto` for edits); never `-m gpt-5-codex`.
- Kimi: `kimi -p '<prompt>'`, no `-y` (can't combine with `-p`).
- Gates run the project's real commands; `None` command → SKIP(pass).
- Per-run timestamped scratch dir; `summary.md`/`summary.json`; VERDICT grep.
- Windows: every subprocess via git-bash, `errors="replace"` decoding, `PYTHONUTF8=1`.

## 8. What is new in Conclave

- Backends are data (YAML), not hardcoded branches.
- Roles are *teams*; councils deliberate and vote/synthesize.
- HTTP adapters (OpenRouter/Ollama/LM Studio) for cheap plan/review/jury.
- aider executor → arbitrary OSS models can edit code.
- Portable bootstrap for any machine + AWS.
- (Stage 3) best-of-N execution in isolated worktrees with a judge.
- (Stage 4) self-distillation from run traces.

---

## 9. Commercialization notes (parked, per your message)

The engine is intentionally repo-agnostic and config-driven, so productizing later means
wrapping it in: a hosted control plane (queue runs, store traces, bill per run), a
secrets vault per tenant, and a results dashboard. None of that is built now, but the
clean `config → roles → backends → gates` seam is what makes it possible. We revisit
after it works well locally.
