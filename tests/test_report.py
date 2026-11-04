"""报告确定性与序列化判据。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests._helpers import dump, records_copy
from trace_replay.engine import evaluate
from trace_replay.model import canonical_json, parse_trace
from trace_replay.report import pretty_json, report_sha256, write_run


def _report() -> dict[str, Any]:
    return evaluate(parse_trace(dump(records_copy()), sha256="sha-001"))


def test_two_evaluations_produce_identical_bytes() -> None:
    first = pretty_json(_report())
    second = pretty_json(_report())
    assert first == second
    assert report_sha256(_report()) == report_sha256(_report())


def test_report_has_no_volatile_fields() -> None:
    text = pretty_json(_report())
    for banned in ("generated_at", "elapsed_ms", "hostname", "python_version"):
        assert banned not in text


def test_canonical_json_is_independent_of_key_order() -> None:
    left = canonical_json({"b": 1, "a": {"d": 4, "c": 3}})
    right = canonical_json({"a": {"c": 3, "d": 4}, "b": 1})
    assert left == right


def test_canonical_json_rejects_nan() -> None:
    with pytest.raises(ValueError):
        canonical_json({"x": float("nan")})


def test_pretty_json_is_sorted_and_newline_terminated() -> None:
    text = pretty_json({"b": 1, "a": 2})
    assert text == '{\n  "a": 2,\n  "b": 1\n}\n'


def test_write_run_produces_identical_report_bytes(tmp_path: Path) -> None:
    report = _report()
    first_dir = tmp_path / "run1"
    second_dir = tmp_path / "run2"
    first_path, first_meta = write_run(report, first_dir, "sha-001", 1)
    second_path, second_meta = write_run(report, second_dir, "sha-001", 2)

    assert first_path.read_bytes() == second_path.read_bytes()
    assert json.loads(first_path.read_text(encoding="utf-8")) == report

    meta_payload = json.loads(first_meta.read_text(encoding="utf-8"))
    assert meta_payload["report_sha256"] == report_sha256(report)
    assert meta_payload["trace_sha256"] == "sha-001"
    assert meta_payload["generated_at_utc"].endswith("Z")
    assert second_meta.exists()


def test_report_sha256_changes_when_verdict_changes() -> None:
    records = records_copy()
    records[0]["scorers"][0]["config"]["expected"] = "2041"
    failed = evaluate(parse_trace(dump(records), sha256="sha-001"))
    assert failed["verdict"] == "FAIL"
    assert report_sha256(failed) != report_sha256(_report())


def test_same_trace_different_path_same_report(tmp_path: Path) -> None:
    first = tmp_path / "a.jsonl"
    second = tmp_path / "sub" / "b.jsonl"
    second.parent.mkdir()
    text = dump(records_copy())
    first.write_text(text, encoding="utf-8")
    second.write_text(text, encoding="utf-8")
    left = evaluate(parse_trace(first.read_text(encoding="utf-8"), sha256="sha-001"))
    right = evaluate(parse_trace(second.read_text(encoding="utf-8"), sha256="sha-001"))
    assert pretty_json(left) == pretty_json(right)
