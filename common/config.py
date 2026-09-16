"""读取并校验 YAML 规则集。

规则集是这套工具的核心，所以校验放在配置阶段：字段写错、类型不支持、变量没定义、
把运行结果（actual / result）当成断言输入，都会在启动时直接报错并说清楚怎么改。
"""

from __future__ import annotations

import difflib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import yaml

from . import db


class ConfigError(Exception):
    """规则文件写错了。"""


# severity 支持别名，最终统一成 error / warn / info
SEVERITY = {
    "error": "error", "high": "error", "critical": "error",
    "warn": "warn", "warning": "warn", "medium": "warn",
    "info": "info", "low": "info",
}
# 这些字段是「运行结果」，由工具回填到报告，不能当成断言输入
RUNTIME_FIELDS = ("actual", "result", "passed", "status", "failed_rows", "duration_ms")
OPERATORS = ("eq", "ne", "gt", "gte", "lt", "lte", "between", "in", "not_in")
MODES = ("metric", "rows")

COMMON_FIELDS = {
    "name", "source", "type", "dim", "severity", "description", "message",
    "vars", "threshold", "filter", "enabled",
}
CHECK_FIELDS = {
    "sql": {"sql", "sql_file", "query", "expected", "expect", "operator", "mode",
            "run_on", "table", "sample_limit"},
    "not_null": {"column", "columns", "treat_empty_as_null"},
    "unique": {"column", "columns"},
    "enum": {"column", "allowed"},
    "range": {"column", "min", "max"},
    "row_count": {"min", "max"},
}
_SOURCE_COMMON = {"vars", "read_options"}
_CONN_FIELDS = {"conn"}          # 数据库连接信息写在 .env 里，YAML 只写连接名
SOURCE_FIELDS = {
    "csv": _SOURCE_COMMON | {"path"},
    "parquet": _SOURCE_COMMON | {"path"},
    "sqlite": _SOURCE_COMMON | {"path", "url", "url_env", "table", "query"},
    "sqlalchemy": _SOURCE_COMMON | {
        "url", "url_env", "dialect", "host", "port", "database", "username", "username_env",
        "password", "password_env", "table", "query", "connect_args", "engine_options",
        "allow_inline_password"},
}
for _kind in db.BIGDATA_TYPES:
    SOURCE_FIELDS[_kind] = SOURCE_FIELDS["sqlalchemy"] | {
        "backend", "url_template", "driver_class", "driver_path", "driver_path_env", "jars_env",
        "schema", "catalog", "project", "service", "query_engine", "extra_driver_args",
    } | _CONN_FIELDS
SOURCE_FIELDS["sqlite"] |= _CONN_FIELDS
SOURCE_FIELDS["sqlalchemy"] |= _CONN_FIELDS


def _suggest(word: str, options) -> str:
    close = difflib.get_close_matches(str(word), [str(item) for item in options], n=3, cutoff=0.6)
    return f"，是不是想写 {close}？" if close else ""


@dataclass
class Config:
    """校验后的规则集。"""

    version: int
    sources: dict = field(default_factory=dict)
    checks: list = field(default_factory=list)
    vars: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)
    path: Path | None = None
    base_dir: Path | None = None
    name: str = ""          # 用例的人类可读名字（yaml 里的 name）
    description: str = ""

    def vars_for(self, check: Mapping[str, Any], overrides: Mapping[str, Any] | None = None) -> dict:
        """一条规则最终生效的变量：内置 < 顶层 < 数据源 < 检查项 < 环境变量 < --var。"""
        source = self.sources.get(str(check.get("source", "")), {})
        merged = {
            "source": str(check.get("source", "")),
            "table": db.table_name(str(check.get("source", "")), source),
            "partition": "1=1",  # 内置默认：分区条件，用 vars / --var 覆盖
        }
        for group in (self.vars, source.get("vars"), check.get("vars"), _env_vars(), overrides):
            for key, value in dict(group or {}).items():
                merged[str(key)] = value
        return merged

    def enabled_checks(self) -> list:
        return [check for check in self.checks if check.get("enabled", True) is not False]


def _env_vars() -> dict:
    """收集 DQ_VAR_PARTITION=xxx 这类环境变量。"""
    result: dict = {}
    for key, value in os.environ.items():
        if key.startswith("DQ_VAR_") and str(value).strip():
            name = key[len("DQ_VAR_"):]
            result[name.lower()] = str(value)
            result.setdefault(name, str(value))
    return result


# --------------------------------------------------------------------------- #
# 校验：sources
# --------------------------------------------------------------------------- #
def _validate_vars(raw: Any, where: str) -> dict:
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise ConfigError(f"{where} 必须是 name: value 形式，实际是 {type(raw).__name__}")
    for key, value in raw.items():
        if not re.match(r"^[A-Za-z_]\w*$", str(key)):
            raise ConfigError(f"{where} 的变量名 {key!r} 非法（字母/数字/下划线，不能以数字开头）")
        if isinstance(value, (dict, list, tuple)):
            raise ConfigError(f"{where}.{key} 只支持标量值（字符串 / 数字 / 布尔）")
    return dict(raw)


