"""规则集的运行入口：一个 ``testcase/xxx.yaml`` 对应一个 ``testcase/xxx.py``。

- 规则集名字就是 YAML 文件名（不带后缀）
- 报告写到 ``reports/<规则集名字>.json / .md / .html``
- 可以在 PyCharm 里直接 Run ``testcase/xxx.py``，也可以用主文件 ``main.py`` 按名字调用
"""

from __future__ import annotations

import argparse
import importlib.util
import inspect
import json
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import yaml

from . import env
from .config import ConfigError, load_config
from .report import render_console, write_reports
from .runner import run

ROOT = Path(__file__).resolve().parent.parent   # 仓库根目录
CASE_DIR = ROOT / "testcase"                    # 规则集目录
REPORT_DIR = ROOT / "reports"                   # 报告目录
DEFAULT_FORMATS = ("json", "md", "html")


def list_cases() -> list:
    """列出 testcase/ 下的所有规则集（.yaml 文件，按名字排序）。"""
    if not CASE_DIR.is_dir():
        return []
    return sorted(CASE_DIR.glob("*.yaml")) + sorted(CASE_DIR.glob("*.yml"))


def case_name(case: Any) -> str:
    """规则集的人类可读名字（yaml 里的 name 字段），没写就用文件名。"""
    path = case if isinstance(case, Path) else case_yaml(case)
    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except Exception:
        raw = {}
    return str(raw.get("name") or Path(path).stem)


def find_cases(keyword: Any) -> list:
    """按 uuid / yaml 里的 name 找规则集，返回最匹配的一组（精确优先，再前缀，最后模糊）。"""
    text = str(keyword).strip().lower()
    if not text:
        return []
    cases = list_cases()
    for matcher in (
        lambda path: path.stem.lower() == text or case_name(path).lower() == text,   # 完全一样
        lambda path: path.stem.lower().startswith(text),                             # uuid 前缀
        lambda path: text in case_name(path).lower(),                                # 名字模糊
    ):
        hits = [path for path in cases if matcher(path)]
        if hits:
            return hits
    return []


def case_yaml(case: Any) -> Path:
    """把 uuid / 用例名 / yaml / py 路径解析成 YAML 文件路径。"""
    text = str(case)
    path = Path(text).expanduser()
    if path.suffix in (".yaml", ".yml"):
        return path if path.is_absolute() else path.resolve()
    if path.suffix == ".py":                     # 传进来的是同名 py 文件
        return path.parent / f"{path.stem}.yaml"
    if path.is_file():
        return path
    for suffix in (".yaml", ".yml"):             # 只给了名字 → 去 testcase/ 找
        candidate = CASE_DIR / f"{text}{suffix}"
        if candidate.is_file():
            return candidate
    hits = find_cases(text)                      # 再按 uuid 前缀 / yaml 里的 name 找
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        names = "；".join(f"{item.stem}（{case_name(item)}）" for item in hits)
        raise ConfigError(f"{text!r} 匹配到多个规则集：{names}；请写完整的 uuid 或 yaml 路径")
    raise ConfigError(f"没找到规则集 {text!r}；testcase/ 里现有：{_all_cases_text()}")


def _all_cases_text() -> str:
    cases = list_cases()
    return "；".join(f"{item.stem}（{case_name(item)}）" for item in cases) or "（还没有规则集）"


def case_python(case: Any) -> Path:
    """规则集对应的 python 文件（和 YAML 同名，后缀不同）。"""
    return case_yaml(case).with_suffix(".py")


