# Conclave

A global, machine-portable system that reproduces (and aims to exceed) frontier-model
output quality using a **coordinated team of cheaper models**, planners, executors,
reviewers and a jury, looping until every quality gate is green.

It generalizes the proven `opus/codex plan → kimi implement → opus/codex review → kimi
fix` loop into a config-driven engine where **cheap models (Kimi, GLM, Qwen, DeepSeek)
do the planning/review/execution**, with an optional frontier (Opus/Codex) final pass
that can re-trigger the cheap loop.

```
PLAN (council, synthesize) → IMPLEMENT (executor) → GATE(lint/type/unit)
  → REVIEW (jury, vote) ⇄ FIX  [loop until quorum] → FINAL GATE(e2e/stress)
  → [optional] FRONTIER REVIEW (reopens cheap loop on changes)
```

See `DESIGN.md` for the full architecture and `ROADMAP.md` for the staged plan.

## Quickstart

```bash
# 1. install (idempotent)
bash bootstrap/install.sh           # Windows: powershell -File bootstrap\install.ps1

# 2. add your key (one key = GLM + Qwen + DeepSeek + Kimi)
cp .env.example .env && edit .env   # set OPENROUTER_API_KEY
#   , or run 100% local: install Ollama + `ollama pull qwen2.5-coder:7b`

# 3. install the OSS executor
pipx install aider-chat

# 4. verify
export PATH="$PWD/bin:$PATH"
conclave doctor                     # shows which backends are reachable

# 5. run
conclave dry-run --project demo --task "Add reverse_words() with tests"   # preview
conclave run     --project demo --task "Add reverse_words() with tests"   # execute
conclave run     --project demo --task "..." --frontier                   # + Opus/Codex final pass
```

## Configure (the brain: `conclave.example.yaml`)

- **backends**: each is one model access path (a CLI or an HTTP endpoint).
- **roles**: `plan / implement / review / fix / final_review`, each filled by a *team*.
- **projects**: repo path + the real gate commands (lint/typecheck/unit/e2e/build).

Change which model does what by editing the `team:` lists, no code changes.
Want OSS execution? Point `implement`/`fix` at `exec_qwen_or` (aider+OpenRouter) or
`exec_qwen_local` (aider+Ollama). Want a bigger jury? Add backends to `review.team`.

## What you need

| Need | How | Required? |
|------|-----|-----------|
| **GPU council, no key, no billing admin** | `ollama signin` → write a config with `:cloud` models | **recommended** |
| Cheap council (GLM/Qwen/DeepSeek/Kimi) | `OPENROUTER_API_KEY` in `.env` | alternative |
| Fully-local council/exec | Ollama + `ollama pull qwen2.5-coder:7b` | alternative |
| OSS code execution | `pipx install aider-chat` | recommended |
| Kimi/Codex/Claude/Opus stages | bring your own CLI login | optional |

### Ollama Cloud (the easy path)

If you have an ollama.com account but no OpenRouter billing, use **Ollama Cloud**: GPU-backed
models billed through your own account, no API key.

```bash
ollama signin                                    # approve in browser
python -m conclave.cli --config conclave.example.yaml doctor
python -m conclave.cli --config conclave.example.yaml run --project demo --task "..."
```

`conclave.example.yaml` ships fully local (qwen2.5-coder:7b council + jury over Ollama,
Kimi CLI as executor). Point `base_url` at your Ollama daemon (or a `:cloud` model after
`ollama signin`) and edit the `team:` lists to taste. The cloud catalog rotates, so
re-probe model names if one 404s. Free tier has rate limits; paid plan at ollama.com/settings.

## Portability

- `bootstrap/install.sh` / `install.ps1`, provision any laptop.
- `bootstrap/aws-userdata.sh`, paste into EC2 user-data for a headless cloud node
  (defaults to a key-free local Ollama council).
- Engine is pure Python stdlib + pyyaml, no heavy deps, trivially relocatable.

## Honesty guarantees

A stage/gate is reported "passed" only when its real exit code is 0. The review loop
converges only on a real quorum verdict, never on hitting the iteration cap. Every stage
is logged to `<repo>/.conclave-runs/<timestamp>/`.