def _validate_sources(raw: Any) -> dict:
    if not isinstance(raw, Mapping) or not raw:
        raise ConfigError("sources 必须是非空映射")
    sources: dict = {}
    for name, source in raw.items():
        where = f"sources.{name}"
        if not isinstance(source, Mapping):
            raise ConfigError(f"{where} 必须是映射")
        source = dict(source)
        kind = str(source.get("type", "")).lower()
        if kind not in SOURCE_FIELDS:
            raise ConfigError(
                f"{where}.type 不支持 {kind!r}，可用：csv / parquet / sqlite / sqlalchemy / "
                + " / ".join(db.BIGDATA_TYPES)
            )
        unknown = [key for key in source if key not in SOURCE_FIELDS[kind] | {"type"}]
        if unknown:
            raise ConfigError(
                f"{where} 出现未知字段 {unknown}{_suggest(unknown[0], SOURCE_FIELDS[kind])}"
            )
        source["vars"] = _validate_vars(source.get("vars"), f"{where}.vars")
        if kind in ("csv", "parquet"):
            if not source.get("path"):
                raise ConfigError(f"{where} 需要 path")
        else:
            if not (source.get("table") or source.get("query")):
                raise ConfigError(f"{where} 需要 table 或 query")
            if kind == "sqlite":
                if not (source.get("path") or source.get("url") or source.get("url_env")
                        or source.get("conn")):
                    raise ConfigError(f"{where} 需要 path，或 url / url_env")
            elif not source.get("conn") and not any(source.get(key) for key in
                                                    ("url", "url_env", "url_template", "dialect", "host")):
                raise ConfigError(
                    f"{where} 需要 conn（连接名，写在 .env 里）或 url / url_env / url_template，"
                    "或者 dialect + host + database"
                )
        sources[str(name)] = source
    return sources


# --------------------------------------------------------------------------- #
# 校验：checks
# --------------------------------------------------------------------------- #
def _validate_checks(raw: Any, sources: Mapping[str, Any], strict_fields: bool, base_dir,
                     global_vars: Mapping[str, Any]) -> tuple:
    if not isinstance(raw, list) or not raw:
        raise ConfigError("checks 必须是非空列表")
    checks: list = []
    warnings: list = []
    names: set = set()
    for index, check in enumerate(raw):
        where = f"checks[{index}]"
        if not isinstance(check, Mapping):
            raise ConfigError(f"{where} 必须是映射")
        check = dict(check)
        name = str(check.get("name") or "").strip()
        if not name:
            raise ConfigError(f"{where}.name 必填")
        if name in names:
            raise ConfigError(f"{where}.name 与其它规则重名：{name}")
        names.add(name)
        check["name"] = name
        where = f"checks[{index}] ({name})"

        _handle_runtime_fields(check, where, strict_fields, warnings)
        if not check.get("type") and (check.get("sql") or check.get("query") or check.get("sql_file")):
            check["type"] = "sql"          # 只写 sql: 就是 SQL 规则
        kind = str(check.get("type", "")).lower()
        if kind not in CHECK_FIELDS:
            raise ConfigError(
                f"{where}.type 必填，可用：{' / '.join(CHECK_FIELDS)}"
                "（只写 sql / sql_file 会自动推断为 sql 规则）"
            )
        check["type"] = kind
        allowed = COMMON_FIELDS | CHECK_FIELDS[kind]
        unknown = [key for key in check if key not in allowed]
        if unknown:
            raise ConfigError(f"{where} 出现未知字段 {unknown}{_suggest(unknown[0], allowed)}")

        source_name = str(check.get("source") or "")
        if source_name not in sources:
            raise ConfigError(
                f"{where}.source 指向未定义的数据源 {source_name!r}{_suggest(source_name, sources)}"
            )
        severity = str(check.get("severity") or "").lower()
        if severity not in SEVERITY:
            raise ConfigError(f"{where}.severity 必填，可用 error / warn / info（也支持 high / medium / low）")
        check["severity"] = SEVERITY[severity]
        check["vars"] = _validate_vars(check.get("vars"), f"{where}.vars")
        _validate_body(check, sources[source_name], where, base_dir, global_vars)
        checks.append(check)
    return checks, warnings


def _handle_runtime_fields(check: dict, where: str, strict: bool, warnings: list) -> None:
    """actual / result 是运行时回填值：默认忽略并提示；没有 expected 直接报错。"""
    used = [key for key in RUNTIME_FIELDS if key in check]
    if not used:
        return
    has_expected = check.get("expected") is not None or check.get("expect") is not None
    if strict:
        raise ConfigError(
            f"{where} 出现运行结果字段 {used}（--strict-fields 下不允许）。"
            "断言写 expected（可配 operator），actual / result 由工具回填到报告。"
        )
    if not has_expected:
        raise ConfigError(
            f"{where} 出现 {used} 但没有 expected。断言请写 expected: <期望值>；"
            "actual / result 是运行时回填字段，不能当期望值用。"
        )
    warnings.append(f"{where} 忽略了运行结果字段 {used}（断言只看 expected + operator）")
    for key in used:
        check.pop(key)


