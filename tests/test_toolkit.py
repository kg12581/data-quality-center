"""工具能力测试：历史归档、摘要 JSON、warn 门禁、样本行数、SQL 带出、新用例生成、CI 文件。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import common

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "main.py"
NEW_CASE = ROOT / "scripts" / "new_case.py"

CSV = "id,age,email,status\n1,20,a@example.com,active\n2,-5,bad-email,unknown\n3,30,c@example.com,pending\n"


def make_case(tmp_path: Path, checks_yaml: str, csv_body: str = CSV) -> Path:
    (tmp_path / "data").mkdir(exist_ok=True)
    (tmp_path / "data" / "users.csv").write_text(csv_body, encoding="utf-8")
    config = tmp_path / "config" / "rules.yaml"
    config.parent.mkdir(exist_ok=True)
    config.write_text(
        "version: 1\nsources:\n  users:\n    type: csv\n    path: data/users.csv\n"
        "checks:\n" + checks_yaml.lstrip("\n"),
        encoding="utf-8",
    )
    return config


NOT_NULL = """
  - name: id 非空
    source: users
    type: not_null
    column: id
    severity: high
"""

WARN_ONLY = """
  - name: 行数下限
    source: users
    type: row_count
    min: 100
    severity: warn
"""


def run_cli(args, cwd=ROOT) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(MAIN), *args], capture_output=True, text=True, cwd=str(cwd))


# ------------------------------------------------------------ 报告增强 ----
def test_archive_writes_history_report(tmp_path):
    config = make_case(tmp_path, NOT_NULL)
    report = common.run_case(config, output_dir=tmp_path / "reports", show_console=False, archive=True)
    assert report["summary"]["total"] == 1
    history = sorted((tmp_path / "reports" / "history").glob("rules_*.json"))
    assert len(history) == 1                       # <用例名>_<时间戳>.json
    payload = json.loads(history[0].read_text(encoding="utf-8"))
    assert payload["summary"]["total"] == 1


def test_summary_json_is_single_line(tmp_path):
    config = make_case(tmp_path, NOT_NULL)
    proc = run_cli(["-c", str(config), "--report", "none", "--summary-json"],
                   cwd=tmp_path)
    lines = [line for line in proc.stdout.splitlines() if line.strip().startswith("{")]
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["status"] == "PASS" and payload["exit_code"] == 0
    assert payload["total"] == 1
    assert payload["name"]                      # 没有 name 时回填规则文件路径
    assert payload["reports"] == []             # --report none 不写文件


def test_summary_json_lists_report_files(tmp_path):
    config = make_case(tmp_path, NOT_NULL)
    proc = run_cli(["-c", str(config), "--report", "md", "-o", str(tmp_path / "out"),
                    "--summary-json"], cwd=tmp_path)
    payload = json.loads([line for line in proc.stdout.splitlines()
                          if line.strip().startswith("{")][0])
    assert len(payload["reports"]) == 1
    assert payload["reports"][0].endswith("rules.md")


def test_fail_on_warn_changes_exit_code(tmp_path):
    config = make_case(tmp_path, WARN_ONLY)
    assert run_cli(["-c", str(config), "--report", "none"], cwd=tmp_path).returncode == 0
    strict = run_cli(["-c", str(config), "--report", "none", "--fail-on-warn"], cwd=tmp_path)
    assert strict.returncode == 1


def test_include_sql_puts_sql_into_report(tmp_path):
    config = make_case(tmp_path, NOT_NULL)
    common.run_case(config, output_dir=tmp_path / "out", show_console=False, include_sql=True)
    payload = json.loads((tmp_path / "out" / "rules.json").read_text(encoding="utf-8"))
    assert "IS NULL" in payload["results"][0]["sql"]
    assert payload["results"][0]["source_type"] == "csv"      # 数据源审计信息

    common.run_case(config, output_dir=tmp_path / "out2", show_console=False)
    plain = json.loads((tmp_path / "out2" / "rules.json").read_text(encoding="utf-8"))
    assert plain["results"][0]["sql"] == ""                   # 默认不带 SQL


# ------------------------------------------------------ 样本行数可配置 ----
def test_sample_limit_controls_failed_rows(tmp_path):
    body = "id,email,status\n" + "\n".join([",a@example.com,active"] * 25) + "\n"
    config = make_case(tmp_path, NOT_NULL.replace("severity: high", "severity: high\n    sample_limit: 5"),
                       body)
    report = common.run_case(config, output_dir=tmp_path / "out", show_console=False)
    result = report["results"][0]
    assert result["actual"] == 25
    assert len(result["failed_rows"]) == 5                    # 只留 5 行样本


# ---------------------------------------------------------- 新用例生成 ----
def test_new_case_script_generates_runnable_case(tmp_path):
    out_dir = tmp_path / "cases"
    proc = subprocess.run(
        [sys.executable, str(NEW_CASE), "--name", "订单数据质量", "--type", "odps",
         "--conn", "odps_dw", "--table", "ods_order_di", "--out-dir", str(out_dir)],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    yaml_files = list(out_dir.glob("*.yaml"))
    py_files = list(out_dir.glob("*.py"))
    assert len(yaml_files) == 1 and len(py_files) == 1
    assert yaml_files[0].stem == py_files[0].stem            # 同名，只差后缀

    from common import checks
    from common import config as config_module

    config = config_module.load_config(yaml_files[0])
    assert config.name == "订单数据质量"
    assert config.sources["main_table"]["conn"] == "odps_dw"
    assert len(config.checks) == 3
    sql, mode, expected, _ = checks.build_sql(
        config.checks[0], "main_table", config.sources["main_table"],
        yaml_files[0].parent, config.vars_for(config.checks[0]))
    assert "ods_order_di" in sql and expected == 0


def test_new_case_script_refuses_overwrite(tmp_path):
    out_dir = tmp_path / "cases"
    args = [sys.executable, str(NEW_CASE), "--name", "重复用例", "--uuid", "fixed-uuid",
            "--out-dir", str(out_dir)]
    assert subprocess.run(args, capture_output=True, text=True, cwd=str(ROOT)).returncode == 0
    again = subprocess.run(args, capture_output=True, text=True, cwd=str(ROOT))
    assert again.returncode == 2 and "已存在" in again.stderr
    assert subprocess.run(args + ["--force"], capture_output=True, text=True,
                          cwd=str(ROOT)).returncode == 0


def test_new_case_script_generates_unique_names_by_default(tmp_path):
    """不指定 --uuid 时每次生成新的 uuid，不会互相覆盖。"""
    out_dir = tmp_path / "cases"
    for _ in range(2):
        subprocess.run([sys.executable, str(NEW_CASE), "--name", "同名用例",
                        "--out-dir", str(out_dir)], capture_output=True, text=True, cwd=str(ROOT))
    assert len(list(out_dir.glob("*.yaml"))) == 2


# ------------------------------------------------------------- 工程化 ----
@pytest.mark.parametrize("path", [
    ".github/workflows/ci.yml",
    ".github/workflows/nightly.yml",
    "Makefile",
    "CHANGELOG.md",
    "docs/01-规则编写手册.md",
    "docs/02-连接配置手册.md",
    "docs/03-运维与调度.md",
    "docs/04-常见问题.md",
])
def test_project_files_exist(path):
    assert (ROOT / path).is_file()


def test_cli_help_lists_new_flags():
    out = run_cli(["--help"]).stdout
    for flag in ("--include-sql", "--fail-on-warn", "--archive", "--summary-json", "--env", "--only"):
        assert flag in out
