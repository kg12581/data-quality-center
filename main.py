#!/usr/bin/env python3
"""主入口：指定要跑哪个规则集。

最常用：改下面的 RULE_SET，然后直接 Run 这个文件（PyCharm 里也一样）。
也可以直接 Run ``testcase/<规则集名字>.py``，效果相同。

命令行：
    python main.py                        # 跑 RULE_SET
    python main.py --case 用户数据质量_脏数据
    python main.py --all                  # 跑 testcase/ 下所有规则集
    python main.py --list-cases           # 看看有哪些规则集
"""

from __future__ import annotations

import argparse
import sys

import common

# ↓↓↓ 改这里就能换规则集：写 uuid（testcase/ 下的文件名）或 yaml 里的 name 都行
RULE_SET = "bec36bd4-5b66-474b-a163-1fa6c5d23b2f"   # 用户数据质量


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="数据质量校验：一个规则集 = testcase/<名字>.yaml + 同名 py 文件",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="退出码：0 全部通过；1 有 error 级规则失败；2 规则或参数写错",
    )
    parser.add_argument("--case", "-c", default=None,
                        help=f"要跑的规则集：uuid（或 yaml 里的 name）；默认 {RULE_SET}")
    parser.add_argument("--all", action="store_true", help="跑 testcase/ 下所有规则集")
    parser.add_argument("--list-cases", action="store_true", help="列出 testcase/ 下的规则集")
    parser.add_argument("--list-rules", action="store_true",
                        help="列出指定规则集里的规则名（配合 --only 用）")
    parser.add_argument("--report", "-r", default="console,json,md,html",
                        help="输出方式，逗号分隔：console / json / md / html / all / none")
    parser.add_argument("--output-dir", "-o", default=None, help="报告目录（默认 reports/）")
    parser.add_argument("--var", action="append", default=[], metavar="NAME=VALUE",
                        help="覆盖规则里的变量（SQL 里的 ${name}），可重复")
    parser.add_argument("--env", default=None,
                        help="数据库连接配置文件（默认仓库根目录的 .env，格式见 .env.example）")
    parser.add_argument("--only", action="append", default=[], metavar="NAME",
                        help="只跑规则集里的某几条规则（名字子串 / 通配符），可重复或用逗号分隔")
    parser.add_argument("--failed-rows", action="store_true", help="控制台也打印失败样本行")
    parser.add_argument("--strict-fields", action="store_true",
                        help="规则里出现 actual / result 等运行结果字段时报错")
    parser.add_argument("--include-sql", action="store_true",
                        help="报告里带上渲染后的 SQL（排查用，报告会变大）")
    parser.add_argument("--fail-on-warn", action="store_true",
                        help="warn 级失败也让退出码变成 1")
    parser.add_argument("--archive", action="store_true",
                        help="往 reports/history/ 留一份带时间戳的历史报告")
    parser.add_argument("--summary-json", action="store_true",
                        help="stdout 打印一行 JSON 摘要（给调度器 / 监控解析）")
    parser.add_argument("--list-checks", action="store_true", help="列出支持的规则类型")
    parser.add_argument("--list-connectors", action="store_true", help="列出支持的数据源 / 数据库")
    parser.add_argument("--version", action="version", version=f"data-quality-center {common.__version__}")
    return parser.parse_args(argv)


def show_cases() -> None:
    rows = [[item.stem, common.case_name(item), "有" if item.with_suffix(".py").is_file() else "无"]
            for item in common.list_cases()]
    print(common.report.table(["规则集（uuid）", "名字（yaml 里的 name）", "同名的 py"], rows))