def load_case_module(py_path: Path):
    """按路径加载 testcase/xxx.py（文件名可以是中文）。"""
    module_name = f"dq_case_{abs(hash(str(py_path)))}"
    spec = importlib.util.spec_from_file_location(module_name, py_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------- #
# 命令行参数解析（main.py 和 testcase/xxx.py 共用）
# --------------------------------------------------------------------------- #
def parse_reports(value: Any) -> list:
    """把 ``console,json,md,html`` 解析成列表。"""
    kinds = [item.strip().lower() for item in str(value).split(",") if item.strip()]
    allowed = ("console", "json", "md", "html")
    for kind in kinds:
        if kind not in allowed + ("all", "none"):
            raise ValueError(f"未知的 --report 取值 {kind!r}，可选：{', '.join(allowed)}, all, none")
    if "none" in kinds:
        return []
    if "all" in kinds:
        return list(allowed)
    return list(dict.fromkeys(kinds)) or ["console"]


def parse_vars(items) -> dict:
    """把 ``--var name=value`` 解析成字典。"""
    variables: dict = {}
    for item in items or []:
        if "=" not in str(item):
            raise ValueError(f"--var 需要 NAME=VALUE 形式：{item!r}")
        name, value = str(item).split("=", 1)
        variables[name.strip()] = value
    return variables


def parse_only(items) -> list:
    """把 ``--only a,b`` / 多次 ``--only`` 解析成列表。"""
    return [part.strip() for item in items or [] for part in str(item).split(",") if part.strip()]


def load_rules(case: Any, env_file: Any = None, conns: Mapping[str, Any] | None = None,
               strict_fields: bool = False):
    """准备一次运行：加载 .env 连接配置 → 读并校验 YAML → 检查引用的连接都在。

    三件事都在这里做，testcase 里的 py 直接调用它就行。
    """
    try:
        info = env.configure(env_file=env_file, conns=conns)
    except env.EnvError as exc:                    # --env 文件不存在等
        raise ConfigError(str(exc)) from exc
    config = load_config(case_yaml(case), strict_fields=strict_fields)
    for warning in config.warnings:
        print(f"[提示] {warning}", file=sys.stderr)
    try:
        env.check_sources(config.sources)          # 连接信息缺失就早点报错
    except env.EnvError as exc:
        raise ConfigError(str(exc)) from exc
    if info["env_file"] or info["conns"]:          # 没配数据库连接时不用刷屏
        print(f"[连接] {env.describe()}", file=sys.stderr)
    return config


def run_case(case: Any, variables: Mapping[str, Any] | None = None,
             formats: Iterable[str] = DEFAULT_FORMATS, output_dir: Any = None,
             show_console: bool = True, failed_rows: bool = False,
             strict_fields: bool = False, only: Any = None,
             env_file: Any = None, conns: Mapping[str, Any] | None = None,
             include_sql: bool = False, fail_on_warn: bool = False,
             archive: bool = False, archive_dir: Any = None) -> dict:
    """跑一个规则集：读同名 YAML → 执行规则 → 报告写到 ``reports/<规则集名>.*``。

    testcase 里的 py 一般会自己分步调用（load_rules / run / write_reports），
    这个函数是"一步到位"的版本，给 main.py、调度脚本或测试用。
    """
    yaml_path = case_yaml(case)
    name = yaml_path.stem
    config = load_rules(yaml_path, env_file=env_file, conns=conns, strict_fields=strict_fields)
    report = run(config, variables=variables, only=only,
                 include_sql=include_sql, fail_on_warn=fail_on_warn)
    written = write_reports(report, output_dir or REPORT_DIR, formats, prefix=name,
                            archive=archive, archive_dir=archive_dir)
    if show_console:
        print(render_console(report, failed_rows=failed_rows))
    for path in written:
        print(f"报告已写入：{path}")
    return report


def summary_json(report: dict, files: Iterable[Any] = ()) -> str:
    """一行 JSON 摘要（stdout 单行输出，给调度器 / 监控抓取）。"""
    summary = report["summary"]
    return json.dumps({
        "name": report.get("name") or report.get("config"),
        "status": summary["status"],
        "exit_code": summary["exit_code"],
        "total": summary["total"],
        "passed": summary["passed"],
        "failed": summary["failed"],
        "errors": summary["errors"],
        "duration_ms": report["duration_ms"],
        "time": report["time"],
        "blocking": summary["blocking"],
        "reports": report.get("reports") or [str(path) for path in files],
    }, ensure_ascii=False)


def case_entry(case_file: Any, argv=None, run_func=None) -> int:
    """``testcase/xxx.py`` 直接运行时的命令行入口（PyCharm Run / 服务器定时任务都用它）。

    ``run_func`` 传 testcase 里定义的 run()，这样命令行参数会交给它执行。
    """
    name = Path(str(case_file)).stem
    yaml_path = case_yaml(case_file)
    title = case_name(yaml_path)
    parser = argparse.ArgumentParser(
        prog=f"{name}.py",
        description=f"跑规则集「{title}」（规则见同目录 {yaml_path.name}）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            f"  python {name}.py\n"
            f"  python {name}.py --env /opt/conf/prod.env\n"
            f"  python {name}.py --var partition=\"dt = '2026-09-16'\"\n"
            f"  python {name}.py --report console,md --failed-rows\n"
        ),
    )
    parser.add_argument("--env", default=None, help="数据库连接配置文件（默认仓库根目录的 .env）")
    parser.add_argument("--report", "-r", default="console,json,md,html",
                        help="输出方式：console / json / md / html / all / none")
    parser.add_argument("--output-dir", "-o", default=None, help="报告目录（默认 reports/）")
    parser.add_argument("--var", action="append", default=[], metavar="NAME=VALUE",
                        help="覆盖规则里的变量，可重复")
    parser.add_argument("--only", action="append", default=[], metavar="NAME",
                        help="只跑某几条规则（名字子串 / 通配符），可重复或用逗号分隔")
    parser.add_argument("--failed-rows", action="store_true", help="控制台打印失败样本行")
    parser.add_argument("--strict-fields", action="store_true",
                        help="规则里出现 actual / result 等运行结果字段时报错")
    parser.add_argument("--include-sql", action="store_true",
                        help="报告里带上渲染后的 SQL（排查用，报告会变大）")
    parser.add_argument("--fail-on-warn", action="store_true",
                        help="warn 级失败也让退出码变成 1")
    parser.add_argument("--archive", action="store_true",
                        help="往 <报告目录>/history/ 留一份带时间戳的历史报告")
    parser.add_argument("--summary-json", action="store_true",
                        help="stdout 再打印一行 JSON 摘要（给调度器 / 监控解析）")
    args = parser.parse_args(argv)
    try:
        formats = parse_reports(args.report)
        variables = parse_vars(args.var)
    except ValueError as exc:
        print(f"[参数错误] {exc}", file=sys.stderr)
        return 2
    try:
        kwargs = dict(variables=variables, formats=formats, output_dir=args.output_dir,
                      show_console="console" in formats, failed_rows=args.failed_rows,
                      strict_fields=args.strict_fields, include_sql=args.include_sql,
                      fail_on_warn=args.fail_on_warn, archive=args.archive)
        if args.only:
            kwargs["only"] = parse_only(args.only)
        if args.env:
            kwargs["env_file"] = args.env
        runner = run_func or (lambda **kw: run_case(case_file, **kw))
        report = runner(**_supported_kwargs(runner, kwargs))
    except ConfigError as exc:
        print(f"[配置错误] {exc}", file=sys.stderr)
        return 2
    if args.summary_json:
        print(summary_json(report))
    return report["summary"]["exit_code"]


def run_case_by_name(case: Any, **kwargs) -> dict:
    """主文件用：优先调用 testcase/<名字>.py 里的 run()，没有就按 YAML 直接跑。返回 report。"""
    py_path = case_python(case)
    if py_path.is_file():
        module = load_case_module(py_path)
        return module.run(**_supported_kwargs(module.run, kwargs))
    return run_case(case, **kwargs)


def _supported_kwargs(func, kwargs: Mapping[str, Any]) -> dict:
    """只传函数签名里有的参数：老用例的 run() 没写新参数时也不会报 TypeError。"""
    try:
        params = inspect.signature(func).parameters
    except (TypeError, ValueError):          # pragma: no cover - 内置函数等
        return dict(kwargs)
    if any(item.kind is inspect.Parameter.VAR_KEYWORD for item in params.values()):
        return dict(kwargs)
    return {key: value for key, value in kwargs.items() if key in params}
