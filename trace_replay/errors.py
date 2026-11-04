"""fail-closed 错误码。

设计原则：
- 每个错误码含义唯一，且长期稳定（测试会锁住编码集合）。
- 任何异常都以 TraceError 抛出，最终在报告里落成 ``{"code", "message", "location"}``，
  绝不静默跳过、绝不猜默认值。
"""

from __future__ import annotations

from typing import Any

# --- 轨迹文件与 JSONL 结构 ---
E_TRACE_IO = "E_TRACE_IO"
E_TRACE_ENCODING = "E_TRACE_ENCODING"
E_TRACE_EMPTY = "E_TRACE_EMPTY"
E_TRACE_EMPTY_LINE = "E_TRACE_EMPTY_LINE"
E_TRACE_BAD_JSON = "E_TRACE_BAD_JSON"
E_TRACE_BAD_TYPE = "E_TRACE_BAD_TYPE"
E_TRACE_MISSING_FIELD = "E_TRACE_MISSING_FIELD"
E_TRACE_EMPTY_FIELD = "E_TRACE_EMPTY_FIELD"
E_TRACE_UNKNOWN_RECORD = "E_TRACE_UNKNOWN_RECORD"
E_TRACE_SCHEMA_VERSION = "E_TRACE_SCHEMA_VERSION"
E_TRACE_META_MISSING = "E_TRACE_META_MISSING"
E_TRACE_META_DUP = "E_TRACE_META_DUP"
E_TRACE_DUP_SPAN = "E_TRACE_DUP_SPAN"
E_TRACE_DUP_SCORER = "E_TRACE_DUP_SCORER"
E_TRACE_INDEX = "E_TRACE_INDEX"
E_TRACE_FINAL_MISSING = "E_TRACE_FINAL_MISSING"
E_TRACE_FINAL_DUP = "E_TRACE_FINAL_DUP"
E_TRACE_FINAL_NOT_LAST = "E_TRACE_FINAL_NOT_LAST"

# --- 回放阶段 ---
E_TRACE_UNKNOWN_TOOL = "E_TRACE_UNKNOWN_TOOL"
E_TRACE_TOOL_ARGS = "E_TRACE_TOOL_ARGS"
E_TRACE_OBS_SCHEMA = "E_TRACE_OBS_SCHEMA"
E_TRACE_STEP_LIMIT = "E_TRACE_STEP_LIMIT"
E_TRACE_LOOP = "E_TRACE_LOOP"
E_TRACE_SCHEMA_UNKNOWN_TYPE = "E_TRACE_SCHEMA_UNKNOWN_TYPE"
E_TOOL_STUB_ERROR = "E_TOOL_STUB_ERROR"
E_TOOL_REPLAY_DIVERGENCE = "E_TOOL_REPLAY_DIVERGENCE"

# --- 评分阶段 ---
E_SCORER_UNKNOWN = "E_SCORER_UNKNOWN"
E_SCORER_CONFIG = "E_SCORER_CONFIG"
E_SCORER_INTERNAL = "E_SCORER_INTERNAL"

#: 错误码 -> 含义（README 与 ``list-errors`` 共用同一份文案，避免文档漂移）。
ERROR_CODES: dict[str, str] = {
    E_TRACE_IO: "轨迹文件读不到",
    E_TRACE_ENCODING: "轨迹文件不是合法 UTF-8",
    E_TRACE_EMPTY: "轨迹文件为空",
    E_TRACE_EMPTY_LINE: "JSONL 中出现空行",
    E_TRACE_BAD_JSON: "某一行不是合法 JSON",
    E_TRACE_BAD_TYPE: "字段类型不符（含 NaN/Infinity 等非法 JSON 值）",
    E_TRACE_MISSING_FIELD: "缺少必填字段",
    E_TRACE_EMPTY_FIELD: "必填字符串为空",
    E_TRACE_UNKNOWN_RECORD: "type 不是 trace/step/final",
    E_TRACE_SCHEMA_VERSION: "schema_version 不受支持",
    E_TRACE_META_MISSING: "缺少且必须位于首行的 trace 头",
    E_TRACE_META_DUP: "出现多个 trace 头",
    E_TRACE_DUP_SPAN: "span_id 重复",
    E_TRACE_DUP_SCORER: "评分器 id 重复",
    E_TRACE_INDEX: "index 与行序不一致",
    E_TRACE_FINAL_MISSING: "轨迹被截断，没有 final 行（判 INCOMPLETE）",
    E_TRACE_FINAL_DUP: "出现多个 final 行",
    E_TRACE_FINAL_NOT_LAST: "final 之后仍有 step",
    E_TRACE_UNKNOWN_TOOL: "调用了未注册的工具",
    E_TRACE_TOOL_ARGS: "工具入参缺字段或类型不符",
    E_TRACE_OBS_SCHEMA: "录制观测缺字段或类型不符",
    E_TRACE_STEP_LIMIT: "步数超过 max_steps",
    E_TRACE_LOOP: "检测到循环调用（周期 1/2/3 连续重复）",
    E_TRACE_SCHEMA_UNKNOWN_TYPE: "工具 schema 声明了未知类型名",
    E_TOOL_STUB_ERROR: "本地 stub 执行失败或返回非 object",
    E_TOOL_REPLAY_DIVERGENCE: "stub 重算结果与录制观测不一致",
    E_SCORER_UNKNOWN: "声明了未注册的评分器类型",
    E_SCORER_CONFIG: "评分器配置非法",
    E_SCORER_INTERNAL: "评分流程内部异常（已 fail-closed 收敛为 FAIL）",
}


class TraceError(Exception):
    """所有可解释失败的统一载体。"""

    def __init__(self, code: str, message: str, location: str | None = None) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.location = location

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.location is not None:
            payload["location"] = self.location
        return payload
