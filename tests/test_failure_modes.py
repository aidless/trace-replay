"""失效模式用例表：一行一个真实会发生的坏情况，每个都可直接运行。

约定：每个 mutator 接收一份正常轨迹 records，返回**被破坏后的 JSONL 文本**；
断言该输入在解析或回放阶段被 fail-closed 拦下，并给出预期错误码。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

import pytest

from tests._helpers import dump, records_copy
from trace_replay.engine import evaluate
from trace_replay.errors import (
    E_SCORER_UNKNOWN,
    E_TOOL_REPLAY_DIVERGENCE,
    E_TRACE_BAD_JSON,
    E_TRACE_BAD_TYPE,
    E_TRACE_DUP_SPAN,
    E_TRACE_EMPTY_LINE,
    E_TRACE_FINAL_MISSING,
    E_TRACE_FINAL_NOT_LAST,
    E_TRACE_INDEX,
    E_TRACE_LOOP,
    E_TRACE_META_MISSING,
    E_TRACE_MISSING_FIELD,
    E_TRACE_OBS_SCHEMA,
    E_TRACE_SCHEMA_VERSION,
    E_TRACE_STEP_LIMIT,
    E_TRACE_UNKNOWN_TOOL,
    TraceError,
)
from trace_replay.model import load_trace

Mutator = Callable[[list[dict[str, Any]]], str]


def _ok(records: list[dict[str, Any]]) -> str:
    return dump(records)


def _drop_final(records: list[dict[str, Any]]) -> str:
    return dump(records[:-1])


def _unknown_tool(records: list[dict[str, Any]]) -> str:
    records[1]["tool"] = "http_get"
    return dump(records)


def _obs_wrong_type(records: list[dict[str, Any]]) -> str:
    records[1]["observation"] = {"status": "ok", "result": 2040}
    return dump(records)


def _obs_missing_key(records: list[dict[str, Any]]) -> str:
    records[1]["observation"] = {"result": "2040"}
    return dump(records)


def _step_limit(records: list[dict[str, Any]]) -> str:
    records[0]["limits"] = {"max_steps": 1, "max_repeats": 3}
    return dump(records)


def _loop(records: list[dict[str, Any]]) -> str:
    records[0]["limits"] = {"max_steps": 8, "max_repeats": 1}
    records.insert(2, dict(records[1], index=1, span_id="s1b"))
    records[2]["index"] = 1
    records[3]["index"] = 2
    records[-1]["index"] = 3
    return dump(records)


def _missing_observation(records: list[dict[str, Any]]) -> str:
    del records[1]["observation"]
    return dump(records)


def _dup_span(records: list[dict[str, Any]]) -> str:
    records[2]["span_id"] = "s0"
    return dump(records)


def _index_gap(records: list[dict[str, Any]]) -> str:
    records[2]["index"] = 3
    return dump(records)


def _unknown_scorer(records: list[dict[str, Any]]) -> str:
    records[0]["scorers"][0] = {"id": "judge", "type": "llm_judge", "config": {}}
    return dump(records)


def _stub_divergence(records: list[dict[str, Any]]) -> str:
    records[2]["args"]["expression"] = "2+3"
    records[2]["observation"]["value"] = 7
    return dump(records)


def _nonfinite(records: list[dict[str, Any]]) -> str:
    return dump(records).replace('"value": 2040}}', '"value": NaN}}', 1)


def _meta_not_first(records: list[dict[str, Any]]) -> str:
    return "\n".join([json.dumps(records[1]), json.dumps(records[0])] + [json.dumps(r) for r in records[2:]])


def _empty_line(records: list[dict[str, Any]]) -> str:
    lines = dump(records).splitlines()
    return "\n".join([lines[0], "", *lines[1:]])


def _final_not_last(records: list[dict[str, Any]]) -> str:
    records.append(dict(records[1], index=3, span_id="s9"))
    return dump(records)


def _schema_version(records: list[dict[str, Any]]) -> str:
    records[0]["schema_version"] = 2
    return dump(records)


def _bad_json(records: list[dict[str, Any]]) -> str:
    text = dump(records)
    return text[: text.rfind("}")] + "\n"


FAILURE_MODES: list[tuple[str, str, Mutator, str]] = [
    ("bad_json", "录制器写了半行 JSON（进程被杀）", _bad_json, E_TRACE_BAD_JSON),
    ("empty_line", "手工编辑时在文件中间留下空行", _empty_line, E_TRACE_EMPTY_LINE),
    ("truncated_no_final", "轨迹被截断，没有 final 行", _drop_final, E_TRACE_FINAL_MISSING),
    ("meta_not_first", "trace 头被挪到第 2 行", _meta_not_first, E_TRACE_META_MISSING),
    ("missing_field", "step 少了 observation", _missing_observation, E_TRACE_MISSING_FIELD),
    ("dup_span", "span_id 被复用，无法定位是哪次调用", _dup_span, E_TRACE_DUP_SPAN),
    ("index_gap", "index 与行序不一致（中间丢了一步）", _index_gap, E_TRACE_INDEX),
    ("final_not_last", "final 之后又追加了一条 step", _final_not_last, E_TRACE_FINAL_NOT_LAST),
    ("schema_version", "旧录制器写出的 schema_version=2", _schema_version, E_TRACE_SCHEMA_VERSION),
    ("nonfinite", "答案里混入 NaN", _nonfinite, E_TRACE_BAD_TYPE),
    ("unknown_tool", "agent 调用了未注册的 http_get", _unknown_tool, E_TRACE_UNKNOWN_TOOL),
    ("obs_wrong_type", "观测字段类型漂移（result 变成 int）", _obs_wrong_type, E_TRACE_OBS_SCHEMA),
    ("obs_missing_key", "观测缺 status 字段", _obs_missing_key, E_TRACE_OBS_SCHEMA),
    ("step_limit", "max_steps=1 却录了 2 步", _step_limit, E_TRACE_STEP_LIMIT),
    ("loop", "同一查询连续重复调用触发循环", _loop, E_TRACE_LOOP),
    ("unknown_scorer", "配置里引用了不存在的 llm_judge 评分器", _unknown_scorer, E_SCORER_UNKNOWN),
    ("stub_divergence", "calc 录制值与本地重算不一致", _stub_divergence, E_TOOL_REPLAY_DIVERGENCE),
]


def _error_code(text: str, tmp_path: Path) -> str:
    path = tmp_path / "trace.jsonl"
    path.write_text(text, encoding="utf-8")
    try:
        trace = load_trace(path)
    except TraceError as exc:
        return exc.code
    report = evaluate(trace)
    error = report["error"]
    assert isinstance(error, dict), "应当判 FAIL 并给出错误码"
    return str(error["code"])


@pytest.mark.parametrize(
    ("case", "description", "mutator", "expected"),
    FAILURE_MODES,
    ids=[case for case, _, _, _ in FAILURE_MODES],
)
def test_failure_mode_is_closed_and_coded(
    case: str, description: str, mutator: Mutator, expected: str, tmp_path: Path
) -> None:
    assert description, f"{case} 必须写明真实场景"
    assert _error_code(mutator(records_copy()), tmp_path) == expected


def test_failure_mode_table_has_at_least_ten_entries() -> None:
    assert len(FAILURE_MODES) >= 10
    assert len({case for case, _, _, _ in FAILURE_MODES}) == len(FAILURE_MODES)


def test_every_failure_code_is_registered() -> None:
    from trace_replay.errors import ERROR_CODES

    for _, _, _, code in FAILURE_MODES:
        assert code in ERROR_CODES, f"{code} 未登记为稳定错误码"
