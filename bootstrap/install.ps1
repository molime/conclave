# =============================================================================
# Conclave installer (Windows / PowerShell). Idempotent.
#   powershell -ExecutionPolicy Bypass -File bootstrap\install.ps1
# =============================================================================
$ErrorActionPreference = "Stop"
$Home_ = (Resolve-Path "$PSScriptRoot\..").Path
Write-Host "==> Conclave home: $Home_"

# 1. Python
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
  Write-Error "python not found. Install Python 3.10+ first."; exit 1
}
python -c "import sys;print('python',sys.version.split()[0])"

# 2. pyyaml
try { python -c "import yaml" 2>$null } catch { python -m pip install --user pyyaml }

# 3. aider (OSS executor)
if (-not (Get-Command aider -ErrorAction SilentlyContinue)) {
  Write-Host "==> aider not found. Install with: pipx install aider-chat  (or: pip install aider-chat)"
}

# 4. Ollama (optional)
if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
  Write-Host "(optional) For local runs install Ollama from https://ollama.com then: ollama pull qwen2.5-coder:7b"
}

# 5. .env
if (-not (Test-Path "$Home_\.env")) {
  Copy-Item "$Home_\.env.example" "$Home_\.env"
  Write-Host "==> created .env, add OPENROUTER_API_KEY (or configure Ollama)"
}

Write-Host "==> add to PATH:  `$env:PATH = `"$Home_\bin;`$env:PATH`""
Write-Host "==> Done. Verify with:  conclave doctor   (or: python -m conclave.cli doctor)"
