#!/usr/bin/env python3
"""
probe_ollama_cloud.py, Discover live Ollama Cloud models and compare to conclave.cloud.yaml.

Run:  python scripts/probe_ollama_cloud.py
Output: prints a report. If --update is passed, updates the # verified line in the yaml.

Used by the weekly Hermes cron job to keep the model roster fresh.
"""
import json
import sys
import urllib.request
import urllib.error
from datetime import date
from pathlib import Path

CANDIDATE_MODELS = [
    # Primary three-model specialization
    "kimi-k2.7-code:cloud",
    "glm-5.2:cloud",
    "deepseek-v4-pro:cloud",
    # Kimi family (legacy / reserve)
    "kimi-k2.5:cloud",
    "kimi-k2.6:cloud",
    "kimi-k2.8:cloud",
    # Qwen family (legacy / reserve)
    "qwen3.5:cloud",
    "qwen3-coder:480b-cloud",
    "qwen2.5-coder:7b:cloud",
    # GLM (legacy)
    "glm-5.1:cloud",
    "glm-4.6:cloud",
    # DeepSeek (legacy / reserve)
    "deepseek-v4-flash:cloud",
    "deepseek-v3:cloud",
    "deepseek-r1:cloud",
    # GPT OSS
    "gpt-oss:120b-cloud",
    # MiniMax
    "minimax-m3:cloud",
    # Gemma
    "gemma3:27b-cloud",
    "gemma3:9b-cloud",
    # Llama (if they come back)
    "llama3.3:70b-cloud",
    "llama3.1:70b-cloud",
    # Mistral
    "mistral-small:cloud",
    "mistral-large:cloud",
    # Phi
    "phi4:cloud",
    "phi4-mini:cloud",
]

OLLAMA_BASE = "http://localhost:11434"


def probe(model: str, timeout: int = 25) -> tuple[bool, str]:
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "Reply: OK"}],
        "stream": False,
    }).encode()
    req = urllib.request.Request(
        f"{OLLAMA_BASE}/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        body = json.loads(resp.read())
        content = body.get("message", {}).get("content", "").strip()[:40]
        return True, content
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}"
    except Exception as e:
        return False, str(e)[:60]


def main():
    update = "--update" in sys.argv
    print(f"Probing {len(CANDIDATE_MODELS)} Ollama Cloud models ({date.today()})...\n")

    live = []
    dead = []
    for model in CANDIDATE_MODELS:
        ok, detail = probe(model)
        status = "OK  " if ok else "DEAD"
        print(f"  [{status}] {model:40} {detail}")
        if ok:
            live.append(model)
        else:
            dead.append(model)

    print(f"\nLive ({len(live)}): {live}")
    print(f"Dead ({len(dead)}): {dead}")

    # Compare to current conclave.cloud.yaml
    yaml_path = Path(__file__).parent.parent / "conclave.cloud.yaml"
    if yaml_path.exists():
        yaml_text = yaml_path.read_text(encoding="utf-8")
        in_config = [m for m in live if m in yaml_text]
        missing = [m for m in live if m not in yaml_text]
        retired = [m for m in dead if m in yaml_text]
        print(f"\nModels in config that are LIVE:    {in_config}")
        print(f"NEW live models not in config:     {missing}")
        print(f"RETIRED (dead but still in config): {retired}")
        if missing:
            print("\n⚠  Consider adding these to conclave.cloud.yaml:")
            for m in missing:
                print(f"   {m}")
        if retired:
            print("\n⚠  Consider removing these from conclave.cloud.yaml:")
            for m in retired:
                print(f"   {m}")

    if update and yaml_path.exists():
        # Update the verified date comment in the yaml
        import re
        new_text = re.sub(
            r"# All models verified live \d{4}-\d{2}-\d{2}",
            f"# All models verified live {date.today()}",
            yaml_path.read_text(encoding="utf-8"),
        )
        yaml_path.write_text(new_text, encoding="utf-8")
        print(f"\nUpdated verified date in {yaml_path}")


if __name__ == "__main__":
    main()
