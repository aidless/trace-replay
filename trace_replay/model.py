"""轨迹数据模型与 JSONL 解析。

JSONL 三行类型（顺序固定，便于确定性回放）：

1. ``trace``  头，必须是第 1 行，且只能有一个：
   ``{"type":"trace","schema_version":1,"trace_id":"...","task":"...",
      "limits":{"max_steps":32,"max_repeats":3},
      "scorers":[{"id":"...","type":"exact_match","config":{...}}]}``
2. ``step``  工具调用，``index`` 必须等于它前面 step 的个数（从 0 起）：
   ``{"type":"step","index":0,"span_id":"s0","tool":"search",
      "args":{...},"observation":{...},"latency_ms":12}``
3. ``final`` 最终答案，必须是最后一行，``index`` 必须等于 step 总数：
   ``{"type":"final","index":2,"answer":{...}}``

``observation`` 是录制下来的工具返回，回放时不再发起任何真实调用。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import (
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

SCHEMA_VERSION = 1
DEFAULT_MAX_STEPS = 32
DEFAULT_MAX_REPEATS = 3


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def canonical_json(value: Any) -> str:
    """唯一的规范化序列化口径：键排序、无空格、拒绝 NaN/Infinity。"""
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )


def _object(value: Any, location: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TraceError(E_TRACE_BAD_TYPE, "期望 JSON object", location)
    out: dict[str, Any] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise TraceError(E_TRACE_BAD_TYPE, "object 的键必须是字符串", location)
        out[key] = item
    return out


def _require(obj: dict[str, Any], key: str, location: str) -> Any:
    if key not in obj:
        raise TraceError(E_TRACE_MISSING_FIELD, f"缺少字段 {key!r}", location)
    return obj[key]


def _string(obj: dict[str, Any], key: str, location: str) -> str:
    value = _require(obj, key, location)
    if not isinstance(value, str):
        raise TraceError(E_TRACE_BAD_TYPE, f"字段 {key!r} 必须是字符串", location)
    if value == "":
        raise TraceError(E_TRACE_EMPTY_FIELD, f"字段 {key!r} 不能为空", location)
    return value


def _integer(obj: dict[str, Any], key: str, location: str) -> int:
    value = _require(obj, key, location)
    if isinstance(value, bool) or not isinstance(value, int):
        raise TraceError(E_TRACE_BAD_TYPE, f"字段 {key!r} 必须是整数", location)
    return value


def _json_object(obj: dict[str, Any], key: str, location: str) -> dict[str, Any]:
    return _object(_require(obj, key, location), f"{location} 的 {key}")


def _assert_json_safe(value: Any, location: str) -> None:
    try:
        canonical_json(value)
    except (TypeError, ValueError) as exc:
        raise TraceError(
            E_TRACE_BAD_TYPE, f"含非法 JSON 值（NaN/Infinity 或不可序列化对象）：{exc}", location
        ) from exc


@dataclass(frozen=True)
class Limits:
    max_steps: int
    max_repeats: int


@dataclass(frozen=True)
class ScorerSpec:
    id: str
    type: str
    config: dict[str, Any]


@dataclass(frozen=True)
class Step:
    index: int
    span_id: str
    tool: str
    args: dict[str, Any]
    observation: dict[str, Any]
    latency_ms: int


@dataclass(frozen=True)
class Final:
    index: int
    answer: dict[str, Any]


@dataclass(frozen=True)
class Trace:
    trace_id: str
    task: str
    schema_version: int
    limits: Limits
    scorers: tuple[ScorerSpec, ...]
    steps: tuple[Step, ...]
    final: Final
    sha256: str


def _parse_limits(meta: dict[str, Any], location: str) -> Limits:
    if "limits" not in meta:
        return Limits(max_steps=DEFAULT_MAX_STEPS, max_repeats=DEFAULT_MAX_REPEATS)
    raw = _object(meta["limits"], f"{location} 的 limits")
    max_steps = _integer(raw, "max_steps", f"{location} 的 limits") if "max_steps" in raw else DEFAULT_MAX_STEPS
    max_repeats = (
        _integer(raw, "max_repeats", f"{location} 的 limits") if "max_repeats" in raw else DEFAULT_MAX_REPEATS
    )
    if max_steps <= 0:
        raise TraceError(E_TRACE_BAD_TYPE, "max_steps 必须 > 0", f"{location} 的 limits")
    if max_repeats <= 0:
        raise TraceError(E_TRACE_BAD_TYPE, "max_repeats 必须 > 0", f"{location} 的 limits")
    return Limits(max_steps=max_steps, max_repeats=max_repeats)


def _parse_scorers(meta: dict[str, Any], location: str) -> tuple[ScorerSpec, ...]:
    raw_value = _require(meta, "scorers", location)
    if not isinstance(raw_value, list):
        raise TraceError(E_TRACE_BAD_TYPE, "scorers 必须是数组", location)
    if not raw_value:
        raise TraceError(E_TRACE_EMPTY_FIELD, "scorers 不能为空", location)
    specs: list[ScorerSpec] = []
    seen: set[str] = set()
    for i, item in enumerate(raw_value):
        item_location = f"{location}.scorers[{i}]"
        raw = _object(item, item_location)
        scorer_id = _string(raw, "id", item_location)
        if scorer_id in seen:
            raise TraceError(E_TRACE_DUP_SCORER, f"评分器 id 重复：{scorer_id}", item_location)
        seen.add(scorer_id)
        scorer_type = _string(raw, "type", item_location)
        config = _object(raw["config"], f"{item_location}.config") if "config" in raw else {}
        _assert_json_safe(config, f"{item_location}.config")
        specs.append(ScorerSpec(id=scorer_id, type=scorer_type, config=config))
    return tuple(specs)


def _parse_step(obj: dict[str, Any], expected_index: int, location: str) -> Step:
    index = _integer(obj, "index", location)
    if index != expected_index:
        raise TraceError(
            E_TRACE_INDEX, f"index 应为 {expected_index}，实际 {index}", location
        )
    span_id = _string(obj, "span_id", location)
    tool = _string(obj, "tool", location)
    args = _json_object(obj, "args", location)
    observation = _json_object(obj, "observation", location)
    latency_ms = _integer(obj, "latency_ms", location)
    if latency_ms < 0:
        raise TraceError(E_TRACE_BAD_TYPE, "latency_ms 必须 >= 0", location)
    _assert_json_safe(args, f"{location} 的 args")
    _assert_json_safe(observation, f"{location} 的 observation")
    return Step(
        index=index,
        span_id=span_id,
        tool=tool,
        args=args,
        observation=observation,
        latency_ms=latency_ms,
    )


def _parse_final(obj: dict[str, Any], expected_index: int, location: str) -> Final:
    index = _integer(obj, "index", location)
    if index != expected_index:
        raise TraceError(E_TRACE_INDEX, f"final index 应为 {expected_index}，实际 {index}", location)
    answer = _json_object(obj, "answer", location)
    _assert_json_safe(answer, f"{location} 的 answer")
    return Final(index=index, answer=answer)


def parse_trace(text: str, sha256: str = "") -> Trace:
    if text.strip() == "":
        raise TraceError(E_TRACE_EMPTY, "轨迹文件为空", "file")

    records: list[tuple[int, dict[str, Any]]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if line.strip() == "":
            raise TraceError(E_TRACE_EMPTY_LINE, "空行会破坏行号定位，显式判失败", f"line {lineno}")
        try:
            raw: Any = json.loads(line)
        except json.JSONDecodeError as exc:
            raise TraceError(
                E_TRACE_BAD_JSON, f"{exc.msg}（第 {exc.colno} 列）", f"line {lineno}"
            ) from exc
        records.append((lineno, _object(raw, f"line {lineno}")))

    meta: dict[str, Any] | None = None
    meta_line = 0
    steps: list[Step] = []
    final: Final | None = None

    for lineno, obj in records:
        location = f"line {lineno}"
        record_type = _string(obj, "type", location)
        if record_type == "trace":
            if meta is not None:
                raise TraceError(E_TRACE_META_DUP, "出现多个 trace 头", location)
            if lineno != 1:
                raise TraceError(E_TRACE_META_MISSING, "trace 头必须是第 1 行", location)
            meta = obj
            meta_line = lineno
        elif record_type == "step":
            if meta is None:
                raise TraceError(E_TRACE_META_MISSING, "step 之前没有 trace 头", location)
            if final is not None:
                raise TraceError(E_TRACE_FINAL_NOT_LAST, "final 之后不允许再有 step", location)
            steps.append(_parse_step(obj, len(steps), location))
        elif record_type == "final":
            if meta is None:
                raise TraceError(E_TRACE_META_MISSING, "final 之前没有 trace 头", location)
            if final is not None:
                raise TraceError(E_TRACE_FINAL_DUP, "出现多个 final", location)
            final = _parse_final(obj, len(steps), location)
        else:
            raise TraceError(E_TRACE_UNKNOWN_RECORD, f"未知记录类型 {record_type!r}", location)

    if meta is None:
        raise TraceError(E_TRACE_META_MISSING, "缺少 trace 头", "file")
    if final is None:
        raise TraceError(E_TRACE_FINAL_MISSING, "轨迹被截断，没有 final 行", "file")

    meta_location = f"line {meta_line}"
    version = _integer(meta, "schema_version", meta_location)
    if version != SCHEMA_VERSION:
        raise TraceError(
            E_TRACE_SCHEMA_VERSION,
            f"只支持 schema_version={SCHEMA_VERSION}，实际 {version}",
            meta_location,
        )

    span_ids: set[str] = set()
    for step in steps:
        if step.span_id in span_ids:
            raise TraceError(E_TRACE_DUP_SPAN, f"span_id 重复：{step.span_id}", f"step[{step.index}]")
        span_ids.add(step.span_id)

    return Trace(
        trace_id=_string(meta, "trace_id", meta_location),
        task=_string(meta, "task", meta_location),
        schema_version=version,
        limits=_parse_limits(meta, meta_location),
        scorers=_parse_scorers(meta, meta_location),
        steps=tuple(steps),
        final=final,
        sha256=sha256,
    )


def load_trace(path: str | Path) -> Trace:
    target = Path(path)
    try:
        raw = target.read_bytes()
    except OSError as exc:
        raise TraceError(E_TRACE_IO, f"读取 {target} 失败：{exc}", "file") from exc
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise TraceError(E_TRACE_ENCODING, f"{target} 不是合法 UTF-8：{exc}", "file") from exc
    return parse_trace(text, sha256=sha256_bytes(raw))