def show_checks() -> None:
    rows = [
        ["sql", "写 SQL：expected + operator 做断言，或 mode: rows 返回失败明细"],
        ["not_null", "空值检查（生成 col IS NULL 的 SQL）"],
        ["unique", "唯一性检查（生成 GROUP BY ... HAVING COUNT(*) > 1）"],
        ["enum", "枚举检查：取值必须在 allowed 列表里"],
        ["range", "数值区间检查：min / max"],
        ["row_count", "行数检查：min / max"],
    ]
    print(common.report.table(["类型", "说明"], rows))
    print("\n提示：SQL 里可以用 ${table}、${source}、${partition} 和规则集里定义的变量")


def show_rules(name: str) -> int:
    try:
        config = common.load_config(common.case_yaml(name))
    except common.ConfigError as exc:
        print(f"[配置错误] {exc}", file=sys.stderr)
        return 2
    print(f"规则集：{config.name or common.case_name(name)}（{common.case_yaml(name).name}）")
    rows = [[item["name"], item.get("source", ""), item.get("dim", ""), item.get("type", ""),
             item.get("severity", ""), "停用" if item.get("enabled", True) is False else "启用"]
            for item in config.checks]
    print(common.report.table(["规则名", "数据源", "维度", "类型", "级别", "状态"], rows))
    return 0


def show_connectors() -> None:
    rows = [[kind, item.get("backend", ""), item.get("driver") or "自定义（driver_class）",
             item.get("url") or "自定义（url / url_template）", item.get("port") or "-",
             item.get("jars") or "-"]
            for kind, item in common.db.PROFILES.items()]
    print(common.report.table(["类型", "默认后端", "JDBC driver_class", "url 模板", "默认端口", "需要的 jar"], rows))
    print(
        "\n提示：\n"
        "  - 关系库可用纯 Python 驱动：mysql / doris → mysql+pymysql，postgresql → psycopg2，oracle → oracledb\n"
        "  - 大数据走 JDBC：pip install jaydebeapi JPype1，并准备 JDK 与对应 jar（driver_path / driver_path_env）\n"
        "  - Hudi / Paimon 没有独立 JDBC，默认经 Trino 查询（query_engine: hive / spark 可切换）"
    )


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.list_cases:
        show_cases()
        return 0
    if args.list_checks:
        show_checks()
        return 0
    if args.list_connectors:
        show_connectors()
        return 0

    try:
        formats = common.parse_reports(args.report)
        variables = common.parse_vars(args.var)
        only = common.parse_only(args.only)
    except ValueError as exc:
        print(f"[参数错误] {exc}", file=sys.stderr)
        return 2
    names = ([item.stem for item in common.list_cases()] if args.all
             else [args.case or RULE_SET])
    if not names:
        print("[参数错误] testcase/ 里没有规则集", file=sys.stderr)
        return 2

    if args.list_rules:
        return max(show_rules(name) for name in names)

    for name in names:                              # --only 写错时早点报错
        try:
            config = common.load_config(common.case_yaml(name))
        except common.ConfigError as exc:
            print(f"[配置错误] {name}: {exc}", file=sys.stderr)
            return 2
        if only and not common.select_checks(config.enabled_checks(), only):
            all_names = ", ".join(item["name"] for item in config.checks)
            print(f"[参数错误] --only 没匹配到任何规则（{name} 现有规则：{all_names}）", file=sys.stderr)
            return 2

    exit_code = 0
    for name in names:
        print(f"=== 规则集：{name} ===", file=sys.stderr)
        kwargs = dict(variables=variables, formats=formats, output_dir=args.output_dir,
                      show_console="console" in formats, failed_rows=args.failed_rows,
                      strict_fields=args.strict_fields, include_sql=args.include_sql,
                      fail_on_warn=args.fail_on_warn, archive=args.archive)
        if only:
            kwargs["only"] = only
        if args.env:
            kwargs["env_file"] = args.env
        try:
            report = common.run_case_by_name(name, **kwargs)
            code = report["summary"]["exit_code"]
            if args.summary_json:
                print(common.summary_json(report))
        except common.ConfigError as exc:
            print(f"[配置错误] {name}: {exc}", file=sys.stderr)
            code = 2
        exit_code = max(exit_code, code)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
