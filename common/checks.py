"""执行一条规则。

规则有两种写法，但最终都翻译成 SQL 执行：

1. ``sql`` 规则：直接写 SQL（指标模式看 expected + operator，明细模式返回行即失败）
2. 快捷方式：not_null / unique / enum / range / row_count 自动生成 SQL

所以只有一条执行路径，csv / parquet 也会被注册成内存表用 SQL 查。
"""

from __future__ import annotations

import math
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from . import db

MAX_SAMPLE = 10  # 失败样本默认留 10 行，规则里可用 sample_limit 调整（上限 1000）
_PLACEHOLDER = re.compile(r"\$\{(\w+)\}|\{\{\s*(\w+)\s*\}\}")


class CheckError(Exception):
    """规则执行不了（SQL 报错、变量缺失、列名有问题等）。"""


@dataclass
class CheckResult:
    """一条规则的执行结果（也是报告里的一条记录）。"""

    name: str
    source: str
    type: str
    dim: str
    severity: str
    status: str  # PASS / FAIL / ERROR
    actual: Any = None
    expected: Any = None
    operator: str = ""
    message: str = ""
    failed_rows: list = field(default_factory=list)
    duration_ms: float = 0.0
    source_type: str = ""        # 数据源类型（csv/hive/odps…），报告里便于排查
    conn_name: str = ""          # 用的连接名（.env 里的名字），不含账号密码
    sql: str = ""                # 渲染后的 SQL（--include-sql 时才带上）

    @property
    def result(self) -> str:
        """和常见规则集对齐的断言结果：passed / failed / error。"""
        return {"PASS": "passed", "FAIL": "failed", "ERROR": "error"}[self.status]

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "source": self.source,
            "type": self.type,
            "dim": self.dim,
            "severity": self.severity,
            "status": self.status,
            "result": self.result,
            "actual": self.actual,
            "expected": self.expected,
            "operator": self.operator,
            "message": self.message,
            "failed_rows": self.failed_rows,
            "duration_ms": self.duration_ms,
            "source_type": self.source_type,
            "conn_name": self.conn_name,
            "sql": self.sql,
        }


# --------------------------------------------------------------------------- #
# 小工具：模板变量、比较、取值
# --------------------------------------------------------------------------- #
def render(text: Any, variables: Mapping[str, Any]) -> str:
    """把 ${name} / {{ name }} 替换成变量值。"""

    def replace(match) -> str:
        name = match.group(1) or match.group(2)
        if name not in variables:
            raise CheckError(
                f"变量 {name} 没定义（可用：{sorted(variables)}；"
                "可在 vars 里定义，或用 --var name=value / 环境变量 DQ_VAR_<NAME>）"
            )
        return str(variables[name])

    return _PLACEHOLDER.sub(replace, str(text))


def scalar(text: Any) -> Any:
    """把渲染后的字符串还原成数字 / 布尔，例如 "100" → 100。"""
    if not isinstance(text, str):
        return text
    value = text.strip()
    if re.fullmatch(r"[+-]?\d+", value):
        return int(value)
    if re.fullmatch(r"[+-]?(\d+\.\d*|\.\d+|\d+)([eE][+-]?\d+)?", value):
        return float(value)
    if value.lower() in ("true", "false"):
        return value.lower() == "true"
    return text


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _equal(actual: Any, expected: Any) -> bool:
    if actual is None or expected is None:
        return actual is None and expected is None
    if _is_number(actual) and _is_number(expected):
        return math.isclose(float(actual), float(expected), rel_tol=1e-9, abs_tol=1e-9)
    if isinstance(actual, bool) or isinstance(expected, bool):
        return bool(actual) == bool(expected)
    return actual == expected


def _ordered(actual: Any, expected: Any):
    """转成可比较的两个值：数字优先，其次字符串。"""
    if _is_number(actual) and _is_number(expected):
        return float(actual), float(expected)
    try:
        return float(str(actual).strip()), float(str(expected).strip())
    except (TypeError, ValueError):
        pass
    if isinstance(actual, str) and isinstance(expected, str):
        return actual, expected
    return None, None


