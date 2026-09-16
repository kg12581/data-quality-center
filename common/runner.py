"""跑规则：按需连数据源，逐条执行检查，最后汇总。"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from fnmatch import fnmatch
from typing import Any, Mapping

from . import checks, db
from .config import Config


class Sources:
    """数据源管家：csv/parquet 读成 DataFrame，数据库/大数据直接用连接（都做缓存）。"""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.base_dir = config.base_dir
        self._frames: dict = {}
        self._connections: dict = {}

    def frame(self, name: str):
        """把数据源读成 DataFrame（同一份只读一次）。"""
        if name not in self._frames:
            self._frames[name] = db.load_frame(name, self.config.sources[name], self.base_dir)
        return self._frames[name]

    def connection(self, name: str, check: Mapping[str, Any] | None = None) -> tuple:
        """返回 (可执行 SQL 的连接, 'source' 或 'frame')。"""
        source = self.config.sources[name]
        run_on = str((check or {}).get("run_on") or "auto").lower()
        if run_on == "auto":
            run_on = "source" if db.supports_conn(source) else "frame"
        if run_on == "source":
            if not db.supports_conn(source):
                raise checks.CheckError(
                    f"数据源 {name}（{source.get('type')}）不能用 run_on: source，"
                    "去掉 run_on 让工具自动选择即可"
                )
            if name not in self._connections:
                self._connections[name] = db.open_conn(source)
            return self._connections[name], "source"
        table = db.table_name(name, source)
        return db.frame_conn(self.frame(name), [table, name]), "frame"

    def close(self) -> None:
        db.reset()


def select_checks(checks: list, only: Any = None) -> list:
    """按 --only 过滤规则：支持名字精确匹配、子串匹配、含 * ? 的通配符。"""
    patterns = only if isinstance(only, (list, tuple, set)) else [only]
    patterns = [str(item).strip() for item in patterns if str(item or "").strip()]
    if not patterns:
        return list(checks)
    picked = []
    for check in checks:
        name = str(check.get("name", ""))
        for pattern in patterns:
            if pattern == name or pattern.lower() in name.lower() or fnmatch(name, pattern):
                picked.append(check)
                break
    return picked


def run(config: Config, variables: Mapping[str, Any] | None = None, on_result=None,
        only: Any = None) -> dict:
    """执行规则，返回报告字典（summary + results）。``only`` 可以只跑指定的几条规则。"""
    started = time.perf_counter()
    sources = Sources(config)
    results = []
    try:
        for check in select_checks(config.enabled_checks(), only):
            result = checks.run_check(check, sources, variables)
            results.append(result)
            if callable(on_result):
                on_result(result)
    finally:
        sources.close()
    return {
        "version": config.version,
        "name": config.name,
        "description": config.description,
        "config": str(config.path or ""),
        "time": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "duration_ms": round((time.perf_counter() - started) * 1000, 3),
        "summary": _summarize(results),
        "results": [item.to_dict() for item in results],
    }


def _summarize(results) -> dict:
    """统计通过 / 失败 / 错误、按维度汇总，并给出退出码。"""
    by_dim: dict = {}
    blocking: list = []
    for item in results:
        bucket = by_dim.setdefault(item.dim or "未标注", {"total": 0, "passed": 0, "failed": 0, "errors": 0})
        bucket["total"] += 1
        bucket[{"PASS": "passed", "FAIL": "failed", "ERROR": "errors"}[item.status]] += 1
        # severity=error 的失败（或没法执行）会让退出码变成 1
        if item.severity == "error" and item.status in ("FAIL", "ERROR"):
            blocking.append(item.name)
    return {
        "total": len(results),
        "passed": sum(1 for item in results if item.status == "PASS"),
        "failed": sum(1 for item in results if item.status == "FAIL"),
        "errors": sum(1 for item in results if item.status == "ERROR"),
        "status": "FAIL" if blocking else "PASS",
        "exit_code": 1 if blocking else 0,
        "blocking": blocking,
        "by_dim": by_dim,
    }
