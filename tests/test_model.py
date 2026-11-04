"""轨迹解析与字段校验的判据测试。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests._helpers import dump, records_copy
from trace_replay.errors import (
    E_TRACE_BAD_JSON,
    E_TRACE_BAD_TYPE,
    E_TRACE_DUP_SCORER,
    E_TRACE_DUP_SPAN,
    E_TRACE_EMPTY,
    E_TRACE_EMPTY_FIELD,
    E_TRACE_EMPTY_LINE,
    E_TRACE_ENCODING,
    E_TRACE_FINAL_DUP,
    E_TRACE_FINAL_MISSING,
    E_TRACE_FINAL_NOT_LAST,
    E_TRACE_INDEX,
    E_TRACE_IO,
    E_TRACE_META_DUP,
    E_TRACE_META_MISSING,
    E_TRACE_MISSING_FIELD,
    E_TRACE_SCHEMA_VERSION,
    E_TRACE_UNKNOWN_RECORD,
    TraceError,
)
from trace_replay.model import canonical_json, load_trace, parse_trace, sha256_bytes


def _parse(records: list[dict[str, Any]]) -> Any:
    return parse_trace(dump(records), sha256="deadbeef")


def test_valid_trace_parses_fields() -> None:
    trace = _parse(records_copy())
    assert trace.trace_id == "t-good-001"
    assert trace.schema_version == 1
    assert len(trace.steps) == 2
    assert trace.final.index == 2
    assert trace.limits.max_steps == 4
    assert [spec.id for spec in trace.scorers] == ["e", "n", "s", "r", "q"]


def test_default_limits_when_absent() -> None:
    records = records_copy()
    del records[0]["limits"]
    trace = _parse(records)
    assert trace.limits.max_steps == 32
    assert trace.limits.max_repeats == 3


def test_sha256_is_over_raw_file_bytes(tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl"
    path.write_text(dump(records_copy()), encoding="utf-8")
    trace = load_trace(path)
    assert trace.sha256 == sha256_bytes(path.read_bytes())
    # 内容变化 -> 哈希必须变化
    records = records_copy()
    records[0]["trace_id"] = "t-good-002"
    path.write_text(dump(records), encoding="utf-8")
    assert load_trace(path).sha256 != trace.sha256


def test_canonical_json_is_sorted_and_compact() -> None:
    assert canonical_json({"b": 1, "a": [2, 3]}) == '{"a":[2,3],"b":1}'
    assert canonical_json({"b": 1, "a": 2}) == canonical_json({"a": 2, "b": 1})


def test_missing_file_raises_io_error(tmp_path: Path) -> None:
    with pytest.raises(TraceError) as caught:
        load_trace(tmp_path / "nope.jsonl")
    assert caught.value.code == E_TRACE_IO


def test_non_utf8_file_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "bad.jsonl"
    path.write_bytes(b'{"type":"trace",\xff}')
    with pytest.raises(TraceError) as caught:
        load_trace(path)
    assert caught.value.code == E_TRACE_ENCODING


def test_empty_file_is_rejected() -> None:
    with pytest.raises(TraceError) as caught:
        parse_trace("", sha256="x")
    assert caught.value.code == E_TRACE_EMPTY


def test_empty_line_is_rejected() -> None:
    with pytest.raises(TraceError) as caught:
        parse_trace(dump(records_copy()) + "\n\n", sha256="x")
    assert caught.value.code == E_TRACE_EMPTY_LINE


def test_bad_json_line_is_rejected() -> None:
    text = dump(records_copy()).replace('"latency_ms": 1', '"latency_ms": ', 1)
    with pytest.raises(TraceError) as caught:
        parse_trace(text, sha256="x")
    assert caught.value.code == E_TRACE_BAD_JSON


def test_missing_meta_is_rejected() -> None:
    records = records_copy()[1:]
    with pytest.raises(TraceError) as caught:
        _parse(records)
    assert caught.value.code == E_TRACE_META_MISSING


def test_duplicate_meta_is_rejected() -> None:
    records = records_copy()
    records.append(records[0])
    with pytest.raises(TraceError) as caught:
        _parse(records)
    assert caught.value.code == E_TRACE_META_DUP


def test_unknown_record_type_is_rejected() -> None:
    records = records_copy()
    records.insert(1, {"type": "metric", "value": 1})
    with pytest.raises(TraceError) as caught:
        _parse(records)
    assert caught.value.code == E_TRACE_UNKNOWN_RECORD


def test_unsupported_schema_version_is_rejected() -> None:
    records = records_copy()
    records[0]["schema_version"] = 2
    with pytest.raises(TraceError) as caught:
        _parse(records)
    assert caught.value.code == E_TRACE_SCHEMA_VERSION


def test_missing_step_field_is_rejected() -> None:
    records = records_copy()
    del records[1]["observation"]
    with pytest.raises(TraceError) as caught:
        _parse(records)
    assert caught.value.code == E_TRACE_MISSING_FIELD
    assert "observation" in caught.value.message


def test_step_index_mismatch_is_rejected() -> None:
    records = records_copy()
    records[2]["index"] = 0
    with pytest.raises(TraceError) as caught:
        _parse(records)
    assert caught.value.code == E_TRACE_INDEX


def test_final_index_mismatch_is_rejected() -> None:
    records = records_copy()
    records[3]["index"] = 1
    with pytest.raises(TraceError) as caught:
        _parse(records)
    assert caught.value.code == E_TRACE_INDEX


def test_duplicate_span_id_is_rejected() -> None:
    records = records_copy()
    records[2]["span_id"] = "s0"
    with pytest.raises(TraceError) as caught:
        _parse(records)
    assert caught.value.code == E_TRACE_DUP_SPAN


def test_duplicate_scorer_id_is_rejected() -> None:
    records = records_copy()
    records[0]["scorers"].append(dict(records[0]["scorers"][0]))
    with pytest.raises(TraceError) as caught:
        _parse(records)
    assert caught.value.code == E_TRACE_DUP_SCORER


def test_truncated_trace_without_final_is_incomplete() -> None:
    records = records_copy()[:-1]
    with pytest.raises(TraceError) as caught:
        _parse(records)
    assert caught.value.code == E_TRACE_FINAL_MISSING


def test_duplicate_final_is_rejected() -> None:
    records = records_copy()
    records.append(dict(records[-1]))
    with pytest.raises(TraceError) as caught:
        _parse(records)
    assert caught.value.code == E_TRACE_FINAL_DUP


def test_step_after_final_is_rejected() -> None:
    records = records_copy()
    records.append(records[1])
    with pytest.raises(TraceError) as caught:
        _parse(records)
    assert caught.value.code == E_TRACE_FINAL_NOT_LAST


def test_empty_answer_object_is_accepted_but_typed() -> None:
    records = records_copy()
    records[-1]["answer"] = {}
    trace = _parse(records)
    assert trace.final.answer == {}


def test_answer_must_be_object() -> None:
    records = records_copy()
    records[-1]["answer"] = "2040"
    with pytest.raises(TraceError) as caught:
        _parse(records)
    assert caught.value.code == E_TRACE_BAD_TYPE


def test_nonfinite_number_is_rejected() -> None:
    text = dump(records_copy()).replace('"value": 2040}}', '"value": NaN}}', 1)
    with pytest.raises(TraceError) as caught:
        parse_trace(text, sha256="x")
    assert caught.value.code == E_TRACE_BAD_TYPE


def test_negative_latency_is_rejected() -> None:
    records = records_copy()
    records[1]["latency_ms"] = -1
    with pytest.raises(TraceError) as caught:
        _parse(records)
    assert caught.value.code == E_TRACE_BAD_TYPE


def test_empty_scorers_is_rejected() -> None:
    records = records_copy()
    records[0]["scorers"] = []
    with pytest.raises(TraceError) as caught:
        _parse(records)
    assert caught.value.code == E_TRACE_EMPTY_FIELD


def test_json_roundtrip_of_scorer_config() -> None:
    records = records_copy()
    text = dump(records)
    assert json.loads(text.splitlines()[0])["scorers"][0]["type"] == "exact_match"
