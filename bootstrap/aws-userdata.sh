#!/usr/bin/env bash
# =============================================================================
# Conclave, AWS EC2 cloud-init / user-data provisioner (Ubuntu 22.04+).
# Paste into the EC2 "User data" field, or run on a fresh box as root.
# Stands up a headless Conclave with a fully-LOCAL council (Ollama) by default,
# so it needs no API keys to start. Add OPENROUTER_API_KEY to /opt/conclave/.env
# to use the cloud council instead.
# =============================================================================
set -euxo pipefail

REPO_URL="${CONCLAVE_REPO_URL:-https://github.com/YOUR_ORG/conclave.git}"
PREFIX=/opt/conclave

apt-get update -y
apt-get install -y python3 python3-pip python3-venv git jq curl pipx
pipx ensurepath || true

# Engine deps + OSS executor
python3 -m pip install --break-system-packages pyyaml || python3 -m pip install pyyaml
pipx install aider-chat || python3 -m pip install --break-system-packages aider-chat || true

# Fetch Conclave
if [ ! -d "$PREFIX" ]; then
  git clone "$REPO_URL" "$PREFIX" || { mkdir -p "$PREFIX"; echo "clone failed, upload code to $PREFIX"; }
fi

# Local model runtime (no API key needed)
curl -fsSL https://ollama.com/install.sh | sh || true
systemctl enable --now ollama || (nohup ollama serve >/var/log/ollama.log 2>&1 &)
sleep 5
ollama pull qwen2.5-coder:7b || true

# .env
[ -f "$PREFIX/.env" ] || cp "$PREFIX/.env.example" "$PREFIX/.env" || true

# Make `conclave` global
ln -sf "$PREFIX/bin/conclave" /usr/local/bin/conclave || true
chmod +x "$PREFIX/bin/conclave" || true

echo "Conclave provisioned at $PREFIX."
echo "Edit roles in $PREFIX/conclave.yaml (use plan_local/rev_local for key-free local council)."
echo "Verify:  conclave doctor"
