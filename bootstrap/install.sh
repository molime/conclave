#!/usr/bin/env bash
# =============================================================================
# Conclave installer (Linux / macOS). Idempotent. Run from anywhere.
#   curl -fsSL .../install.sh | bash      OR      bash bootstrap/install.sh
# =============================================================================
set -euo pipefail

CONCLAVE_HOME="${CONCLAVE_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
echo "==> Conclave home: $CONCLAVE_HOME"

# 1. Python 3.10+
if ! command -v python3 >/dev/null 2>&1; then
  echo "!! python3 not found. Install Python 3.10+ first." >&2; exit 1
fi
PYV=$(python3 -c 'import sys;print("%d.%d"%sys.version_info[:2])')
echo "==> python3 $PYV"

# 2. pyyaml (only hard dep of the engine)
python3 -c 'import yaml' 2>/dev/null || {
  echo "==> installing pyyaml"
  python3 -m pip install --user pyyaml || pipx runpip pyyaml install pyyaml || true
}

# 3. OSS executor: aider (optional but recommended)
if ! command -v aider >/dev/null 2>&1; then
  echo "==> aider not found. Installing via pipx (recommended)."
  if command -v pipx >/dev/null 2>&1; then
    pipx install aider-chat || echo "!! pipx install failed; run: pip install aider-chat"
  else
    echo "!! pipx not found. Install it (python3 -m pip install --user pipx) then: pipx install aider-chat"
  fi
fi

# 4. jq (nice-to-have for inspecting summary.json)
command -v jq >/dev/null 2>&1 || echo "(optional) install jq to pretty-print run summaries"

# 5. Ollama (optional, for fully-local council/execution)
if ! command -v ollama >/dev/null 2>&1; then
  echo "(optional) For 100% local runs: install Ollama from https://ollama.com"
  echo "           then: ollama pull qwen2.5-coder:7b"
fi

# 6. .env
if [ ! -f "$CONCLAVE_HOME/.env" ]; then
  cp "$CONCLAVE_HOME/.env.example" "$CONCLAVE_HOME/.env"
  echo "==> created .env, edit it and add OPENROUTER_API_KEY (or configure Ollama)"
fi

# 7. PATH hint
echo "==> add to PATH:  export PATH=\"$CONCLAVE_HOME/bin:\$PATH\""
chmod +x "$CONCLAVE_HOME/bin/conclave" || true

echo "==> Done. Verify with:  conclave doctor"
