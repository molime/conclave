"""
util.py, shell, paths, logging, env. Windows/git-bash friendly. Stdlib only.

Mirrors the proven patterns from multi-model-dev-pipeline:
- every subprocess runs through git-bash (not cmd.exe) on Windows
- child output decoded with errors="replace"; PYTHONUTF8 exported
- HERMES_PIPELINE_ACTIVE=1 exported so Hermes enforcement hooks never block executors
"""
from __future__ import annotations
import os
import shlex
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

IS_WINDOWS = os.name == "nt"


def find_bash() -> str | None:
    """Locate git-bash on Windows; plain bash elsewhere."""
    override = os.environ.get("HERMES_BASH") or os.environ.get("CONCLAVE_BASH")
    if override and Path(override).exists():
        return override
    if not IS_WINDOWS:
        for c in ("/bin/bash", "/usr/bin/bash"):
            if Path(c).exists():
                return c
        return "bash"
    for c in (
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files (x86)\Git\bin\bash.exe",
        os.path.expanduser(r"~\AppData\Local\Programs\Git\bin\bash.exe"),
    ):
        if Path(c).exists():
            return c
    return None


# Env exported into every spawned stage. Mirrors pipeline.py's bash prefix.
_BASE_ENV_EXPORTS = (
    "export HERMES_PIPELINE_ACTIVE=1; "
    "export CONCLAVE_ACTIVE=1; "
    "export PYTHONUTF8=1; "
    "export PYTHONIOENCODING=utf-8:replace; "
)


def sh(
    command: str,
    cwd: str | None = None,
    timeout: int = 5400,
    extra_env: dict | None = None,
    log_path: str | Path | None = None,
) -> tuple[int, str]:
    """
    Run a command through git-bash. Returns (exit_code, combined_output).
    Output is also appended to log_path if given. Never raises on non-zero exit;
    raises only on the inability to spawn. TimeoutExpired -> (124, partial).
    """
    bash = find_bash()
    env = os.environ.copy()
    if extra_env:
        env.update({k: str(v) for k, v in extra_env.items()})

    full = _BASE_ENV_EXPORTS + command
    # CRITICAL: strip carriage returns. Model output (plans/reviews/diffs) routinely
    # carries CRLF; a lone CR inside a single-quoted bash arg makes git-bash -lc abort
    # with "unexpected EOF while looking for matching `'`" (rc=2), silently no-op'ing
    # the whole stage. CRs in a bash command line are always spurious, drop them.
    full = full.replace("\r", "")

    if bash:
        argv = [bash, "-lc", full]
    else:
        # Fallback: no bash (rare on a fresh non-Windows box without bash) -> sh -c
        argv = ["sh", "-c", full]

    start = time.time()
    try:
        proc = subprocess.run(
            argv,
            cwd=cwd,
            env=env,
            capture_output=True,
            timeout=timeout,
        )
        out = (proc.stdout or b"").decode("utf-8", errors="replace")
        err = (proc.stderr or b"").decode("utf-8", errors="replace")
        combined = out + (("\n[stderr]\n" + err) if err.strip() else "")
        rc = proc.returncode
    except subprocess.TimeoutExpired as e:
        partial = b""
        for chunk in (e.stdout, e.stderr):
            if chunk:
                partial += chunk if isinstance(chunk, bytes) else chunk.encode()
        combined = partial.decode("utf-8", errors="replace") + f"\n[TIMEOUT after {timeout}s]"
        rc = 124

    if log_path:
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        header = f"# cmd: {command}\n# cwd: {cwd or os.getcwd()}\n# elapsed: {time.time()-start:.1f}s rc={rc}\n\n"
        Path(log_path).write_text(header + combined, encoding="utf-8")
    return rc, combined


def run_dir(repo: str) -> Path:
    """Create and return a fresh timestamped run scratch dir under the repo."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    d = Path(repo) / ".conclave-runs" / ts
    d.mkdir(parents=True, exist_ok=True)
    return d


def log(msg: str, level: str = "INFO") -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {level:5} {msg}", flush=True)


def write(path: str | Path, content: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(content, encoding="utf-8")


def read(path: str | Path, default: str = "") -> str:
    p = Path(path)
    return p.read_text(encoding="utf-8", errors="replace") if p.exists() else default


def load_dotenv(path: str | Path) -> None:
    """Minimal .env loader (KEY=VALUE), does not override existing env."""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v




def is_rate_limited(result) -> bool:
    """
    Detect when an executor is rate-limited so we can fall back gracefully.
    Checks the result text and rc for known rate-limit signals from Kimi CLI,
    Ollama Cloud, and other providers.
    """
    if result is None:
        return False
    text = (getattr(result, "text", "") or "").lower()
    rc = getattr(result, "rc", 0)
    signals = [
        "provider.rate_limit",
        "429",
        "rate limit",
        "rate_limit",
        "you've reached your usage limit",
        "you have reached your usage limit",
        "usage limit for this period",
        "too many requests",
        "quota exceeded",
        "context_length_exceeded",  # treat context overflow as a signal too
    ]
    return any(s in text for s in signals)

def which(cmd: str) -> str | None:
    """Resolve a command on PATH (via bash login shell so PATH matches CLI auth)."""
    rc, out = sh(f"command -v {shlex.quote(cmd)} 2>/dev/null", timeout=20)
    out = out.strip().splitlines()[0].strip() if out.strip() else ""
    return out or None
