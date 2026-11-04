"""本地工具注册表与确定性 stub。

回放绝不发起真实网络请求：工具要么只回放录制观测（recorded 模式），
要么用注册的本地 stub 重算一次再和录制观测比对（stub 模式，可发现工具实现漂移）。
"""

from __future__ import annotations

import ast
import operator
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from .errors import (
    E_TOOL_STUB_ERROR,
    E_TRACE_OBS_SCHEMA,
    E_TRACE_SCHEMA_UNKNOWN_TYPE,
    E_TRACE_TOOL_ARGS,
    E_TRACE_UNKNOWN_TOOL,
    TraceError,
)

#: 类型名 -> 判定函数。类型名会写进轨迹文件的 schema，必须稳定。
TYPE_CHECKS: dict[str, Callable[[Any], bool]] = {
    "str": lambda value: isinstance(value, str),
    "int": lambda value: isinstance(value, int) and not isinstance(value, bool),
    "number": lambda value: isinstance(value, (int, float)) and not isinstance(value, bool),
    "bool": lambda value: isinstance(value, bool),
    "list": lambda value: isinstance(value, list),
    "dict": lambda value: isinstance(value, dict),
}

Stub = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    required_args: Mapping[str, str]
    observation_schema: Mapping[str, str]
    stub: Stub | None = None


def type_matches(type_name: str, value: Any) -> bool:
    check = TYPE_CHECKS.get(type_name)
    if check is None:
        raise TraceError(E_TRACE_SCHEMA_UNKNOWN_TYPE, f"未知类型名 {type_name!r}", "schema")
    return check(value)


class ToolRegistry:
    """工具名 -> 规格。不注册就无法回放（工具不存在直接判 FAIL）。"""

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec:
        spec = self._tools.get(name)
        if spec is None:
            raise TraceError(
                E_TRACE_UNKNOWN_TOOL,
                f"工具 {name!r} 未注册；回放器拒绝发起真实调用",
                f"tool:{name}",
            )
        return spec

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._tools))


def validate_args(spec: ToolSpec, args: dict[str, Any], location: str) -> None:
    for key, type_name in sorted(spec.required_args.items()):
        if key not in args:
            raise TraceError(E_TRACE_TOOL_ARGS, f"工具 {spec.name} 缺少入参 {key!r}", location)
        if not type_matches(type_name, args[key]):
            raise TraceError(
                E_TRACE_TOOL_ARGS,
                f"工具 {spec.name} 的入参 {key!r} 应为 {type_name}",
                location,
            )


def validate_observation(spec: ToolSpec, observation: dict[str, Any], location: str) -> None:
    for key, type_name in sorted(spec.observation_schema.items()):
        if key not in observation:
            raise TraceError(
                E_TRACE_OBS_SCHEMA, f"工具 {spec.name} 的观测缺少 {key!r}", location
            )
        if not type_matches(type_name, observation[key]):
            raise TraceError(
                E_TRACE_OBS_SCHEMA,
                f"工具 {spec.name} 的观测字段 {key!r} 应为 {type_name}",
                location,
            )


_BIN_OPS: dict[type[ast.operator], Callable[[float, float], float]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}


def _eval_node(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)
    if isinstance(node, ast.Constant):
        value = node.value
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("只允许数字常量")
        return float(value)
    if isinstance(node, ast.UnaryOp):
        operand = _eval_node(node.operand)
        if isinstance(node.op, ast.USub):
            return -operand
        if isinstance(node.op, ast.UAdd):
            return +operand
        raise ValueError("不支持的一元运算符")
    if isinstance(node, ast.BinOp):
        handler = _BIN_OPS.get(type(node.op))
        if handler is None:
            raise ValueError("不支持的二元运算符")
        return handler(_eval_node(node.left), _eval_node(node.right))
    raise ValueError(f"不支持的语法节点 {type(node).__name__}")


def _stub_calc(args: dict[str, Any]) -> dict[str, Any]:
    expression = args["expression"]
    if not isinstance(expression, str):
        raise ValueError("expression 必须是字符串")
    tree = ast.parse(expression, mode="eval")
    return {"status": "ok", "value": _eval_node(tree)}


#: 离线小语料。stub 只做本地查表，永不联网。
_CORPUS: dict[str, str] = {
    "population 2040": "2040",
    "casmi 2026 deadline": "2026-12-07",
    "protocol": "http/1.1",
}


def _stub_search(args: dict[str, Any]) -> dict[str, Any]:
    query = args["query"]
    if not isinstance(query, str):
        raise ValueError("query 必须是字符串")
    return {"status": "ok", "result": _CORPUS.get(query, "")}


_TABLE: dict[str, list[dict[str, Any]]] = {
    "orders": [
        {"id": 1, "amount": 120},
        {"id": 2, "amount": 80},
    ],
}


def _stub_lookup_table(args: dict[str, Any]) -> dict[str, Any]:
    key = args["key"]
    if not isinstance(key, str):
        raise ValueError("key 必须是字符串")
    return {"status": "ok", "rows": list(_TABLE.get(key, []))}


def _stub_echo(args: dict[str, Any]) -> dict[str, Any]:
    text = args["text"]
    if not isinstance(text, str):
        raise ValueError("text 必须是字符串")
    return {"status": "ok", "echo": text}


def default_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="search",
            required_args={"query": "str"},
            observation_schema={"status": "str", "result": "str"},
            stub=_stub_search,
        )
    )
    registry.register(
        ToolSpec(
            name="calc",
            required_args={"expression": "str"},
            observation_schema={"status": "str", "value": "number"},
            stub=_stub_calc,
        )
    )
    registry.register(
        ToolSpec(
            name="lookup_table",
            required_args={"key": "str"},
            observation_schema={"status": "str", "rows": "list"},
            stub=_stub_lookup_table,
        )
    )
    registry.register(
        ToolSpec(
            name="echo",
            required_args={"text": "str"},
            observation_schema={"status": "str", "echo": "str"},
            stub=_stub_echo,
        )
    )
    return registry


def run_stub(spec: ToolSpec, args: dict[str, Any], location: str) -> dict[str, Any]:
    stub = spec.stub
    if stub is None:
        raise TraceError(E_TOOL_STUB_ERROR, f"工具 {spec.name} 没有注册 stub", location)
    try:
        result = stub(args)
    except Exception as exc:  # stub 是本地纯函数，任何异常都算回放失败
        raise TraceError(E_TOOL_STUB_ERROR, f"stub 执行异常：{exc!r}", location) from exc
    if not isinstance(result, dict):
        raise TraceError(E_TOOL_STUB_ERROR, "stub 必须返回 object", location)
    return result


__all__ = [
    "ToolSpec",
    "ToolRegistry",
    "default_registry",
    "run_stub",
    "type_matches",
    "validate_args",
    "validate_observation",
]
