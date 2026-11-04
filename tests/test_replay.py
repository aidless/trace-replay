"""回放器判据：录制回放、stub 重算、fail-closed 与离线保证。"""

from __future__ import annotations

import socket
from typing import Any

import pytest

from tests._helpers import dump, records_copy
from trace_replay.errors import (
    E_TOOL_REPLAY_DIVERGENCE,
    E_TRACE_LOOP,
    E_TRACE_OBS_SCHEMA,
    E_TRACE_STEP_LIMIT,
    E_TRACE_TOOL_ARGS,
    E_TRACE_UNKNOWN_TOOL,
    TraceError,
)
from trace_replay.model import parse_trace
from trace_replay.replay import ReplayOutcome, replay_trace
from trace_replay.tools import default_registry


def _trace(records: list[dict[str, Any]]) -> Any:
    return parse_trace(dump(records), sha256="sha")


def _replay(records: list[dict[str, Any]], stub_mode: bool = True) -> ReplayOutcome:
    return replay_trace(_trace(records), default_registry(), stub_mode=stub_mode)


def test_recorded_replay_returns_sequence_and_answer() -> None:
    outcome = _replay(records_copy(), stub_mode=False)
    assert outcome.tool_sequence == ("search", "calc")
    assert [call.span_id for call in outcome.calls] == ["s0", "s1"]
    assert outcome.answer == {"text": "2040", "value": 2040}
    assert outcome.stub_checks == 0


def test_stub_mode_recomputes_and_matches() -> None:
    outcome = _replay(records_copy(), stub_mode=True)
    assert outcome.stub_checks == 2
    assert all(call.stub_checked and call.stub_matched for call in outcome.calls)


def test_stub_divergence_fails_closed() -> None:
    records = records_copy()
    records[2]["observation"]["value"] = 7  # 录制端写错：2+3 不可能是 7
    records[2]["args"]["expression"] = "2+3"
    with pytest.raises(TraceError) as caught:
        _replay(records)
    assert caught.value.code == E_TOOL_REPLAY_DIVERGENCE
    assert "value" in caught.value.message


def test_unknown_tool_fails_closed() -> None:
    records = records_copy()
    records[1]["tool"] = "http_get"
    with pytest.raises(TraceError) as caught:
        _replay(records)
    assert caught.value.code == E_TRACE_UNKNOWN_TOOL


def test_missing_tool_argument_fails_closed() -> None:
    records = records_copy()
    records[1]["args"] = {}
    with pytest.raises(TraceError) as caught:
        _replay(records, stub_mode=False)
    assert caught.value.code == E_TRACE_TOOL_ARGS


def test_wrong_tool_argument_type_fails_closed() -> None:
    records = records_copy()
    records[1]["args"] = {"query": 42}
    with pytest.raises(TraceError) as caught:
        _replay(records, stub_mode=False)
    assert caught.value.code == E_TRACE_TOOL_ARGS


def test_observation_missing_field_fails_closed() -> None:
    records = records_copy()
    records[1]["observation"] = {"status": "ok"}
    with pytest.raises(TraceError) as caught:
        _replay(records)
    assert caught.value.code == E_TRACE_OBS_SCHEMA
    assert "result" in caught.value.message


def test_observation_wrong_type_fails_closed() -> None:
    records = records_copy()
    records[1]["observation"] = {"status": "ok", "result": 2040}
    with pytest.raises(TraceError) as caught:
        _replay(records)
    assert caught.value.code == E_TRACE_OBS_SCHEMA


def test_step_limit_fails_closed() -> None:
    records = records_copy()
    records[0]["limits"] = {"max_steps": 1, "max_repeats": 3}
    with pytest.raises(TraceError) as caught:
        _replay(records)
    assert caught.value.code == E_TRACE_STEP_LIMIT
    assert caught.value.location == "step[1]"


def _search_step(index: int, span_id: str) -> dict[str, Any]:
    return {
        "type": "step",
        "index": index,
        "span_id": span_id,
        "tool": "search",
        "args": {"query": "population 2040"},
        "observation": {"status": "ok", "result": "2040"},
        "latency_ms": 1,
    }


def _calc_step(index: int, span_id: str) -> dict[str, Any]:
    return {
        "type": "step",
        "index": index,
        "span_id": span_id,
        "tool": "calc",
        "args": {"expression": "1+1"},
        "observation": {"status": "ok", "value": 2},
        "latency_ms": 1,
    }


def _meta_with_repeats(max_repeats: int, max_steps: int = 8) -> dict[str, Any]:
    meta = records_copy()[0]
    meta["limits"] = {"max_steps": max_steps, "max_repeats": max_repeats}
    return meta


def _final(index: int) -> dict[str, Any]:
    return {"type": "final", "index": index, "answer": {"text": "2040"}}


def test_loop_period_one_fails_closed() -> None:
    records = [
        _meta_with_repeats(2),
        _search_step(0, "a0"),
        _search_step(1, "a1"),
        _search_step(2, "a2"),
        _final(3),
    ]
    with pytest.raises(TraceError) as caught:
        _replay(records, stub_mode=False)
    assert caught.value.code == E_TRACE_LOOP
    assert "周期 1" in caught.value.message


def test_loop_period_two_fails_closed() -> None:
    records = [
        _meta_with_repeats(1),
        _search_step(0, "a0"),
        _calc_step(1, "b0"),
        _search_step(2, "a1"),
        _calc_step(3, "b1"),
        _final(4),
    ]
    with pytest.raises(TraceError) as caught:
        _replay(records, stub_mode=False)
    assert caught.value.code == E_TRACE_LOOP
    assert "周期 2" in caught.value.message


def test_repeats_below_threshold_are_allowed() -> None:
    records = [
        _meta_with_repeats(3),
        _search_step(0, "a0"),
        _search_step(1, "a1"),
        _search_step(2, "a2"),
        _final(3),
    ]
    outcome = _replay(records, stub_mode=False)
    assert len(outcome.calls) == 3


def test_replay_is_pure(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*_: Any, **__: Any) -> Any:
        raise AssertionError("回放期间不允许建立网络连接")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    first = _replay(records_copy())
    second = _replay(records_copy())
    assert first == second


def test_replay_signature_changes_with_args() -> None:
    records = records_copy()
    records[1]["args"] = {"query": "casmi 2026 deadline"}
    records[1]["observation"] = {"status": "ok", "result": "2026-12-07"}
    outcome = _replay(records)
    assert outcome.tool_sequence == ("search", "calc")
