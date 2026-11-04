"""评测主流程：解析 -> 回放 -> 评分 -> 报告（纯函数，可重复调用）。"""

from __future__ import annotations

from typing import Any

from .errors import E_SCORER_INTERNAL, TraceError
from .model import Trace
from .replay import ReplayOutcome, replay_trace
from .scorers import Check, ScoreContext, build_scorer
from .tools import ToolRegistry, default_registry

TOOL_VERSION = "0.1.0"
REPORT_SCHEMA = "trace-replay/report/v1"


def evaluate(
    trace: Trace,
    registry: ToolRegistry | None = None,
    stub_mode: bool = True,
) -> dict[str, Any]:
    """返回确定性报告 dict；调用两次必须得到完全相同的对象。"""
    tools = default_registry() if registry is None else registry

    outcome: ReplayOutcome | None = None
    checks: tuple[Check, ...] = ()
    error: dict[str, Any] | None = None
    try:
        outcome = replay_trace(trace, tools, stub_mode=stub_mode)
        context = ScoreContext(answer=outcome.answer, tool_sequence=outcome.tool_sequence)
        scorers = [build_scorer(spec.id, spec.type, spec.config) for spec in trace.scorers]
        checks = tuple(scorer.check(context) for scorer in scorers)
    except TraceError as exc:
        error = exc.to_dict()
    except Exception as exc:  # 评分器自身 bug 也必须 fail-closed，不能把异常漏给调用方
        error = {"code": E_SCORER_INTERNAL, "message": f"评分流程内部错误：{exc!r}"}

    passed = error is None and all(check.passed for check in checks)
    passed_count = sum(1 for check in checks if check.passed)

    replay_block: dict[str, Any] | None = None
    if outcome is not None:
        replay_block = {
            "tool_sequence": list(outcome.tool_sequence),
            "stub_checks": outcome.stub_checks,
            "calls": [
                {
                    "index": call.index,
                    "span_id": call.span_id,
                    "tool": call.tool,
                    "stub_checked": call.stub_checked,
                    "stub_matched": call.stub_matched,
                }
                for call in outcome.calls
            ],
        }

    return {
        "schema": REPORT_SCHEMA,
        "tool_version": TOOL_VERSION,
        "stub_mode": stub_mode,
        "trace": {
            "trace_id": trace.trace_id,
            "task": trace.task,
            "sha256": trace.sha256,
            "schema_version": trace.schema_version,
            "steps": len(trace.steps),
            "max_steps": trace.limits.max_steps,
            "max_repeats": trace.limits.max_repeats,
        },
        "verdict": "PASS" if passed else "FAIL",
        "error": error,
        "replay": replay_block,
        "checks": [check.to_dict() for check in checks],
        "summary": {
            "total": len(checks),
            "passed": passed_count,
            "failed": len(checks) - passed_count,
        },
    }


def error_report(error: dict[str, Any], stub_mode: bool = True) -> dict[str, Any]:
    """连轨迹都解析不出来时的最小 FAIL 报告。

    不写文件路径/时间戳，保证同一坏输入两次运行得到同一份报告。
    """
    return {
        "schema": REPORT_SCHEMA,
        "tool_version": TOOL_VERSION,
        "stub_mode": stub_mode,
        "trace": None,
        "verdict": "FAIL",
        "error": error,
        "replay": None,
        "checks": [],
        "summary": {"total": 0, "passed": 0, "failed": 0},
    }
