"""命令行入口。

退出码约定：
- 0  PASS / verify 的多次重放完全一致
- 1  判决 FAIL（输入可读，但被 fail-closed 判失败）
- 2  用法或输入错误（文件读不到、参数非法等）
- 3  verify 发现多次重放的 sha256 不一致（确定性被打破）
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any, Callable, Sequence

from .engine import error_report, evaluate
from .errors import E_TRACE_IO, ERROR_CODES, TraceError
from .model import load_trace
from .report import report_sha256, write_run
from .scorers import scorer_types

EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_USAGE = 2
EXIT_NONDETERMINISTIC = 3


def _cmd_run(args: argparse.Namespace) -> int:
    started = time.perf_counter()
    stub_mode = not args.no_stub
    try:
        trace = load_trace(args.trace)
    except TraceError as exc:
        # 文件读不到 = 用法问题；其余（坏 JSON/缺字段/被截断）= 判 FAIL 并落报告
        if exc.code == E_TRACE_IO:
            print(f"input_error {exc.to_dict()}", file=sys.stderr)
            return EXIT_USAGE
        failed_report = error_report(exc.to_dict(), stub_mode=stub_mode)
        elapsed_ms = int(round((time.perf_counter() - started) * 1000))
        digest = report_sha256(failed_report)
        report_path, meta_path = write_run(failed_report, args.out, "", elapsed_ms)
        print(
            f"verdict=FAIL checks=0/0 sha256={digest} "
            f"report={report_path} meta={meta_path}",
            file=sys.stdout,
        )
        print(f"error={exc.code} message={exc.message}", file=sys.stderr)
        return EXIT_FAIL

    report: dict[str, Any] = evaluate(trace, stub_mode=stub_mode)
    elapsed_ms = int(round((time.perf_counter() - started) * 1000))
    digest = report_sha256(report)
    report_path, meta_path = write_run(report, args.out, trace.sha256, elapsed_ms)

    summary = report.get("summary")
    stats = summary if isinstance(summary, dict) else {}
    print(
        f"verdict={report['verdict']} "
        f"checks={stats.get('passed', 0)}/{stats.get('total', 0)} "
        f"sha256={digest} report={report_path} meta={meta_path}"
    )
    error = report.get("error")
    if isinstance(error, dict):
        print(f"error={error.get('code')} message={error.get('message')}", file=sys.stderr)
    return EXIT_PASS if report["verdict"] == "PASS" else EXIT_FAIL


def _cmd_verify(args: argparse.Namespace) -> int:
    trace = load_trace(args.trace)
    digests: list[str] = []
    for _ in range(args.runs):
        report: dict[str, Any] = evaluate(trace, stub_mode=not args.no_stub)
        digests.append(report_sha256(report))
    identical = len(set(digests)) == 1
    print(f"runs={args.runs} sha256={digests[0]} identical={'true' if identical else 'false'}")
    if not identical:
        for index, digest in enumerate(digests):
            print(f"run[{index}] sha256={digest}", file=sys.stderr)
        return EXIT_NONDETERMINISTIC
    return EXIT_PASS


def _cmd_list_errors(_: argparse.Namespace) -> int:
    for code in sorted(ERROR_CODES):
        print(f"{code}\t{ERROR_CODES[code]}")
    return EXIT_PASS


def _cmd_list_scorers(_: argparse.Namespace) -> int:
    for name in scorer_types():
        print(name)
    return EXIT_PASS


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="trace-replay",
        description="离线重放 agent 轨迹并给出确定性判决（零第三方依赖）",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="回放一条轨迹并写出 report.json / meta.json")
    run.add_argument("--trace", required=True, help="JSONL 轨迹文件")
    run.add_argument("--out", required=True, help="输出目录")
    run.add_argument("--no-stub", action="store_true", help="只回放录制观测，不跑本地 stub")
    run.set_defaults(handler=_cmd_run)

    verify = sub.add_parser("verify", help="重复回放并比对报告的 sha256")
    verify.add_argument("--trace", required=True, help="JSONL 轨迹文件")
    verify.add_argument("--runs", type=int, default=2, help="重放次数（默认 2）")
    verify.add_argument("--no-stub", action="store_true", help="只回放录制观测，不跑本地 stub")
    verify.set_defaults(handler=_cmd_verify)

    list_errors = sub.add_parser("list-errors", help="打印全部稳定错误码")
    list_errors.set_defaults(handler=_cmd_list_errors)

    list_scorers = sub.add_parser("list-scorers", help="打印内置评分器")
    list_scorers.set_defaults(handler=_cmd_list_scorers)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "verify" and args.runs < 2:
        parser.error("--runs 至少为 2")
    handler: Callable[[argparse.Namespace], int] = args.handler
    try:
        return handler(args)
    except TraceError as exc:
        payload = json.dumps(exc.to_dict(), ensure_ascii=False, sort_keys=True)
        print(f"input_error {payload}", file=sys.stderr)
        return EXIT_USAGE
    except FileNotFoundError as exc:
        print(f"input_error {exc}", file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
