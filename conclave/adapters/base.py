"""Adapter base classes."""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class AdapterResult:
    ok: bool
    text: str
    rc: int = 0
    log_path: str | None = None
    meta: dict = field(default_factory=dict)


class Adapter:
    """
    One backend instance. Two capabilities:
      - chat(prompt, cwd, log_path)  -> text response (planners, reviewers, jury)
      - act(prompt, cwd, log_path)   -> edits files in cwd (implement/fix)

    HTTP adapters implement chat only (act raises). CLI agent adapters implement both.
    """

    can_act = False

    def __init__(self, backend):
        self.backend = backend
        self.name = backend.name
        self.spec = backend.spec
        self.model = backend.model

    def chat(self, prompt: str, cwd: str | None = None, log_path=None) -> AdapterResult:
        raise NotImplementedError(f"{self.backend.kind} does not support chat")

    def act(self, prompt: str, cwd: str, log_path=None) -> AdapterResult:
        raise NotImplementedError(f"{self.backend.kind} cannot edit files (chat-only backend)")

    def health(self) -> tuple[bool, str]:
        """(reachable, detail), cheap check for `conclave doctor`."""
        return True, "no health check implemented"
