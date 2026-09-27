"""
cli.py, CLI agent adapters. Each shells out (via git-bash) to an already-authed CLI.

Flag choices are copied from the verified-working multi-model-dev-pipeline:
  - hermes:  hermes chat -q -m <model> --provider <prov> --yolo
  - codex:   codex exec --skip-git-repo-check   (+ --full-auto for edit stages)
             NEVER pass -m gpt-5-codex (ChatGPT-account login rejects it)
  - kimi:    kimi -p '<prompt>'   (auto-executes tools; cannot combine with -y/--auto)
  - claude:  claude -p '<prompt>' --permission-mode acceptEdits  (Claude Code headless)
  - aider:   aider --model <m> --yes --no-auto-commits --message '<prompt>' <files?>
"""
from __future__ import annotations
import shlex
from pathlib import Path

from .base import Adapter, AdapterResult
from .. import util


class _CliBase(Adapter):
    can_act = True
    cli_name = ""  # the executable to look for on PATH

    def health(self):
        path = util.which(self.cli_name)
        if path:
            return True, f"{self.cli_name} -> {path}"
        return False, f"{self.cli_name} not on PATH"

    def _run(self, cmd: str, cwd, log_path, timeout) -> AdapterResult:
        rc, out = util.sh(cmd, cwd=cwd, timeout=timeout, log_path=log_path)
        return AdapterResult(ok=(rc == 0), text=out, rc=rc, log_path=str(log_path) if log_path else None)

    def _prompt_file_instruction(self, prompt: str, log_path) -> str:
        """Write large/model-generated prompts to disk and pass only a small instruction.

        This avoids git-bash quoting failures when prior model logs contain literal shell
        quote escape fragments, CRLF, or other text that is valid markdown but toxic as a
        one-huge-command argument.
        """
        if not log_path:
            return prompt
        prompt_path = Path(log_path).with_suffix(".prompt.md")
        util.write(prompt_path, prompt)
        return (
            f"Read the prompt file at {prompt_path} and carry out those instructions "
            "completely. Do not ask questions. Output your final answer/verdict as requested."
        )


class HermesAdapter(_CliBase):
    cli_name = "hermes"

    def _cmd(self, prompt: str) -> str:
        model = self.model or "claude-opus-4-8"
        provider = self.spec.get("provider", "anthropic")
        return f"hermes chat -q -m {shlex.quote(model)} --provider {shlex.quote(provider)} --yolo {shlex.quote(prompt)}"

    def chat(self, prompt, cwd=None, log_path=None):
        return self._run(self._cmd(prompt), cwd, log_path, self.spec.get("timeout", 5400))

    def act(self, prompt, cwd, log_path=None):
        return self._run(self._cmd(prompt), cwd, log_path, self.spec.get("timeout", 5400))


class CodexAdapter(_CliBase):
    cli_name = "codex"

    def _flags(self):
        # codex auto-cancels MCP tool calls (incl. shared memory) in non-interactive
        # exec unless approvals+sandbox are bypassed. Opt in per-backend with
        # spec.mcp_memory: true (NOTE: this also disables codex's sandbox).
        return " --dangerously-bypass-approvals-and-sandbox" if self.spec.get("mcp_memory") else ""

    def chat(self, prompt, cwd=None, log_path=None):
        instr = self._prompt_file_instruction(prompt, log_path)
        cmd = f"codex exec --skip-git-repo-check{self._flags()} {shlex.quote(instr)}"
        return self._run(cmd, cwd, log_path, self.spec.get("timeout", 5400))

    def act(self, prompt, cwd, log_path=None):
        instr = self._prompt_file_instruction(prompt, log_path)
        full = " --dangerously-bypass-approvals-and-sandbox" if self.spec.get("mcp_memory") else " --full-auto"
        cmd = f"codex exec --skip-git-repo-check{full} {shlex.quote(instr)}"
        return self._run(cmd, cwd, log_path, self.spec.get("timeout", 5400))


class KimiAdapter(_CliBase):
    cli_name = "kimi"

    def chat(self, prompt, cwd=None, log_path=None):
        instr = self._prompt_file_instruction(prompt, log_path)
        cmd = f"kimi -p {shlex.quote(instr)}"
        return self._run(cmd, cwd, log_path, self.spec.get("timeout", 5400))

    def act(self, prompt, cwd, log_path=None):
        instr = self._prompt_file_instruction(prompt, log_path)
        cmd = f"kimi -p {shlex.quote(instr)}"
        return self._run(cmd, cwd, log_path, self.spec.get("timeout", 5400))


class ClaudeAdapter(_CliBase):
    cli_name = "claude"

    def chat(self, prompt, cwd=None, log_path=None):
        cmd = f"claude -p {shlex.quote(prompt)}"
        return self._run(cmd, cwd, log_path, self.spec.get("timeout", 5400))

    def act(self, prompt, cwd, log_path=None):
        cmd = f"claude -p {shlex.quote(prompt)} --permission-mode acceptEdits"
        return self._run(cmd, cwd, log_path, self.spec.get("timeout", 5400))


class AiderAdapter(_CliBase):
    """
    The OSS-execution workhorse. aider speaks any model:
      --model openrouter/qwen/qwen-2.5-coder-32b-instruct
      --model openrouter/deepseek/deepseek-chat
      --model ollama/qwen2.5-coder:32b   (needs OLLAMA_API_BASE)
      --model lm_studio/<model>          (needs LM_STUDIO_API_BASE/_KEY)
    Headless: --yes-always --no-auto-commits --message '<prompt>'.
    """
    cli_name = "aider"

    def _cmd(self, prompt: str, act: bool) -> str:
        model = self.model
        parts = ["aider", "--model", shlex.quote(model),
                 "--yes-always", "--no-auto-commits", "--no-gitignore",
                 "--no-check-update", "--no-show-model-warnings"]
        # extra raw flags from config (e.g. --map-tokens, --weak-model)
        for f in self.spec.get("extra_flags", []) or []:
            parts.append(f)
        if not act:
            parts.append("--no-auto-lint")
        parts += ["--message", shlex.quote(prompt)]
        # optional file scoping
        for f in self.spec.get("files", []) or []:
            parts.append(shlex.quote(f))
        return " ".join(parts)

    def chat(self, prompt, cwd=None, log_path=None):
        return self._run(self._cmd(prompt, act=False), cwd, log_path, self.spec.get("timeout", 5400))

    def act(self, prompt, cwd, log_path=None):
        return self._run(self._cmd(prompt, act=True), cwd, log_path, self.spec.get("timeout", 5400))
