"""用户数据质量_脏数据（用例名见同名 yaml 里的 name 字段）：
规则见同目录的「4c307f9c-2e9e-4d9d-9130-dd71b6c2da57.yaml」。

用来演示失败与退出码 1；本地在 PyCharm 里直接 Run，服务器上：
    python 4c307f9c-2e9e-4d9d-9130-dd71b6c2da57.py --env /opt/conf/prod.env
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import common

# ============================ 本用例的配置 ============================
YAML = Path(__file__).with_suffix(".yaml")
ENV_FILE = None                                # None = 仓库根目录的 .env
REPORT_FORMATS = ("json", "md", "html")
OUTPUT_DIR = None
EXTRA_VARS = {}
ONLY = []
SHOW_FAILED_ROWS = True                        # 脏数据用例：控制台直接打印失败样本


def run(variables=None, formats=REPORT_FORMATS, output_dir=OUTPUT_DIR, show_console=True,
        failed_rows=SHOW_FAILED_ROWS, strict_fields=False, only=None, env_file=ENV_FILE,
        include_sql=False, fail_on_warn=False, archive=False):
    """执行本规则集：读 YAML → 跑规则 → 出报告，返回 report（含 summary / results）。"""
    config = common.load_rules(YAML, env_file=env_file, strict_fields=strict_fields)
    report = common.run(config, variables={**EXTRA_VARS, **(variables or {})}, only=only or ONLY,
                        include_sql=include_sql, fail_on_warn=fail_on_warn)
    written = common.write_reports(report, output_dir or common.REPORT_DIR, formats,
                                   prefix=YAML.stem, archive=archive)
    if show_console:
        print(common.render_console(report, failed_rows=failed_rows))
    for path in written:
        print(f"报告已写入：{path}")
    return report


def main(argv=None):
    return common.case_entry(YAML, argv, run_func=run)


if __name__ == "__main__":
    raise SystemExit(main())