def compare(actual: Any, expected: Any, operator: str = "eq") -> tuple:
    """按 operator 断言，返回 (是否通过, 说明)。"""
    value = actual.item() if hasattr(actual, "item") else actual
    op = str(operator or "eq").lower()
    if op == "eq":
        passed = _equal(value, expected)
    elif op == "ne":
        passed = not _equal(value, expected)
    elif op in ("gt", "gte", "lt", "lte"):
        left, right = _ordered(value, expected)
        passed = False if left is None else {
            "gt": left > right, "gte": left >= right, "lt": left < right, "lte": left <= right
        }[op]
    elif op == "between":
        if not isinstance(expected, (list, tuple)) or len(expected) != 2:
            raise CheckError("operator: between 需要 expected: [下限, 上限]")
        low, high = sorted(_ordered(expected[0], expected[1]))
        left, _ = _ordered(value, expected[0])
        passed = left is not None and low <= left <= high
    elif op in ("in", "not_in"):
        if not isinstance(expected, (list, tuple, set)):
            raise CheckError(f"operator: {op} 需要 expected 是列表")
        hit = any(_equal(value, item) for item in expected)
        passed = hit if op == "in" else not hit
    else:
        raise CheckError(f"不支持的 operator: {operator!r}")
    return passed, f"{op} {expected!r}"


def jsonable(value: Any) -> Any:
    """把 numpy / NaN 之类转成能写进 JSON 的值。"""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return None if (math.isnan(value) or math.isinf(value)) else value
    if hasattr(value, "item"):
        try:
            return jsonable(value.item())
        except Exception:
            pass
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def records(frame: Any, limit: int = MAX_SAMPLE) -> list:
    """取失败样本（最多 limit 行）。"""
    if frame is None or len(frame) == 0:
        return []
    return [{str(key): jsonable(value) for key, value in row.items()}
            for row in frame.head(limit).to_dict(orient="records")]


def sample_limit(check: Mapping[str, Any]) -> int:
    """失败样本行数：规则里的 sample_limit，默认 10，最大 1000。"""
    value = check.get("sample_limit", MAX_SAMPLE)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise CheckError(f"sample_limit 必须是正整数，实际是 {value!r}")
    return min(value, 1000)


# --------------------------------------------------------------------------- #
# 把规则翻译成 SQL
# --------------------------------------------------------------------------- #
def _sql_text(check: Mapping[str, Any], base_dir: Any, variables: Mapping[str, Any]) -> str:
    if check.get("sql_file"):
        path = db.resolve_path(check["sql_file"], base_dir)
        if not path.is_file():
            raise CheckError(f"SQL 文件不存在：{path}")
        raw = path.read_text(encoding="utf-8")
    else:
        raw = str(check.get("sql") or check.get("query") or "")
    return render(raw, variables)


def _where(conditions, variables) -> str:
    parts = [render(item, variables) for item in conditions if item]
    return " WHERE " + " AND ".join(f"({part})" for part in parts) if parts else ""


def _columns(check: Mapping[str, Any]) -> list:
    columns = check.get("columns") or check.get("column")
    if isinstance(columns, str):
        columns = [columns]
    if not columns:
        raise CheckError("规则需要 column 或 columns")
    return [str(item) for item in columns]


def check_columns(check: Mapping[str, Any], frame: pd.DataFrame) -> None:
    """快捷方式规则在本地文件上跑之前，先确认列名写对了。

    SQLite 会把未知的引号标识符当成字符串，不提前拦下来就会"假失败/假通过"。
    """
    if frame is None:
        return
    wanted = _columns(check) if check["type"] in ("not_null", "unique") else [str(check.get("column") or "")]
    missing = [name for name in wanted if name and name not in frame.columns]
    if missing:
        raise CheckError(
            f"数据里没有列 {missing}，现有列：{list(frame.columns)}"
            "（检查一下 column / columns 是不是写错了）"
        )


