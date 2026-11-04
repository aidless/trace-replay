"""CLI 端到端判据（子进程调用，验证真实退出码与输出）。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from trace_replay.engine import evaluate
from trace_replay.errors import ERROR_CODES
from trace_replay.model import canonical_json, load_trace
from trace_replay.report import report_sha256

REPO = Path(__file__).resolve().parents[1]
OK_TRACE = REPO / "examples" / "traces" / "ok_search_calc.jsonl"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "trace_replay", *args],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        check=False,
    )


def test_run_passes_and_writes_report(tmp_path: Path) -> None:
    result = _run("run", "--trace", str(OK_TRACE), "--out", str(tmp_path))
    assert result.returncode == 0, result.stderr
    assert "verdict=PASS" in result.stdout
    assert "checks=4/4" in result.stdout
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    digest = report_sha256(report)
    assert f"sha256={digest}" in result.stdout
    assert report["verdict"] == "PASS"


def test_run_is_byte_identical_across_directories(tmp_path: Path) -> None:
    left = tmp_path / "left"
    right = tmp_path / "right"
    assert _run("run", "--trace", str(OK_TRACE), "--out", str(left)).returncode == 0
    assert _run("run", "--trace", str(OK_TRACE), "--out", str(right)).returncode == 0
    assert (left / "report.json").read_bytes() == (right / "report.json").read_bytes()


def test_run_fail_exit_code_and_error_code(tmp_path: Path) -> None:
    result = _run("run", "--trace", str(REPO / "examples/traces/fail_loop.jsonl"), "--out", str(tmp_path))
    assert result.returncode == 1
    assert "verdict=FAIL" in result.stdout
    assert "E_TRACE_LOOP" in result.stderr


def test_run_truncated_trace_is_fail_not_usage_error(tmp_path: Path) -> None:
    result = _run("run", "--trace", str(REPO / "examples/traces/fail_incomplete.jsonl"), "--out", str(tmp_path))
    assert result.returncode == 1
    assert "E_TRACE_FINAL_MISSING" in result.stderr
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["verdict"] == "FAIL"
    assert report["trace"] is None


def test_run_missing_file_is_usage_error(tmp_path: Path) -> None:
    result = _run("run", "--trace", str(tmp_path / "nope.jsonl"), "--out", str(tmp_path))
    assert result.returncode == 2
    assert "E_TRACE_IO" in result.stderr


def test_verify_reports_identical_sha() -> None:
    result = _run("verify", "--trace", str(OK_TRACE), "--runs", "3")
    assert result.returncode == 0
    assert result.stdout.startswith("runs=3 sha256=")
    assert "identical=true" in result.stdout
    digest = result.stdout.split("sha256=")[1].split()[0]
    expected = report_sha256(evaluate(load_trace(OK_TRACE)))
    assert digest == expected


def test_verify_rejects_single_run() -> None:
    result = _run("verify", "--trace", str(OK_TRACE), "--runs", "1")
    assert result.returncode == 2


def test_list_errors_covers_all_codes() -> None:
    result = _run("list-errors")
    assert result.returncode == 0
    printed = {line.split("\t")[0] for line in result.stdout.strip().splitlines()}
    assert printed == set(ERROR_CODES)


def test_list_scorers_prints_five() -> None:
    result = _run("list-scorers")
    assert result.returncode == 0
    assert len(result.stdout.strip().splitlines()) == 5


def test_no_stub_mode_still_passes(tmp_path: Path) -> None:
    result = _run("run", "--trace", str(OK_TRACE), "--out", str(tmp_path), "--no-stub")
    assert result.returncode == 0
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["stub_mode"] is False
    assert report["replay"]["stub_checks"] == 0


def test_usage_error_without_arguments() -> None:
    result = _run()
    assert result.returncode == 2


def test_canonical_json_baseline_is_stable() -> None:
    assert canonical_json({"a": 1}) == '{"a":1}'