def _validate_body(check: Mapping[str, Any], source: Mapping[str, Any], where: str, base_dir,
                   global_vars: Mapping[str, Any]) -> None:
    kind = check["type"]
    if kind == "sql":
        if not (check.get("sql") or check.get("query") or check.get("sql_file")):
            raise ConfigError(f"{where} 需要 sql / sql_file / query 之一")
        if check.get("sql") and check.get("sql_file"):
            raise ConfigError(f"{where} 的 sql 与 sql_file 只能二选一")
        operator = str(check.get("operator") or "eq").lower()
        if operator not in OPERATORS:
            raise ConfigError(f"{where}.operator 不支持 {operator!r}，可用：{' / '.join(OPERATORS)}")
        mode = str(check.get("mode") or "").lower()
        if mode and mode not in MODES:
            raise ConfigError(f"{where}.mode 只能是 metric / rows")
        if mode == "metric" and check.get("expected", check.get("expect")) is None:
            raise ConfigError(f"{where} 的 mode: metric 需要 expected")
        run_on = str(check.get("run_on") or "auto").lower()
        if run_on not in ("auto", "frame", "source"):
            raise ConfigError(f"{where}.run_on 只能是 auto / frame / source")
    elif kind in ("not_null", "unique"):
        if not (check.get("column") or check.get("columns")):
            raise ConfigError(f"{where} 的 {kind} 需要 column 或 columns")
    elif kind == "enum":
        if not check.get("column") or not isinstance(check.get("allowed"), list) or not check["allowed"]:
            raise ConfigError(f"{where} 的 enum 需要 column 和 allowed: [值1, 值2]")
    elif kind == "range":
        if not check.get("column") or (check.get("min") is None and check.get("max") is None):
            raise ConfigError(f"{where} 的 range 需要 column，以及 min / max 至少一个")
    elif kind == "row_count":
        if check.get("min") is None and check.get("max") is None:
            raise ConfigError(f"{where} 的 row_count 需要 min / max 至少一个")

    threshold = check.get("threshold")
    if threshold is not None and (
        isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not 0 <= threshold <= 1
    ):
        raise ConfigError(f"{where}.threshold 必须是 0~1 的数字（允许的失败率）")
    _validate_variables(check, source, where, base_dir, global_vars)


def _validate_variables(check: Mapping[str, Any], source: Mapping[str, Any], where: str, base_dir,
                        global_vars: Mapping[str, Any]) -> None:
    """提前检查 ${变量} 是否都有定义，不要等跑起来才报错。"""
    text = str(check.get("sql") or check.get("query") or "")
    if check.get("sql_file"):
        path = db.resolve_path(check["sql_file"], base_dir)
        if not path.is_file():
            raise ConfigError(f"{where}.sql_file 文件不存在：{path}")
        text = path.read_text(encoding="utf-8")
    used = {name for pair in re.findall(r"\$\{(\w+)\}|\{\{\s*(\w+)\s*\}\}", text + str(check.get("filter") or ""))
            for name in pair if name}
    if not used:
        return
    available = ({"source", "table", "partition"} | set(global_vars) | set(source.get("vars") or {})
                 | set(check.get("vars") or {}))
    missing = sorted(name for name in used
                     if name not in available and f"DQ_VAR_{name.upper()}" not in os.environ)
    if missing:
        raise ConfigError(
            f"{where} 里用到的变量未定义：{missing}。可用的变量：{sorted(available)}；"
            "可在顶层 vars / 数据源 vars / 检查项 vars 里定义，或用 --var name=value"
            "（环境变量 DQ_VAR_<NAME> 也可以）"
        )


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #
TOP_FIELDS = {"version", "sources", "checks", "vars", "name", "description"}


def load_config(path: Any, strict_fields: bool = False) -> Config:
    """读取并校验 YAML 规则集。"""
    config_path = Path(str(path)).expanduser()
    if not config_path.is_file():
        raise ConfigError(f"配置文件不存在：{config_path.resolve()}")
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"YAML 解析失败：{exc}") from exc
    if not isinstance(raw, Mapping):
        raise ConfigError("配置根节点必须是映射（至少要有 version / sources / checks）")
    unknown = [key for key in raw if key not in TOP_FIELDS]
    if unknown:
        raise ConfigError(f"根节点出现未知字段 {unknown}{_suggest(unknown[0], TOP_FIELDS)}")
    if str(raw.get("version")) != "1":
        raise ConfigError("缺少 version: 1（当前只支持版本 1）")
    if "sources" not in raw or "checks" not in raw:
        raise ConfigError("缺少必填字段 sources / checks")

    base_dir = config_path.parent
    global_vars = _validate_vars(raw.get("vars"), "vars")
    sources = _validate_sources(raw["sources"])
    checks, warnings = _validate_checks(raw["checks"], sources, strict_fields, base_dir, global_vars)
    return Config(
        version=1,
        sources=sources,
        checks=checks,
        vars=global_vars,
        warnings=warnings,
        path=config_path,
        base_dir=base_dir,
        name=str(raw.get("name") or ""),
        description=str(raw.get("description") or ""),
    )
