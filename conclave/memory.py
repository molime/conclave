"""
memory.py, shared cross-agent memory bridge for Conclave's stateless OSS models.

The CLI/HTTP models Conclave drives (Qwen, GLM, DeepSeek via Ollama/OpenRouter) have NO
memory of their own and don't speak MCP. But Hermes, Claude Code and Codex all persist to
a shared knowledge-graph file written by @modelcontextprotocol/server-memory:

    ~/.agent-memory/memory.json   (JSONL: one entity/relation per line)

This module lets the orchestrator READ that same file and inject the facts into OSS-model
prompts (so they benefit from everything the other agents learned), and APPEND new entities
the same way (so what the council learns flows back to every agent). Same store, one brain.

Stdlib only.
"""
from __future__ import annotations
import json
import os
from pathlib import Path


def _memory_path() -> Path:
    p = os.environ.get("MEMORY_FILE_PATH") or str(
        Path.home() / ".agent-memory" / "memory.json"
    )
    return Path(p)


def load_graph(path: str | None = None) -> tuple[list, list]:
    """Return (entities, relations) from the shared JSONL knowledge graph."""
    p = Path(path) if path else _memory_path()
    entities, relations = [], []
    if not p.exists():
        return entities, relations
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("type") == "entity":
            entities.append(obj)
        elif obj.get("type") == "relation":
            relations.append(obj)
    return entities, relations


def render_memory_block(path: str | None = None, max_chars: int = 4000) -> str:
    """
    Render the shared memory as a compact text block to prepend to a model prompt.
    Returns "" if empty so callers can skip cleanly.
    """
    entities, relations = load_graph(path)
    if not entities and not relations:
        return ""
    lines = ["=== SHARED MEMORY (cross-agent knowledge graph) ==="]
    for e in entities:
        obs = e.get("observations", [])
        lines.append(f"- {e.get('name','?')} [{e.get('entityType','')}]:")
        for o in obs:
            lines.append(f"    • {o}")
    if relations:
        lines.append("Relations:")
        for r in relations:
            lines.append(f"    {r.get('from','?')} -{r.get('relationType','related')}-> {r.get('to','?')}")
    block = "\n".join(lines)
    if len(block) > max_chars:
        block = block[:max_chars] + "\n… (truncated)"
    return block + "\n=== END SHARED MEMORY ===\n"


def append_entities(new_entities: list[dict], path: str | None = None) -> int:
    """
    Append entities to the shared JSONL store (same format the MCP server uses), skipping
    names that already exist. Each entity: {name, entityType, observations:[...]}.
    Returns number of entities written.
    """
    p = Path(path) if path else _memory_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    existing_names = {e.get("name") for e in load_graph(str(p))[0]}
    written = 0
    with p.open("a", encoding="utf-8") as f:
        for e in new_entities:
            name = e.get("name")
            if not name or name in existing_names:
                continue
            rec = {
                "type": "entity",
                "name": name,
                "entityType": e.get("entityType", "fact"),
                "observations": e.get("observations", []),
            }
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            existing_names.add(name)
            written += 1
    return written


if __name__ == "__main__":  # quick manual check
    print(render_memory_block() or "(memory empty)")
