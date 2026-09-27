"""
config.py, load conclave.yaml, expand env, resolve roles -> teams -> backends.

The YAML schema (see conclave.yaml):

  backends:
    <name>:
      kind: cli_kimi | cli_codex | cli_hermes | cli_claude | cli_aider
            | http_openai | http_ollama
      model: <model id>            # for http_* and some cli (aider/hermes)
      base_url: <url>              # http_* only; env-expandable
      api_key_env: OPENROUTER_API_KEY   # http_openai only
      # any extra kind-specific knobs

  roles:
    plan:    { team: [planner_kimi], aggregate: synthesize, synthesizer: planner_kimi }
    implement: { team: [exec_kimi], strategy: single }
    review:  { team: [rev_kimi, rev_glm, rev_deepseek], aggregate: vote, quorum: majority }
    fix:     { team: [exec_kimi], strategy: single }
    final_review: { team: [opus, codex], aggregate: vote, quorum: all, enabled: false }

  projects:
    demo:
      repo: <path>
      gates: { lint: null, typecheck: null, unit: "python -m pytest -q", e2e: null, build: null }
      stress_cmd: null
      context: "free-form blurb injected into prompts"

  defaults:
    max_review_iters: 3
"""
from __future__ import annotations
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import util

_ENV_RE = re.compile(r"\$\{([A-Z0-9_]+)(?::-([^}]*))?\}")


def _expand(value):
    """Recursively expand ${VAR} / ${VAR:-default} in strings."""
    if isinstance(value, str):
        def repl(m):
            return os.environ.get(m.group(1), m.group(2) or "")
        return _ENV_RE.sub(repl, value)
    if isinstance(value, list):
        return [_expand(v) for v in value]
    if isinstance(value, dict):
        return {k: _expand(v) for k, v in value.items()}
    return value


@dataclass
class Backend:
    name: str
    kind: str
    spec: dict = field(default_factory=dict)

    @property
    def model(self) -> str:
        return self.spec.get("model", "")


@dataclass
class Project:
    name: str
    repo: str
    gates: dict
    stress_cmd: str | None
    context: str


@dataclass
class Config:
    raw: dict
    backends: dict
    roles: dict
    projects: dict
    defaults: dict

    def backend(self, name: str) -> Backend:
        if name not in self.backends:
            raise KeyError(f"backend '{name}' not defined in conclave.yaml")
        b = self.backends[name]
        return Backend(name=name, kind=b["kind"], spec=b)

    def role(self, name: str) -> dict:
        return self.roles.get(name, {}) or {}

    def team(self, role: str) -> list[Backend]:
        return [self.backend(n) for n in self.role(role).get("team", [])]

    def project(self, name: str) -> Project:
        if name not in self.projects:
            raise KeyError(
                f"project '{name}' not defined. Known: {', '.join(self.projects) or '(none)'}"
            )
        p = self.projects[name]
        return Project(
            name=name,
            repo=os.path.expanduser(p["repo"]),
            gates=p.get("gates", {}) or {},
            stress_cmd=p.get("stress_cmd"),
            context=p.get("context", ""),
        )


def find_config(explicit: str | None = None) -> Path:
    if explicit:
        return Path(explicit)
    env = os.environ.get("CONCLAVE_CONFIG")
    if env:
        return Path(env)
    # walk up from cwd, then the package root
    here = Path.cwd()
    for d in [here, *here.parents]:
        c = d / "conclave.yaml"
        if c.exists():
            return c
    return Path(__file__).resolve().parent.parent / "conclave.yaml"


def load(path: str | None = None) -> Config:
    cfg_path = find_config(path)
    root = cfg_path.parent
    # load .env next to the config so keys are available for expansion
    util.load_dotenv(root / ".env")
    util.load_dotenv(root / "conclave" / ".env")
    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    data = _expand(data)
    return Config(
        raw=data,
        backends=data.get("backends", {}) or {},
        roles=data.get("roles", {}) or {},
        projects=data.get("projects", {}) or {},
        defaults=data.get("defaults", {}) or {},
    )
