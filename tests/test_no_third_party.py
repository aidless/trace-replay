"""零第三方依赖与零联网的静态/运行期判据。"""

from __future__ import annotations

import ast
import socket
from pathlib import Path
from typing import Any

import pytest

from tests._helpers import dump, records_copy
from trace_replay.engine import evaluate
from trace_replay.model import parse_trace

PACKAGE = Path(__file__).resolve().parents[1] / "trace_replay"

ALLOWED_IMPORTS = {
    "__future__",
    "argparse",
    "ast",
    "dataclasses",
    "datetime",
    "hashlib",
    "json",
    "math",
    "operator",
    "pathlib",
    "platform",
    "re",
    "sys",
    "time",
    "typing",
}

FORBIDDEN_NETWORK = {"socket", "ssl", "urllib", "http", "ftplib", "asyncio", "telnetlib"}


def _imported_modules() -> set[str]:
    found: set[str] = set()
    for path in sorted(PACKAGE.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    found.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.level == 0 and node.module:
                    found.add(node.module.split(".")[0])
    return found


def test_package_imports_only_stdlib() -> None:
    imported = _imported_modules()
    assert imported <= ALLOWED_IMPORTS, f"出现非标准库依赖：{sorted(imported - ALLOWED_IMPORTS)}"


def test_package_imports_no_network_modules() -> None:
    assert not (_imported_modules() & FORBIDDEN_NETWORK)


def test_runtime_works_with_socket_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*_: Any, **__: Any) -> Any:
        raise RuntimeError("沙箱内禁止网络")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    report = evaluate(parse_trace(dump(records_copy()), sha256="sha"))
    assert report["verdict"] == "PASS"


def test_no_requests_dependency_in_pyproject() -> None:
    pyproject = (PACKAGE.parent / "pyproject.toml").read_text(encoding="utf-8")
    lowered = pyproject.lower()
    for banned in ("requests", "pydantic", "httpx", "orjson", "urllib3"):
        assert banned not in lowered
