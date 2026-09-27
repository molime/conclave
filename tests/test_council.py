"""
Extended tests for council.py, parallel execution and ordering guarantees.
"""
import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conclave.council import parse_verdict, vote, run_council_chat  # noqa: E402
from conclave.adapters.base import AdapterResult  # noqa: E402


# ---------------------------------------------------------------------------
# Existing verdict tests (keep them here too so the file is self-contained)
# ---------------------------------------------------------------------------

def test_clean_trailing_verdict():
    assert parse_verdict("looks good.\n\nVERDICT: APPROVED") == "APPROVED"
    assert parse_verdict("bug found.\nVERDICT: CHANGES_REQUESTED") == "CHANGES_REQUESTED"


def test_menu_echo_then_real_verdict():
    text = (
        'The instruction says end with "VERDICT: APPROVED or VERDICT: CHANGES_REQUESTED".\n'
        "The code is correct and satisfies the task.\n"
        "VERDICT: APPROVED\n"
    )
    assert parse_verdict(text) == "APPROVED"


def test_markdown_bold_verdict():
    assert parse_verdict("review...\n**VERDICT: APPROVED**") == "APPROVED"


def test_no_verdict():
    assert parse_verdict("I think it's fine") == "UNKNOWN"
    assert parse_verdict("") == "UNKNOWN"


def test_only_menu_echo_is_unknown_not_false_negative():
    text = "end with VERDICT: APPROVED or VERDICT: CHANGES_REQUESTED"
    assert parse_verdict(text) == "UNKNOWN"


def test_vote_majority():
    class B:  # noqa
        def __init__(self, n): self.name = n; self.spec = {}

    class R:  # noqa
        def __init__(self, t): self.ok = True; self.text = t

    reviews = [
        (B("a"), R("VERDICT: APPROVED")),
        (B("b"), R("VERDICT: APPROVED")),
        (B("c"), R("VERDICT: CHANGES_REQUESTED")),
    ]
    passed, verdicts, _ = vote(reviews, quorum="majority")
    assert passed is True
    passed_all, _, _ = vote(reviews, quorum="all")
    assert passed_all is False


# ---------------------------------------------------------------------------
# New parallel execution tests
# ---------------------------------------------------------------------------

def _make_backend(name):
    b = MagicMock()
    b.name = name
    b.kind = "http_ollama"
    b.model = "test-model"
    b.spec = {}
    return b


def test_run_council_chat_sequential_ordering():
    """Sequential mode preserves order and calls each backend once."""
    team = [_make_backend("a"), _make_backend("b"), _make_backend("c")]
    call_order = []

    def fake_make_adapter(backend):
        adapter = MagicMock()
        adapter.chat.side_effect = lambda prompt, cwd, log_path: (
            call_order.append(backend.name),
            AdapterResult(ok=True, text=f"response from {backend.name}", rc=0)
        )[1]
        return adapter

    run_dir = Path(".")
    with patch("conclave.council.make_adapter", side_effect=fake_make_adapter):
        results = run_council_chat(team, "test prompt", ".", run_dir, "test", parallel=False)

    assert len(results) == 3
    assert [b.name for b, r in results] == ["a", "b", "c"]
    assert call_order == ["a", "b", "c"]
    assert all(r.ok for _, r in results)


def test_run_council_chat_parallel_ordering():
    """Parallel mode returns results in ORIGINAL TEAM ORDER, not completion order."""
    import time as _time

    team = [_make_backend("slow"), _make_backend("fast"), _make_backend("medium")]

    # slow takes 0.15s, fast 0.01s, medium 0.07s, fast finishes first but should be second
    delays = {"slow": 0.15, "fast": 0.01, "medium": 0.07}

    def fake_make_adapter(backend):
        adapter = MagicMock()
        def _chat(prompt, cwd, log_path):
            _time.sleep(delays[backend.name])
            return AdapterResult(ok=True, text=f"from {backend.name}", rc=0)
        adapter.chat.side_effect = _chat
        return adapter

    run_dir = Path(".")
    with patch("conclave.council.make_adapter", side_effect=fake_make_adapter):
        results = run_council_chat(team, "test", ".", run_dir, "order_test", parallel=True)

    # Order must match original team order, not completion order
    assert len(results) == 3
    names = [b.name for b, r in results]
    assert names == ["slow", "fast", "medium"], f"Got order: {names}"
    assert all(r.ok for _, r in results)
    texts = [r.text for _, r in results]
    assert texts == ["from slow", "from fast", "from medium"]


def test_run_council_chat_parallel_handles_exception():
    """A backend that raises an exception gets an ok=False result; others succeed."""
    team = [_make_backend("good"), _make_backend("bad"), _make_backend("also_good")]

    def fake_make_adapter(backend):
        adapter = MagicMock()
        if backend.name == "bad":
            adapter.chat.side_effect = RuntimeError("simulated network error")
        else:
            adapter.chat.return_value = AdapterResult(ok=True, text=f"ok from {backend.name}", rc=0)
        return adapter

    run_dir = Path(".")
    with patch("conclave.council.make_adapter", side_effect=fake_make_adapter):
        results = run_council_chat(team, "test", ".", run_dir, "exc_test", parallel=True)

    assert len(results) == 3
    good_r = next(r for b, r in results if b.name == "good")
    bad_r = next(r for b, r in results if b.name == "bad")
    also_good_r = next(r for b, r in results if b.name == "also_good")
    assert good_r.ok is True
    assert bad_r.ok is False
    assert "simulated network error" in bad_r.text or "future error" in bad_r.text
    assert also_good_r.ok is True


def test_run_council_chat_parallel_single_backend():
    """Single-backend team still works correctly in parallel mode."""
    team = [_make_backend("solo")]

    def fake_make_adapter(backend):
        adapter = MagicMock()
        adapter.chat.return_value = AdapterResult(ok=True, text="solo response", rc=0)
        return adapter

    run_dir = Path(".")
    with patch("conclave.council.make_adapter", side_effect=fake_make_adapter):
        results = run_council_chat(team, "test", ".", run_dir, "solo_test", parallel=True)

    assert len(results) == 1
    assert results[0][1].text == "solo response"


def test_run_council_chat_empty_team():
    """Empty team returns empty list without errors."""
    run_dir = Path(".")
    results = run_council_chat([], "test", ".", run_dir, "empty_test", parallel=True)
    assert results == []