def _literal(value: Any) -> str:
    """把 Python 值写成 SQL 字面量。"""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if _is_number(value):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def _expected_with_vars(check: Mapping[str, Any], variables: Mapping[str, Any]) -> Any:
    """expected 里也允许写变量（阈值参数化），渲染后还原成数字。"""
    expected = check.get("expected", check.get("expect"))
    if isinstance(expected, Mapping):
        return {key: scalar(render(item, variables)) if isinstance(item, str) and _PLACEHOLDER.search(item) else item
                for key, item in expected.items()}
    if isinstance(expected, str) and _PLACEHOLDER.search(expected):
        return scalar(render(expected, variables))
    return expected


def build_sql(check: Mapping[str, Any], source_name: str, source: Mapping[str, Any],
              base_dir: Any, variables: Mapping[str, Any]) -> tuple:
    """返回 (sql, mode, expected, operator)。"""
    kind = check["type"]
    table = db.quote(db.table_name(source_name, source))
    conditions = [check.get("filter")] if check.get("filter") else []

    if kind == "sql":
        sql = _sql_text(check, base_dir, variables)
        if check.get("filter"):
            sql = f"SELECT * FROM (\n{sql}\n) AS _t{_where(conditions, variables)}"
        expected = _expected_with_vars(check, variables)
        mode = str(check.get("mode") or ("metric" if expected is not None else "rows")).lower()
        return sql, mode, expected, str(check.get("operator") or "eq").lower()

    if kind == "not_null":
        parts = []
        for column in _columns(check):
            name = db.quote(column)
            item = f"{name} IS NULL"
            if check.get("treat_empty_as_null"):
                item += f" OR TRIM(CAST({name} AS VARCHAR)) = ''"
            parts.append(item)
        sql = f"SELECT * FROM {table}{_where(conditions + ['(' + ' OR '.join(parts) + ')'], variables)}"
        return sql, "rows", 0, "eq"

    if kind == "unique":
        names = ", ".join(db.quote(column) for column in _columns(check))
        sql = (f"SELECT {names}, COUNT(*) AS dup_cnt FROM {table}"
               f"{_where(conditions, variables)} GROUP BY {names} HAVING COUNT(*) > 1")
        return sql, "rows", 0, "eq"

    if kind == "enum":
        column = db.quote(str(check["column"]))
        allowed = ", ".join(_literal(item) for item in check["allowed"])
        sql = f"SELECT * FROM {table}{_where(conditions + [f'{column} NOT IN ({allowed})'], variables)}"
        return sql, "rows", 0, "eq"

    if kind == "range":
        column = db.quote(str(check["column"]))
        bounds = []
        if check.get("min") is not None:
            bounds.append(f"{column} < {_literal(check['min'])}")
        if check.get("max") is not None:
            bounds.append(f"{column} > {_literal(check['max'])}")
        sql = f"SELECT * FROM {table}{_where(conditions + ['(' + ' OR '.join(bounds) + ')'], variables)}"
        return sql, "rows", 0, "eq"

    if kind == "row_count":
        sql = f"SELECT COUNT(*) AS row_count FROM {table}{_where(conditions, variables)}"
        low, high = check.get("min"), check.get("max")
        if low is not None and high is not None:
            return sql, "metric", [low, high], "between"
        return sql, "metric", low if low is not None else high, "gte" if low is not None else "lte"

    raise CheckError(f"不支持的规则类型：{kind}")


# --------------------------------------------------------------------------- #
# 执行
# --------------------------------------------------------------------------- #
def run_check(check: Mapping[str, Any], sources, overrides: Mapping[str, Any] | None = None,
              include_sql: bool = False) -> CheckResult:
    """执行一条规则；任何异常都变成 status=ERROR，不影响其它规则。

    ``include_sql=True`` 时把渲染后的 SQL 一起写进结果（排查用，报告会变大）。
    """
    started = time.perf_counter()
    base = dict(
        name=str(check.get("name", "")),
        source=str(check.get("source", "")),
        type=str(check.get("type", "")),
        dim=str(check.get("dim") or ""),
        severity=str(check.get("severity") or "error"),
        status="ERROR",
    )
    try:
        source_name = base["source"]
        source = sources.config.sources[source_name]
        base["source_type"] = db._kind(source)
        base["conn_name"] = str(source.get("conn") or "")
        variables = sources.config.vars_for(check, overrides)
        sql, mode, expected, operator = build_sql(check, source_name, source, sources.base_dir, variables)
        if include_sql:
            base["sql"] = sql
        connection, origin = sources.connection(source_name, check)
        if origin == "frame" and check["type"] != "sql":
            check_columns(check, sources.frame(source_name))
        frame = db.query(connection, sql)
        total = len(sources.frame(source_name)) if origin == "frame" else None
        limit = sample_limit(check)
        outcome = (_judge_rows(check, frame, expected, operator, total, limit) if mode == "rows"
                   else _judge_metric(frame, expected, operator, limit))
    except CheckError as exc:
        base["message"] = str(exc)
    except Exception as exc:  # 兜底：单条规则出错不影响整体
        base["message"] = f"{type(exc).__name__}: {exc}"
    else:
        base.update(outcome)
    base["duration_ms"] = round((time.perf_counter() - started) * 1000, 3)
    return CheckResult(**base)


