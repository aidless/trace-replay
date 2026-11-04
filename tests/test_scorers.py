"""评分器逐条判据测试。"""

from __future__ import annotations

from typing import Any

import pytest

from trace_replay.errors import E_SCORER_CONFIG, E_SCORER_UNKNOWN, TraceError
from trace_replay.scorers import (
    ScoreContext,
    build_scorer,
    lookup_path,
    scorer_types,
)


def _ctx(answer: dict[str, Any] | None = None, tools: tuple[str, ...] = ("search", "calc")) -> ScoreContext:
    return ScoreContext(answer={"text": "2040", "value": 2040} if answer is None else answer, tool_sequence=tools)


def test_five_scorer_types_are_registered() -> None:
    assert scorer_types() == (
        "exact_match",
        "numeric_tolerance",
        "regex_rubric",
        "structural_fields",
        "tool_sequence",
    )


def test_lookup_path_nested_and_list() -> None:
    node = {"a": {"b": [{"c": 1}]}}
    assert lookup_path(node, "a.b.0.c") == 1
    assert lookup_path(node, "") is node
    assert lookup_path(node, "a.b.5") not in (0, 1, node)


def test_exact_match_pass() -> None:
    check = build_scorer("id", "exact_match", {"path": "text", "expected": "2040"}).check(_ctx())
    assert check.passed and check.reason == "ok"


def test_exact_match_mismatch() -> None:
    check = build_scorer("id", "exact_match", {"path": "text", "expected": "2041"}).check(_ctx())
    assert not check.passed and check.reason == "mismatch"


def test_exact_match_normalize() -> None:
    check = build_scorer(
        "id", "exact_match", {"path": "text", "expected": " 2040 ", "normalize": True}
    ).check(_ctx())
    assert check.passed


def test_exact_match_missing_path() -> None:
    check = build_scorer("id", "exact_match", {"path": "nope", "expected": "x"}).check(_ctx())
    assert not check.passed and check.reason == "missing_path"


def test_exact_match_non_string() -> None:
    check = build_scorer("id", "exact_match", {"path": "value", "expected": "2040"}).check(_ctx())
    assert not check.passed and check.reason == "observed_not_string"


def test_numeric_tolerance_pass() -> None:
    check = build_scorer("id", "numeric_tolerance", {"path": "value", "expected": 2040}).check(_ctx())
    assert check.passed


def test_numeric_tolerance_within_tolerance() -> None:
    check = build_scorer(
        "id", "numeric_tolerance", {"path": "value", "expected": 2040.5, "abs_tol": 0.5}
    ).check(_ctx())
    assert check.passed


def test_numeric_tolerance_out_of_range() -> None:
    check = build_scorer(
        "id", "numeric_tolerance", {"path": "value", "expected": 2040.5, "abs_tol": 0.4}
    ).check(_ctx())
    assert not check.passed and check.reason == "out_of_tolerance"


def test_numeric_tolerance_relative() -> None:
    check = build_scorer(
        "id", "numeric_tolerance", {"path": "value", "expected": 2000, "rel_tol": 0.05}
    ).check(_ctx())
    assert check.passed


def test_numeric_tolerance_rejects_bool() -> None:
    check = build_scorer("id", "numeric_tolerance", {"path": "flag", "expected": 1}).check(
        _ctx({"flag": True})
    )
    assert not check.passed and check.reason == "observed_not_numeric"


def test_numeric_tolerance_rejects_non_numeric_string() -> None:
    check = build_scorer("id", "numeric_tolerance", {"path": "text", "expected": 1}).check(_ctx())
    assert not check.passed and check.reason == "observed_not_numeric"


def test_structural_fields_pass() -> None:
    check = build_scorer(
        "id", "structural_fields", {"path": "", "fields": {"text": "str", "value": "int"}}
    ).check(_ctx())
    assert check.passed


def test_structural_fields_missing() -> None:
    check = build_scorer(
        "id", "structural_fields", {"path": "", "fields": {"text": "str", "value": "int", "extra": "str"}}
    ).check(_ctx())
    assert not check.passed and check.reason == "missing_fields"
    assert check.detail["missing"] == ["extra"]


def test_structural_fields_type_mismatch() -> None:
    check = build_scorer(
        "id", "structural_fields", {"path": "", "fields": {"value": "str"}}
    ).check(_ctx())
    assert not check.passed and check.reason == "type_mismatch"


def test_structural_fields_extra_disallowed() -> None:
    check = build_scorer(
        "id",
        "structural_fields",
        {"path": "", "fields": {"text": "str"}, "allow_extra": False},
    ).check(_ctx())
    assert not check.passed and check.reason == "unexpected_fields"


def test_structural_fields_not_object() -> None:
    check = build_scorer("id", "structural_fields", {"path": "text", "fields": {"x": "str"}}).check(
        _ctx({"text": "x"})
    )
    assert not check.passed and check.reason == "observed_not_object"


def test_regex_rubric_pass() -> None:
    check = build_scorer(
        "id", "regex_rubric", {"rules": [{"id": "r1", "path": "text", "pattern": r"^\d{4}$"}]}
    ).check(_ctx())
    assert check.passed


def test_regex_rubric_must_not_match() -> None:
    check = build_scorer(
        "id",
        "regex_rubric",
        {"rules": [{"id": "r1", "path": "text", "pattern": "sorry", "must_match": False}]},
    ).check(_ctx())
    assert check.passed


def test_regex_rubric_rule_failed() -> None:
    check = build_scorer(
        "id",
        "regex_rubric",
        {"rules": [{"id": "r1", "path": "text", "pattern": "^4[0-9]{3}$"}, {"id": "r2", "path": "text", "pattern": "2040"}]},
    ).check(_ctx())
    assert not check.passed and check.reason == "rule_failed"
    assert [rule["passed"] for rule in check.detail["rules"]] == [False, True]


def test_regex_rubric_invalid_pattern() -> None:
    with pytest.raises(TraceError) as caught:
        build_scorer("id", "regex_rubric", {"rules": [{"id": "r1", "path": "text", "pattern": "("}]})
    assert caught.value.code == E_SCORER_CONFIG


def test_tool_sequence_pass() -> None:
    check = build_scorer("id", "tool_sequence", {"expected": ["search", "calc"]}).check(_ctx())
    assert check.passed


def test_tool_sequence_mismatch() -> None:
    check = build_scorer("id", "tool_sequence", {"expected": ["calc", "search"]}).check(_ctx())
    assert not check.passed and check.reason == "sequence_mismatch"
    assert check.detail["observed"] == ["search", "calc"]


def test_unknown_scorer_type() -> None:
    with pytest.raises(TraceError) as caught:
        build_scorer("id", "llm_judge", {})
    assert caught.value.code == E_SCORER_UNKNOWN


def test_scorer_config_requires_expected() -> None:
    with pytest.raises(TraceError) as caught:
        build_scorer("id", "exact_match", {"path": "text"})
    assert caught.value.code == E_SCORER_CONFIG


def test_structural_fields_rejects_unknown_type_name() -> None:
    with pytest.raises(TraceError) as caught:
        build_scorer("id", "structural_fields", {"fields": {"x": "integer"}})
    assert caught.value.code == E_SCORER_CONFIG
