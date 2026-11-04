"""可插拔评分器。

所有评分器都是纯函数：只看回放结果，不读环境、不看时间、不联网。
每条评分器产出结构化 ``Check``（含稳定 reason 码与 detail），便于逐条复核。

已内置：
- ``exact_match``        字符串精确匹配（可 normalize）
- ``numeric_tolerance``  数值容差比较（abs/rel）
- ``structural_fields``  结构化字段存在性 + 类型比对
- ``regex_rubric``       正则/rubric 断言（must_match / must_not_match）
- ``tool_sequence``      工具调用序列比对
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol

from .errors import E_SCORER_CONFIG, TraceError
from .tools import TYPE_CHECKS, type_matches

_MISSING: object = object()


@dataclass(frozen=True)
class ScoreContext:
    answer: dict[str, Any]
    tool_sequence: tuple[str, ...]


@dataclass(frozen=True)
class Check:
    id: str
    scorer: str
    passed: bool
    reason: str
    detail: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "scorer": self.scorer,
            "passed": self.passed,
            "reason": self.reason,
            "detail": self.detail,
        }


class Scorer(Protocol):
    def check(self, ctx: ScoreContext) -> Check: ...


ScorerFactory = Callable[[str, dict[str, Any]], Scorer]


def lookup_path(root: Any, path: str) -> Any:
    """点路径取值；取不到返回哨兵 ``_MISSING``（不抛异常，也不给默认值）。"""
    if path == "":
        return root
    node: Any = root
    for segment in path.split("."):
        if isinstance(node, dict):
            if segment not in node:
                return _MISSING
            node = node[segment]
        elif isinstance(node, list):
            if not segment.isdigit():
                return _MISSING
            position = int(segment)
            if position >= len(node):
                return _MISSING
            node = node[position]
        else:
            return _MISSING
    return node


def _config_str(config: dict[str, Any], key: str, default: str | None = None) -> str:
    value = config.get(key, default)
    if not isinstance(value, str):
        raise TraceError(E_SCORER_CONFIG, f"配置项 {key!r} 必须是字符串", "scorer")
    return value


def _config_bool(config: dict[str, Any], key: str, default: bool) -> bool:
    value = config.get(key, default)
    if not isinstance(value, bool):
        raise TraceError(E_SCORER_CONFIG, f"配置项 {key!r} 必须是布尔值", "scorer")
    return value


def _config_number(config: dict[str, Any], key: str, default: float) -> float:
    value = config.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TraceError(E_SCORER_CONFIG, f"配置项 {key!r} 必须是数字", "scorer")
    return float(value)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _stable_number(value: float) -> float:
    return round(value, 12)


class ExactMatchScorer:
    def __init__(self, check_id: str, config: dict[str, Any]) -> None:
        self._id = check_id
        self._path = _config_str(config, "path")
        expected = config.get("expected", _MISSING)
        if expected is _MISSING or not isinstance(expected, str):
            raise TraceError(E_SCORER_CONFIG, "exact_match 需要字符串 expected", "scorer")
        self._expected = expected
        self._normalize = _config_bool(config, "normalize", False)

    def check(self, ctx: ScoreContext) -> Check:
        observed = lookup_path(ctx.answer, self._path)
        detail: dict[str, Any] = {"path": self._path, "expected": self._expected}
        if observed is _MISSING:
            detail["observed"] = None
            return Check(self._id, "exact_match", False, "missing_path", detail)
        if not isinstance(observed, str):
            detail["observed_type"] = type(observed).__name__
            return Check(self._id, "exact_match", False, "observed_not_string", detail)
        left = observed.strip() if self._normalize else observed
        right = self._expected.strip() if self._normalize else self._expected
        detail["observed"] = observed
        detail["normalized"] = self._normalize
        passed = left == right
        return Check(self._id, "exact_match", passed, "ok" if passed else "mismatch", detail)


class NumericToleranceScorer:
    def __init__(self, check_id: str, config: dict[str, Any]) -> None:
        self._id = check_id
        self._path = _config_str(config, "path")
        expected = config.get("expected", _MISSING)
        expected_number = _number(expected)
        if expected_number is None:
            raise TraceError(E_SCORER_CONFIG, "numeric_tolerance 需要数字 expected", "scorer")
        self._expected = expected_number
        self._abs_tol = abs(_config_number(config, "abs_tol", 0.0))
        self._rel_tol = abs(_config_number(config, "rel_tol", 0.0))

    def check(self, ctx: ScoreContext) -> Check:
        observed = lookup_path(ctx.answer, self._path)
        detail: dict[str, Any] = {
            "path": self._path,
            "expected": _stable_number(self._expected),
        }
        if observed is _MISSING:
            return Check(self._id, "numeric_tolerance", False, "missing_path", detail)
        observed_number = _number(observed)
        if observed_number is None:
            detail["observed_type"] = type(observed).__name__
            return Check(self._id, "numeric_tolerance", False, "observed_not_numeric", detail)
        if not math.isfinite(observed_number):
            return Check(self._id, "numeric_tolerance", False, "observed_not_finite", detail)
        diff = abs(observed_number - self._expected)
        tolerance = max(self._abs_tol, self._rel_tol * abs(self._expected))
        detail["observed"] = _stable_number(observed_number)
        detail["abs_diff"] = _stable_number(diff)
        detail["tolerance"] = _stable_number(tolerance)
        passed = diff <= tolerance
        return Check(
            self._id, "numeric_tolerance", passed, "ok" if passed else "out_of_tolerance", detail
        )


class StructuralFieldsScorer:
    def __init__(self, check_id: str, config: dict[str, Any]) -> None:
        self._id = check_id
        self._path = _config_str(config, "path", "")
        raw_fields = config.get("fields", _MISSING)
        if raw_fields is _MISSING or not isinstance(raw_fields, dict) or not raw_fields:
            raise TraceError(E_SCORER_CONFIG, "structural_fields 需要非空 fields 映射", "scorer")
        fields: dict[str, str] = {}
        for key, value in raw_fields.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise TraceError(E_SCORER_CONFIG, "fields 必须是 name->类型名 的字符串映射", "scorer")
            if value not in TYPE_CHECKS:
                raise TraceError(E_SCORER_CONFIG, f"未知类型名 {value!r}", "scorer")
            fields[key] = value
        self._fields = fields
        self._allow_extra = _config_bool(config, "allow_extra", True)

    def check(self, ctx: ScoreContext) -> Check:
        observed = lookup_path(ctx.answer, self._path)
        detail: dict[str, Any] = {"path": self._path, "fields": dict(sorted(self._fields.items()))}
        if observed is _MISSING or not isinstance(observed, dict):
            reason = "missing_path" if observed is _MISSING else "observed_not_object"
            return Check(self._id, "structural_fields", False, reason, detail)
        missing = [key for key in sorted(self._fields) if key not in observed]
        type_errors = [
            key
            for key in sorted(self._fields)
            if key in observed and not type_matches(self._fields[key], observed[key])
        ]
        extra = [str(key) for key in sorted(observed) if key not in self._fields]
        detail["missing"] = missing
        detail["type_errors"] = type_errors
        detail["extra"] = extra
        if missing:
            return Check(self._id, "structural_fields", False, "missing_fields", detail)
        if type_errors:
            return Check(self._id, "structural_fields", False, "type_mismatch", detail)
        if extra and not self._allow_extra:
            return Check(self._id, "structural_fields", False, "unexpected_fields", detail)
        return Check(self._id, "structural_fields", True, "ok", detail)


@dataclass(frozen=True)
class _RegexRule:
    id: str
    path: str
    pattern: re.Pattern[str]
    must_match: bool


class RegexRubricScorer:
    def __init__(self, check_id: str, config: dict[str, Any]) -> None:
        self._id = check_id
        raw_rules = config.get("rules", _MISSING)
        if raw_rules is _MISSING or not isinstance(raw_rules, list) or not raw_rules:
            raise TraceError(E_SCORER_CONFIG, "regex_rubric 需要非空 rules 数组", "scorer")
        rules: list[_RegexRule] = []
        seen: set[str] = set()
        for index, item in enumerate(raw_rules):
            if not isinstance(item, dict):
                raise TraceError(E_SCORER_CONFIG, f"rules[{index}] 必须是 object", "scorer")
            rule_id = item.get("id")
            rule_path = item.get("path")
            pattern = item.get("pattern")
            must_match = item.get("must_match", True)
            if not isinstance(rule_id, str) or not rule_id:
                raise TraceError(E_SCORER_CONFIG, f"rules[{index}].id 必须是非空字符串", "scorer")
            if rule_id in seen:
                raise TraceError(E_SCORER_CONFIG, f"规则 id 重复：{rule_id}", "scorer")
            seen.add(rule_id)
            if not isinstance(rule_path, str):
                raise TraceError(E_SCORER_CONFIG, f"rules[{index}].path 必须是字符串", "scorer")
            if not isinstance(pattern, str):
                raise TraceError(E_SCORER_CONFIG, f"rules[{index}].pattern 必须是字符串", "scorer")
            if not isinstance(must_match, bool):
                raise TraceError(E_SCORER_CONFIG, f"rules[{index}].must_match 必须是布尔值", "scorer")
            try:
                compiled = re.compile(pattern)
            except re.error as exc:
                raise TraceError(
                    E_SCORER_CONFIG, f"rules[{index}].pattern 无法编译：{exc}", "scorer"
                ) from exc
            rules.append(_RegexRule(rule_id, rule_path, compiled, must_match))
        self._rules = tuple(rules)

    def check(self, ctx: ScoreContext) -> Check:
        results: list[dict[str, Any]] = []
        all_passed = True
        for rule in self._rules:
            observed = lookup_path(ctx.answer, rule.path)
            if observed is _MISSING:
                passed, reason = False, "missing_path"
            elif not isinstance(observed, str):
                passed, reason = False, "observed_not_string"
            else:
                hit = rule.pattern.search(observed) is not None
                passed = hit if rule.must_match else not hit
                reason = "ok" if passed else ("not_matched" if rule.must_match else "matched_forbidden")
            all_passed = all_passed and passed
            results.append({"id": rule.id, "path": rule.path, "passed": passed, "reason": reason})
        detail: dict[str, Any] = {"rules": results}
        return Check(
            self._id, "regex_rubric", all_passed, "ok" if all_passed else "rule_failed", detail
        )


class ToolSequenceScorer:
    def __init__(self, check_id: str, config: dict[str, Any]) -> None:
        self._id = check_id
        expected = config.get("expected", _MISSING)
        if (
            expected is _MISSING
            or not isinstance(expected, list)
            or not expected
            or not all(isinstance(item, str) and item for item in expected)
        ):
            raise TraceError(E_SCORER_CONFIG, "tool_sequence 需要非空字符串数组 expected", "scorer")
        self._expected = tuple(expected)

    def check(self, ctx: ScoreContext) -> Check:
        actual = ctx.tool_sequence
        detail: dict[str, Any] = {
            "expected": list(self._expected),
            "observed": list(actual),
        }
        passed = actual == self._expected
        return Check(
            self._id, "tool_sequence", passed, "ok" if passed else "sequence_mismatch", detail
        )


SCORERS: dict[str, ScorerFactory] = {
    "exact_match": ExactMatchScorer,
    "numeric_tolerance": NumericToleranceScorer,
    "structural_fields": StructuralFieldsScorer,
    "regex_rubric": RegexRubricScorer,
    "tool_sequence": ToolSequenceScorer,
}


def build_scorer(check_id: str, scorer_type: str, config: Mapping[str, Any]) -> Scorer:
    from .errors import E_SCORER_UNKNOWN

    factory = SCORERS.get(scorer_type)
    if factory is None:
        raise TraceError(E_SCORER_UNKNOWN, f"未知评分器类型 {scorer_type!r}", "scorer")
    return factory(check_id, dict(config))


def scorer_types() -> tuple[str, ...]:
    return tuple(sorted(SCORERS))
