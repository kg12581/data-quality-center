"""保险_承保理赔数据质量（用例名见同名 yaml 里的 name 字段）：
规则见同目录的「6e49032b-ea6e-4621-8dbc-bf4bfc3a832a.yaml」，数据源是阿里云 MaxCompute（ODPS）。

运行方式：
  1) PyCharm 里直接 Run 这个文件（连接读仓库根目录的 .env）
  2) 跑批（T+1）：
       python 6e49032b-ea6e-4621-8dbc-bf4bfc3a832a.py \
         --env /opt/conf/ins.env --var partition="dt = '2026-09-16'" --var bizdate=2026-09-16
  3) 主入口调用：python main.py --case 保险_承保理赔数据质量
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # 直接 Run 时也能 import common

import common

# ============================ 本用例的配置 ============================
YAML = Path(__file__).with_suffix(".yaml")     # 规则集文件
ENV_FILE = None                                # 数据库连接配置：None = 仓库根目录 .env（也可写 /opt/conf/ins.env）
REPORT_FORMATS = ("json", "md", "html")        # 要生成哪几份报告
OUTPUT_DIR = None                              # None = 仓库根目录 reports/
EXTRA_VARS = {}                                # 额外变量，例如 {"partition": "dt = '2026-09-16'"}
ONLY = []                                      # 只跑某几条规则，例如 ["保单号唯一", "保费"]
SHOW_FAILED_ROWS = False                       # 控制台是否打印失败样本行


def run(variables=None, formats=REPORT_FORMATS, output_dir=OUTPUT_DIR, show_console=True,
        failed_rows=SHOW_FAILED_ROWS, strict_fields=False, only=None, env_file=ENV_FILE):
    """执行本规则集：读 YAML → 跑规则 → 出报告，返回 report（含 summary / results）。"""
    config = common.load_rules(YAML, env_file=env_file, strict_fields=strict_fields)
    report = common.run(config, variables={**EXTRA_VARS, **(variables or {})}, only=only or ONLY)
    written = common.write_reports(report, output_dir or common.REPORT_DIR, formats,
                                   prefix=YAML.stem)          # 报告名 = yaml 文件名
    if show_console:
        print(common.render_console(report, failed_rows=failed_rows))
    for path in written:
        print(f"报告已写入：{path}")
    return report


def main(argv=None):
    """命令行入口：python <uuid>.py [--env .env] [--var partition="dt='...'"] [--report md]"""
    return common.case_entry(YAML, argv, run_func=run)


if __name__ == "__main__":
    raise SystemExit(main())
