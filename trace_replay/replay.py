"""确定性回放器。

输入是解析好的 ``Trace``，输出是重新算出的工具调用序列与最终答案。
不发起任何网络/模型调用：recorded 模式直接使用录制观测，stub 模式额外用本地
stub 重算一次并逐字段比对（发现工具实现漂移）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .errors import (
    E_TOOL_REPLAY_DIVERGENCE,
    E_TRACE_LOOP,
    E_TRACE_STEP_LIMIT,
    TraceError,
)
from .model import Step, Trace, canonical_json
from .tools import ToolRegistry, run_stub, validate_args, validate_observation

#: 循环检测覆盖的周期（1=连续相同调用，2/3=交替循环）。
LOOP_PERIODS: tuple[int, ...] = (1, 2, 3)


@dataclass(frozen=True)
class CallRecord:
    index: int
    span_id: str
    tool: str
    stub_checked: bool
    stub_matched: bool


@dataclass(frozen=True)
class ReplayOutcome:
    calls: tuple[CallRecord, ...]
    tool_sequence: tuple[str, ...]
    answer: dict[str, Any]
    stub_checks: int


def _signature(step: Step) -> str:
    return canonical_json({"tool": step.tool, "args": step.args})


def _loop_period(signatures: list[str], max_repeats: int) -> int | None:
    """返回触发循环的周期长度；没有则返回 None。

    判定口径：某周期 ``p`` 连续完整重复 ``max_repeats + 1`` 次即算循环。
    """
    for period in LOOP_PERIODS:
        need = period * (max_repeats + 1)
        if len(signatures) < need:
            continue
        window = signatures[-need:]
        cycle = window[:period]
        if window == cycle * (max_repeats + 1):
            return period
    return None


def _values_match(expected: Any, actual: Any) -> bool:
    """递归比较；数值按数值相等（5 == 5.0），bool 与数字严格区分。"""
    if isinstance(expected, bool) or isinstance(actual, bool):
        return isinstance(expected, bool) and isinstance(actual, bool) and expected == actual
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        return float(expected) == float(actual)
    if isinstance(expected, dict) and isinstance(actual, dict):
        if set(expected) != set(actual):
            return False
        return all(_values_match(expected[key], actual[key]) for key in expected)
    if isinstance(expected, list) and isinstance(actual, list):
        if len(expected) != len(actual):
            return False
        return all(_values_match(a, b) for a, b in zip(expected, actual))
    return bool(expected == actual)


def _diverged_fields(stub_output: dict[str, Any], recorded: dict[str, Any]) -> list[str]:
    """只比较 stub 真正产出的字段，录制端可以额外附带 status 等元数据。"""
    bad: list[str] = []
    for key in sorted(stub_output):
        if key not in recorded or not _values_match(stub_output[key], recorded[key]):
            bad.append(key)
    return bad


def replay_trace(
    trace: Trace,
    registry: ToolRegistry,
    stub_mode: bool = True,
) -> ReplayOutcome:
    if len(trace.steps) > trace.limits.max_steps:
        raise TraceError(
            E_TRACE_STEP_LIMIT,
            f"步数 {len(trace.steps)} 超过 max_steps={trace.limits.max_steps}，拒绝继续回放",
            f"step[{trace.limits.max_steps}]",
        )

    calls: list[CallRecord] = []
    signatures: list[str] = []
    stub_checks = 0

    for step in trace.steps:
        location = f"step[{step.index}]"
        spec = registry.get(step.tool)
        validate_args(spec, step.args, location)
        validate_observation(spec, step.observation, location)

        signatures.append(_signature(step))
        period = _loop_period(signatures, trace.limits.max_repeats)
        if period is not None:
            raise TraceError(
                E_TRACE_LOOP,
                f"检测到周期 {period} 的循环调用，连续重复超过 {trace.limits.max_repeats} 次",
                location,
            )

        checked = False
        matched = False
        if stub_mode and spec.stub is not None:
            stub_output = run_stub(spec, step.args, location)
            stub_checks += 1
            checked = True
            bad_fields = _diverged_fields(stub_output, step.observation)
            if bad_fields:
                raise TraceError(
                    E_TOOL_REPLAY_DIVERGENCE,
                    f"stub 重算与录制观测不一致的字段：{', '.join(bad_fields)}",
                    location,
                )
            matched = True

        calls.append(
            CallRecord(
                index=step.index,
                span_id=step.span_id,
                tool=step.tool,
                stub_checked=checked,
                stub_matched=matched,
            )
        )

    return ReplayOutcome(
        calls=tuple(calls),
        tool_sequence=tuple(step.tool for step in trace.steps),
        answer=trace.final.answer,
        stub_checks=stub_checks,
    )
