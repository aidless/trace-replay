"""测试共用的轨迹构造器（不引入任何第三方依赖）。"""

from __future__ import annotations

import copy
import json
from typing import Any


def good_records() -> list[dict[str, Any]]:
    """一条能同时通过 5 个内置评分器的轨迹。"""
    return [
        {
            "type": "trace",
            "schema_version": 1,
            "trace_id": "t-good-001",
            "task": "查人口并给出数值答案",
            "limits": {"max_steps": 4, "max_repeats": 2},
            "scorers": [
                {"id": "e", "type": "exact_match", "config": {"path": "text", "expected": "2040"}},
                {
                    "id": "n",
                    "type": "numeric_tolerance",
                    "config": {"path": "value", "expected": 2040, "abs_tol": 0},
                },
                {
                    "id": "s",
                    "type": "structural_fields",
                    "config": {"path": "", "fields": {"text": "str", "value": "int"}, "allow_extra": False},
                },
                {
                    "id": "r",
                    "type": "regex_rubric",
                    "config": {"rules": [{"id": "r1", "path": "text", "pattern": r"^\d{4}$"}]},
                },
                {"id": "q", "type": "tool_sequence", "config": {"expected": ["search", "calc"]}},
            ],
        },
        {
            "type": "step",
            "index": 0,
            "span_id": "s0",
            "tool": "search",
            "args": {"query": "population 2040"},
            "observation": {"status": "ok", "result": "2040"},
            "latency_ms": 1,
        },
        {
            "type": "step",
            "index": 1,
            "span_id": "s1",
            "tool": "calc",
            "args": {"expression": "2040"},
            "observation": {"status": "ok", "value": 2040},
            "latency_ms": 1,
        },
        {"type": "final", "index": 2, "answer": {"text": "2040", "value": 2040}},
    ]


def records_copy() -> list[dict[str, Any]]:
    return copy.deepcopy(good_records())


def dump(records: list[dict[str, Any]]) -> str:
    return "\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n"


def write_trace(path: Any, records: list[dict[str, Any]]) -> Any:
    path.write_text(dump(records), encoding="utf-8")
    return path
