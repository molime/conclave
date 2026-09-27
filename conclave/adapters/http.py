"""
http.py, HTTP chat adapters using ONLY stdlib urllib (no `requests` dependency).

http_openai  : any OpenAI-compatible /chat/completions endpoint.
               Covers OpenRouter, LM Studio, Together, Groq, DeepSeek direct, vLLM, etc.
               spec: { base_url, api_key_env, model, temperature?, max_tokens?, headers? }
http_ollama  : a local Ollama daemon /api/chat.
               spec: { base_url (default http://localhost:11434), model }

These are chat-only (cannot edit files) -> used for plan / review / jury / synthesize.

http_ollama_act : OllamaActAdapter, chat-capable Ollama backend that CAN also edit
                  files by generating a unified diff and applying it with git apply.
                  Used as a rate-limit fallback executor when cli_kimi hits 429.

2026-07-05 S6: moved all imports to module top, replaced util_shlex_quote() shim
with direct shlex.quote() calls, removed module-bottom alias block.
"""
from __future__ import annotations
import json
import os
import re
import shlex
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

from .base import Adapter, AdapterResult
from .. import util


def _post(url: str, payload: dict, headers: dict, timeout: int) -> tuple[int, dict]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    for k, v in headers.items():
        if v:
            req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return resp.status, json.loads(body)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(body)
        except Exception:
            return e.code, {"error": body}
    except Exception as e:  # noqa: BLE001
        return 0, {"error": str(e)}


class OpenAIHttpAdapter(Adapter):
    can_act = False

    def _base(self) -> str:
        return (self.spec.get("base_url") or "https://openrouter.ai/api/v1").rstrip("/")

    def _key(self) -> str:
        env = self.spec.get("api_key_env", "OPENROUTER_API_KEY")
        return os.environ.get(env, "")

    def health(self):
        if not self.model:
            return False, "no model configured"
        if self.spec.get("api_key_env") and not self._key():
            return False, f"{self.spec['api_key_env']} not set in env/.env"
        return True, f"{self._base()} model={self.model}"

    def chat(self, prompt, cwd=None, log_path=None):
        url = self._base() + "/chat/completions"
        headers = {"Authorization": f"Bearer {self._key()}"} if self._key() else {}
        # OpenRouter etiquette headers (harmless elsewhere)
        headers.setdefault("HTTP-Referer", self.spec.get("referer", "https://conclave.local"))
        headers.setdefault("X-Title", "Conclave")
        headers.update(self.spec.get("headers", {}) or {})
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": self.spec.get("temperature", 0.3),
        }
        if self.spec.get("max_tokens"):
            payload["max_tokens"] = self.spec["max_tokens"]
        status, body = _post(url, payload, headers, self.spec.get("timeout", 600))
        if status == 200 and "choices" in body:
            msg = body["choices"][0]["message"]
            text = msg.get("content", "")
            # Reasoning models (DeepSeek V4 Pro, etc.) may leave content empty
            # and put the answer in reasoning_content / reasoning; fall back so
            # review/plan text is never lost (mirrors OllamaHttpAdapter's thinking
            # fallback). The key name varies: Ollama uses "reasoning_content",
            # Headroom-proxied OpenAI format uses "reasoning".
            if not text.strip():
                text = msg.get("reasoning_content", "") or msg.get("reasoning", "")
            res = AdapterResult(ok=True, text=text, rc=0, meta={"usage": body.get("usage", {})})
        else:
            err = body.get("error", body)
            res = AdapterResult(ok=False, text=f"[HTTP {status}] {json.dumps(err)[:800]}", rc=1)
        if log_path:
            util.write(log_path, f"# {url}\n# model={self.model} status={status}\n\n{res.text}")
        return res


class OllamaHttpAdapter(Adapter):
    can_act = False

    def _base(self) -> str:
        return (self.spec.get("base_url") or "http://localhost:11434").rstrip("/")

    def health(self):
        status, body = _post(
            self._base() + "/api/chat",
            {"model": self.model, "messages": [{"role": "user", "content": "ping"}], "stream": False},
            {},
            self.spec.get("health_timeout", 60),
        )
        if status == 200:
            return True, f"ollama {self._base()} model={self.model}"
        return False, f"ollama unreachable at {self._base()} ({body.get('error', '')[:120]})"

    def chat(self, prompt, cwd=None, log_path=None):
        url = self._base() + "/api/chat"
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "options": {"temperature": self.spec.get("temperature", 0.3)},
        }
        status, body = _post(url, payload, {}, self.spec.get("timeout", 900))
        if status == 200 and "message" in body:
            content = body["message"].get("content", "")
            # reasoning cloud models sometimes leave the answer empty and put it
            # in 'thinking'; fall back so verdict/plan text is never lost
            if not content.strip():
                content = body["message"].get("thinking", "")
            res = AdapterResult(ok=True, text=content, rc=0)
        else:
            res = AdapterResult(ok=False, text=f"[ollama {status}] {json.dumps(body)[:800]}", rc=1)
        if log_path:
            util.write(log_path, f"# {url}\n# model={self.model} status={status}\n\n{res.text}")
        return res


