#!/usr/bin/env python3
"""生成一个新用例：testcase/<uuid>.yaml + 同名 .py。

示例：
    python scripts/new_case.py --name 订单数据质量
    python scripts/new_case.py --name 保险理赔质量 --type odps --conn odps_dw --table ods_claim_di
    python scripts/new_case.py --name 本地联调 --type csv --out-dir /tmp/dq_new

生成后：改 YAML 里的 sources / checks，然后
    python main.py --case 订单数据质量
或在 PyCharm 里直接 Run 生成的 py 文件。
"""

from __future__ import annotations

import argparse
import sys
import uuid as uuid_lib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import common

YAML_TEMPLATE = """\
# {name}：规则集说明写这里
# 连接信息写在 .env（参考 .env.example），规则集里只写连接名 conn
name: {name}
description: 一句话说明这个用例检查什么；建议在 .env 里配好 {conn_hint}
version: 1

vars:
  partition: "1=1"        # 生产上建议传具体分区：--var partition="dt = '2026-09-16'"
  bizdate: "2026-09-16"   # 业务日期（及时性/对照类规则用）

sources:
  main_table:
    type: {source_type}
{source_fields}

checks:
  # ① SQL 断言：expected + operator
  - name: 主键非空
    source: main_table
    dim: completeness
    severity: high
    description: 主键列不允许为空
    sql: |
      SELECT COUNT(*) FROM ${{table}}
      WHERE ${{partition}} AND id IS NULL
    expected: 0

  # ② SQL 明细：返回的行就是失败明细（自带最多 10 行样本）
  - name: 主键重复明细
    source: main_table
    dim: uniqueness
    severity: high
    sql: |
      SELECT id, COUNT(*) AS dup_cnt FROM ${{table}}
      WHERE ${{partition}} AND id IS NOT NULL
      GROUP BY id HAVING COUNT(*) > 1
    mode: rows
    threshold: 0.001         # 允许 0.1% 的重复

  # ③ 快捷方式：不写 SQL，自动生成
  - name: 分区行数下限
    source: main_table
    dim: completeness
    severity: medium
    type: row_count
    min: 1
"""

PY_TEMPLATE = '''"""{name}（用例名见同名 yaml 里的 name 字段）：
规则见同目录的「{uuid}.yaml」。

运行方式：
  1) PyCharm 里直接 Run 这个文件（连接读仓库根目录的 .env）
  2) 服务器定时任务：python {uuid}.py --env /opt/conf/prod.env --report json,md
  3) 主入口调用：python main.py --case {name}
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # 直接 Run 时也能 import common

import common

# ============================ 本用例的配置 ============================
YAML = Path(__file__).with_suffix(".yaml")     # 规则集文件
ENV_FILE = None                                # 连接配置：None = 仓库根目录 .env；服务器可写 /opt/conf/prod.env
REPORT_FORMATS = ("json", "md", "html")        # 要生成哪几份报告
OUTPUT_DIR = None                              # None = 仓库根目录 reports/
EXTRA_VARS = {{}}                              # 额外变量，例如 {{"partition": "dt = '2026-09-16'"}}
ONLY = []                                      # 只跑某几条规则，例如 ["主键"]
SHOW_FAILED_ROWS = False                       # 控制台是否打印失败样本行


def run(variables=None, formats=REPORT_FORMATS, output_dir=OUTPUT_DIR, show_console=True,
        failed_rows=SHOW_FAILED_ROWS, strict_fields=False, only=None, env_file=ENV_FILE,
        include_sql=False, fail_on_warn=False, archive=False):
    """执行本规则集：读 YAML → 跑规则 → 出报告，返回 report（含 summary / results）。"""
    config = common.load_rules(YAML, env_file=env_file, strict_fields=strict_fields)
    report = common.run(config, variables={{**EXTRA_VARS, **(variables or {{}})}}, only=only or ONLY,
                        include_sql=include_sql, fail_on_warn=fail_on_warn)
    written = common.write_reports(report, output_dir or common.REPORT_DIR, formats,
                                   prefix=YAML.stem, archive=archive)
    if show_console:
        print(common.render_console(report, failed_rows=failed_rows))
    for path in written:
        print(f"报告已写入：{{path}}")
    return report


def main(argv=None):
    return common.case_entry(YAML, argv, run_func=run)


if __name__ == "__main__":
    raise SystemExit(main())
'''


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="new_case.py",
        description="生成一个新用例（testcase/<uuid>.yaml + 同名 .py）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例：python scripts/new_case.py --name 订单数据质量 --type odps --conn odps_dw --table ods_order_di",
    )
    parser.add_argument("--name", required=True, help="用例名（写在 yaml 的 name 里）")
    parser.add_argument("--uuid", default=None, help="指定文件名（默认自动生成 uuid）")
    parser.add_argument("--type", default="csv", help="数据源类型：csv / odps / hive / doris / mysql …")
    parser.add_argument("--conn", default="", help=".env 里的连接名（csv/parquet 不用填）")
    parser.add_argument("--table", default="your_table", help="表名（csv 时是文件路径）")
    parser.add_argument("--out-dir", default=None, help="输出目录（默认 testcase/）")
    parser.add_argument("--force", action="store_true", help="已存在时覆盖")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    out_dir = Path(args.out_dir) if args.out_dir else common.CASE_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    case_id = args.uuid or str(uuid_lib.uuid4())
    yaml_path = out_dir / f"{case_id}.yaml"
    py_path = out_dir / f"{case_id}.py"
    if not args.force and (yaml_path.exists() or py_path.exists()):
        print(f"[错误] {yaml_path.name} 已存在（加 --force 覆盖）", file=sys.stderr)
        return 2

    source_type = args.type.lower()
    if source_type in ("csv", "parquet"):
        source_fields = f"    path: {args.table}"
        conn_hint = "数据文件路径写在 sources.main_table.path"
    else:
        conn_name = args.conn or ("local_db" if source_type == "sqlite" else "your_conn")
        source_fields = f"    conn: {conn_name}\n    table: {args.table}"
        conn_hint = f"连接 {conn_name}（.env 里的 DQ_CONN_{conn_name.upper()}_*）"
    yaml_path.write_text(
        YAML_TEMPLATE.format(
            name=args.name, source_type=source_type, table=args.table,
            source_fields=source_fields, conn_hint=conn_hint,
        ),
        encoding="utf-8",
    )
    py_path.write_text(PY_TEMPLATE.format(name=args.name, uuid=case_id), encoding="utf-8")

    print(f"已生成：\n  {yaml_path}\n  {py_path}")
    print(f"\n下一步：\n  1) 改 {yaml_path.name} 的 sources / checks（字段名写错会在跑之前报错）"
          f"\n  2) 跑起来：python main.py --case {args.name}\n"
          f"     或在 PyCharm 里 Run {py_path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