def _apply_threshold(check: Mapping[str, Any], status: str, failed: int, total, message: str) -> tuple:
    """按 threshold（允许的失败率）修正结论。"""
    limit = check.get("threshold")
    if limit is None or status != "FAIL" or not total:
        return status, message
    rate = failed / total
    note = f"失败率 {rate:.2%}（{failed}/{total}），阈值 {float(limit):.2%}"
    if rate <= float(limit):
        return "PASS", f"{message}；在允许范围内：{note}"
    return status, f"{message}；超出阈值：{note}"


def _judge_rows(check: Mapping[str, Any], frame: pd.DataFrame, expected: Any,
                operator: str, total, limit: int = MAX_SAMPLE) -> dict:
    count = int(len(frame))
    target = 0 if expected is None else expected
    passed, comparison = compare(count, target, operator)
    detail = "没有失败明细" if passed else f"返回 {count} 行失败明细"
    status, message = _apply_threshold(
        check, "PASS" if passed else "FAIL", count, total,
        f"明细模式：{detail}，期望 {comparison}",
    )
    if status == "FAIL" and check.get("message"):
        message = f"{message}｜{check['message']}"
    return {"status": status, "actual": count, "expected": target, "operator": operator,
            "message": message, "failed_rows": records(frame, limit) if status == "FAIL" else []}


def _judge_metric(frame: pd.DataFrame, expected: Any, operator: str,
                  limit: int = MAX_SAMPLE) -> dict:
    if frame is None or len(frame) == 0:
        raise CheckError("指标模式要求 SQL 返回一行，但查询没有返回任何行")
    if isinstance(expected, Mapping):
        row = frame.iloc[0].to_dict()
        actual, problems = {}, []
        for column, want in expected.items():
            if column not in row:
                problems.append(f"结果里没有列 {column}")
                continue
            actual[str(column)] = jsonable(row[column])
            item_operator = operator
            if isinstance(want, Mapping):          # 单列也可以指定 operator
                item_operator = str(want.get("operator") or operator).lower()
                want = want.get("value")
            ok, comparison = compare(row[column], want, item_operator)
            if not ok:
                problems.append(f"{column}={jsonable(row[column])!r} 不满足 {comparison}")
        if problems:
            return {"status": "FAIL", "actual": actual, "expected": dict(expected), "operator": operator,
                    "message": "指标不符合预期：" + "；".join(problems),
                    "failed_rows": records(frame, limit)}
        return {"status": "PASS", "actual": actual, "expected": dict(expected), "operator": operator,
                "message": f"指标符合预期：{actual}", "failed_rows": []}

    if len(frame) > 1:
        raise CheckError(
            f"指标模式要求单行结果，实际返回 {len(frame)} 行；查明细请写 mode: rows，或把 SQL 改成聚合"
        )
    value = frame.iloc[0, 0]
    passed, comparison = compare(value, expected, operator)
    return {
        "status": "PASS" if passed else "FAIL",
        "actual": jsonable(value),
        "expected": expected,
        "operator": operator,
        "message": (f"指标 {frame.columns[0]}={jsonable(value)!r}，期望 {comparison} "
                    f"{'通过' if passed else '不通过'}"),
        "failed_rows": [] if passed else records(frame, limit),
    }