class OllamaActAdapter(OllamaHttpAdapter):
    """
    An Ollama adapter that CAN edit files, used as a fallback executor when
    cli_kimi is rate-limited. Strategy:
      1. Ask the model to produce ONLY a unified diff (git diff format).
      2. Extract the diff block from the response.
      3. Write to a temp file and apply with `git apply --whitespace=fix`.
    This is lightweight and does not require aider.

    2026-07-12: added optional chunked mode. When backend spec has `chunked: true`,
    the task is split into one-file-at-a-time prompts to keep diff latency low and
    avoid oversized-prompt crashes seen with cloud Kimi. The diff extraction now
    also strips trailing whitespace and tolerates `git apply` rejecting patches.
    """
    can_act = True

    _DIFF_PROMPT_PREFIX = (
        "You are implementing a code change. Produce ONLY a unified diff in git format "
        "(starting with --- a/... and +++ b/...). Do NOT include explanations, markdown "
        "code fences, or any text outside the diff. The diff must be directly applicable "
        "with `git apply`. Here is the task:\n\n"
    )

    _DIFF_PROMPT_CHUNK = (
        "You are implementing ONE file change. Produce ONLY a unified diff in git format "
        "for a single file (starting with --- a/<relative_path> and +++ b/<relative_path>). "
        "Do NOT include explanations, markdown code fences, or any text outside the diff. "
        "The diff must be directly applicable with `git apply`. File to modify: {file}\n\n"
        "Task context:\n{context}\n\n"
        "Implement ONLY the changes for this file."
    )

    def _extract_diff(self, text: str) -> str | None:
        # Strip markdown fences if present
        fenced = re.search(r"```(?:diff|patch)?\n?(.*?)```", text, re.DOTALL)
        diff_text = fenced.group(1).strip() if fenced else text.strip()
        if not ("--- " in diff_text and "+++ " in diff_text):
            return None
        return diff_text

    def _apply_diff(self, diff_text: str, cwd: str, log_path=None) -> tuple[int, str]:
        diff_text = diff_text.rstrip() + "\n"
        # Strip trailing whitespace per line while preserving diff structure
        cleaned = "\n".join(line.rstrip() for line in diff_text.splitlines()) + "\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".patch",
                                         delete=False, encoding="utf-8") as tmp:
            tmp.write(cleaned)
            tmp_path = tmp.name
        try:
            rc, out = util.sh(
                f"git apply --whitespace=nowarn {shlex.quote(tmp_path)}",
                cwd=cwd, timeout=60,
            )
            if rc != 0:
                rc2, out2 = util.sh(
                    f"git apply --whitespace=nowarn --reject {shlex.quote(tmp_path)}",
                    cwd=cwd, timeout=60,
                )
                return rc2, f"first apply rc={rc} {out[:400]}\n--reject rc={rc2} {out2[:400]}"
            return rc, out
        finally:
            os.unlink(tmp_path)

    def act(self, prompt: str, cwd: str, log_path=None) -> AdapterResult:
        if not self.spec.get("chunked"):
            return self._act_single(prompt, cwd, log_path)
        return self._act_chunked(prompt, cwd, log_path)

    def _act_single(self, prompt: str, cwd: str, log_path=None) -> AdapterResult:
        diff_prompt = self._DIFF_PROMPT_PREFIX + prompt
        chat_result = self.chat(diff_prompt, cwd=cwd, log_path=log_path)
        if not chat_result.ok:
            return chat_result
        diff_text = self._extract_diff(chat_result.text)
        if not diff_text:
            msg = f"[OllamaActAdapter] Model did not produce a valid diff. Output:\n{chat_result.text[:600]}"
            if log_path:
                util.write(log_path, msg)
            return AdapterResult(ok=False, text=msg, rc=1)
        rc, out = self._apply_diff(diff_text, cwd, log_path)
        if rc == 0:
            if log_path:
                util.write(log_path, f"[OllamaActAdapter] Applied diff OK\n\n{diff_text}")
            return AdapterResult(ok=True, text=f"Applied diff:\n{diff_text}", rc=0)
        msg = f"[OllamaActAdapter] failed to apply diff:\n{out}\n\nDiff attempted:\n{diff_text[:2000]}"
        if log_path:
            util.write(log_path, msg)
        return AdapterResult(ok=False, text=msg, rc=rc)

    def _act_chunked(self, prompt: str, cwd: str, log_path=None) -> AdapterResult:
        """
        Chunked mode: find every file mentioned in the prompt that should be created or
        modified, ask the model for one diff per file, and apply them sequentially.
        This keeps each cloud request small and avoids oversized-prompt crashes.
        """
        # Heuristic: file paths are lines like "File: path/to/file.ext" or in backticks.
        files = re.findall(r"(?:File|file):\s*([\w\-./]+(?:\.\w+)?)", prompt)
        files = [f for f in files if "." in f]
        if not files:
            # No explicit file list, fall back to single prompt
            return self._act_single(prompt, cwd, log_path)

        all_ok = True
        combined = []
        for i, file in enumerate(files, 1):
            chunk_prompt = self._DIFF_PROMPT_CHUNK.format(file=file, context=prompt)
            chunk_log = f"{log_path}.chunk{i}" if log_path else None
            res = self.chat(chunk_prompt, cwd=cwd, log_path=chunk_log)
            if not res.ok:
                all_ok = False
                combined.append(f"[chunk {file}] chat failed: {res.text[:300]}")
                continue
            diff_text = self._extract_diff(res.text)
            if not diff_text:
                all_ok = False
                combined.append(f"[chunk {file}] no valid diff: {res.text[:300]}")
                continue
            rc, out = self._apply_diff(diff_text, cwd, chunk_log)
            if rc != 0:
                all_ok = False
                combined.append(f"[chunk {file}] apply failed rc={rc}: {out[:300]}")
            else:
                combined.append(f"[chunk {file}] OK\n{diff_text}")
        summary = "\n\n".join(combined)
        if log_path:
            util.write(log_path, f"[OllamaActAdapter chunked] ok={all_ok}\n\n{summary}")
        return AdapterResult(ok=all_ok, text=summary, rc=0 if all_ok else 1)
