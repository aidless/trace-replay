"""报告序列化与落盘。

关键约定：``report.json`` 里不出现时间戳、耗时、主机名、绝对路径，
因此同一输入重放任意次数都应得到字节完全相同的 ``report.json``。
易变信息只写进 ``meta.json``。
"""

from __future__ import annotations

import hashlib
import json
import platform
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .engine import REPORT_SCHEMA, TOOL_VERSION


def pretty_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n"


def report_sha256(report: dict[str, Any]) -> str:
    """对**实际落盘的 report.json 字节**求哈希，可直接用 ``sha256sum`` 复核。"""
    return hashlib.sha256(pretty_json(report).encode("utf-8")).hexdigest()


def write_run(
    report: dict[str, Any],
    out_dir: str | Path,
    trace_sha256: str,
    elapsed_ms: int,
) -> tuple[Path, Path]:
    target = Path(out_dir)
    target.mkdir(parents=True, exist_ok=True)

    report_text = pretty_json(report)
    report_path = target / "report.json"
    report_path.write_text(report_text, encoding="utf-8")

    meta: dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "tool_version": TOOL_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "elapsed_ms": elapsed_ms,
        "python_version": platform.python_version(),
        "trace_sha256": trace_sha256,
        "report_sha256": report_sha256(report),
    }
    meta_path = target / "meta.json"
    meta_path.write_text(pretty_json(meta), encoding="utf-8")
    return report_path, meta_path
