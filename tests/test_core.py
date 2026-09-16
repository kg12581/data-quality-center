"""common/ 的核心测试：断言、明细、变量、快捷方式、报告、命令行。

只覆盖公共代码的关键路径，改动 common/ 之后跑一下 `pytest` 就知道有没有弄坏规则集。
（不跑测试也不影响工具本身，删掉 tests/ 也能正常用。）
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import common
from common import checks, db

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "main.py"

CSV = "id,age,email,status\n1,20,a@example.com,active\n2,-5,bad-email,unknown\n3,30,c@example.com,pending\n"


@pytest.fixture(autouse=True)
def _clean():
    yield
    db.reset()


def make_case(tmp_path: Path, checks_yaml: str, csv_body: str = CSV) -> Path:
    """造一个最小规则集（yaml + 数据）。"""
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


def run_case_check(tmp_path: Path, checks_yaml: str, csv_body: str = CSV) -> dict:
    config = make_case(tmp_path, checks_yaml, csv_body)
    return common.run_case(config, output_dir=tmp_path / "reports", show_console=False)


def run_cli(args, cwd) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(MAIN), *args], capture_output=True, text=True, cwd=str(cwd))


def case_path(title: str, suffix: str = ".yaml") -> Path:
    """按 yaml 里的 name 找用例文件（文件名是 uuid）。"""
    for item in common.list_cases():
        if common.case_name(item) == title:
            return item.with_suffix(suffix)
    raise AssertionError(f"没有找到用例 {title}")


# ---------------------------------------------------------------- 断言 ----
def test_metric_assertion_pass_and_fail(tmp_path):
    report = run_case_check(tmp_path, """
  - name: 负数年龄数量
    source: users
    severity: high
    sql: SELECT COUNT(*) FROM ${table} WHERE age < 0
    expected: 0
""")
    result = report["results"][0]
    assert result["status"] == "FAIL" and result["result"] == "failed"
    assert result["actual"] == 1
    assert result["expected"] == 0 and result["operator"] == "eq"


@pytest.mark.parametrize("operator,expected,passed", [
    ("eq", 3, True), ("ne", 2, True), ("gt", 2, True), ("gte", 3, True),
    ("lt", 4, True), ("lte", 3, True), ("between", [2, 5], True), ("in", [3, 9], True),
    ("not_in", [1], True), ("eq", 4, False), ("gte", 4, False),
])
def test_operators(operator, expected, passed):
    assert checks.compare(3, expected, operator)[0] is passed


def test_rows_mode_returns_failed_rows(tmp_path):
    report = run_case_check(tmp_path, """
  - name: 非法邮箱
    source: users
    severity: high
    sql: SELECT id, email FROM ${table} WHERE email NOT GLOB '*@*.*'
    mode: rows
""")
    result = report["results"][0]
    assert result["status"] == "FAIL"
    assert result["failed_rows"] == [{"id": 2, "email": "bad-email"}]


def test_threshold_allows_small_failure_rate(tmp_path):
    rows = ["1,a@example.com,active"] * 99 + [",a@example.com,active"]
    report = run_case_check(tmp_path, """
  - name: id 非空
    source: users
    type: not_null
    column: id
    severity: high
    threshold: 0.01
""", "id,email,status\n" + "\n".join(rows) + "\n")
    assert report["results"][0]["status"] == "PASS"
    assert "失败率" in report["results"][0]["message"]


# ---------------------------------------------------------------- 变量 ----
def test_variables_and_override(tmp_path):
    config = make_case(tmp_path, """
  - name: 分区行数
    source: users
    severity: high
    sql: SELECT COUNT(*) FROM ${table} WHERE ${partition}
    expected: "${min_rows}"
    operator: gte
    vars:
      min_rows: 3
""")
    loaded = common.load_config(config)
    assert common.run(loaded)["results"][0]["status"] == "PASS"
    assert common.run(loaded, variables={"min_rows": 10})["results"][0]["status"] == "FAIL"


def test_undefined_variable_is_config_error(tmp_path):
    config = make_case(tmp_path, """
  - name: 变量没定义
    source: users
    severity: high
    sql: SELECT COUNT(*) FROM ${table} WHERE ${dt} > 0
    expected: 0
""")
    with pytest.raises(common.ConfigError, match="变量未定义"):
        common.load_config(config)


# ------------------------------------------------------------ 快捷方式 ----
@pytest.mark.parametrize("check,keyword", [
    ({"type": "not_null", "column": "id"}, "IS NULL"),
    ({"type": "unique", "columns": ["id"]}, "HAVING COUNT(*) > 1"),
    ({"type": "enum", "column": "status", "allowed": ["active"]}, "NOT IN ('active')"),
    ({"type": "range", "column": "age", "min": 0, "max": 120}, '"age" > 120'),
])
def test_shortcuts_generate_sql(check, keyword):
    sql, mode, expected, _ = checks.build_sql(check, "users", {"table": "users"}, ROOT, {})
    assert keyword in sql and mode == "rows" and expected == 0


def test_shortcut_catches_bad_rows_and_wrong_column(tmp_path):
    report = run_case_check(tmp_path, """
  - name: 年龄越界
    source: users
    type: range
    column: age
    min: 0
    max: 120
    severity: high
  - name: 列名写错
    source: users
    type: range
    column: age_typo
    min: 0
    severity: high
""")
    first, second = report["results"]
    assert first["status"] == "FAIL" and first["actual"] == 1     # -5 越界
    assert second["status"] == "ERROR" and "没有列" in second["message"]


# -------------------------------------------------- actual / result ----
def test_runtime_fields_ignored_with_warning(tmp_path):
    config = make_case(tmp_path, """
  - name: status 枚举合法
    source: users
    severity: high
    sql: SELECT COUNT(*) FROM ${table} WHERE status NOT IN ('active','pending')
    expected: 0
    actual: 0
    result: passed
""")
    loaded = common.load_config(config)
    assert "actual" not in loaded.checks[0] and loaded.warnings


def test_runtime_field_without_expected_and_strict_mode(tmp_path):
    body = """
  - name: 只有 actual
    source: users
    severity: high
    sql: SELECT COUNT(*) FROM ${table}
    actual: 0
"""
    with pytest.raises(common.ConfigError, match="没有 expected"):
        common.load_config(make_case(tmp_path, body))
    with pytest.raises(common.ConfigError, match="strict-fields"):
        common.load_config(make_case(tmp_path, body.replace("actual: 0", "expected: 3\n    result: passed")),
                           strict_fields=True)


# ---------------------------------------------------------------- 连接 ----
@pytest.mark.parametrize("source,url,driver", [
    ({"type": "hive", "host": "hive1", "database": "dw"},
     "jdbc:hive2://hive1:10000/dw", "org.apache.hive.jdbc.HiveDriver"),
    ({"type": "hudi", "host": "t1", "catalog": "hive", "schema": "ods"},
     "jdbc:trino://t1:8080/hive/ods", "io.trino.jdbc.TrinoDriver"),
    ({"type": "odps", "host": "odps.example.com", "project": "p1"},
     "jdbc:odps:https://odps.example.com/api?project=p1", "com.aliyun.odps.jdbc.OdpsDriver"),
])
def test_connection_url_and_driver(source, url, driver):
    params = db.resolve(source)
    assert params["url"] == url and params["driver"] == driver


def test_doris_uses_pure_python_driver(monkeypatch):
    monkeypatch.setenv("DORIS_PASSWORD", "secret")
    params = db.resolve({"type": "doris", "host": "fe", "database": "dw",
                         "username": "etl", "password_env": "DORIS_PASSWORD"})
    assert params["url"] == "mysql+pymysql://etl:secret@fe:9030/dw"


def test_inline_password_rejected():
    with pytest.raises(db.DbError, match="明文密码"):
        db.resolve({"type": "mysql", "url": "mysql+pymysql://u:pwd@h:3306/db"})


def test_bigdata_case_config_loads():
    loaded = common.load_config(case_path("大数据连接示例"))
    assert {"hive_dw", "hudi_dw", "odps_dw"} <= set(loaded.sources)


# ---------------------------------------------------------------- 报告 ----
def test_report_files_named_after_case(tmp_path):
    report = run_case_check(tmp_path, """
  - name: 行数
    source: users
    type: row_count
    min: 100
    severity: high
""")
    assert report["summary"]["total"] == 1
    for suffix in ("json", "md", "html"):
        assert (tmp_path / "reports" / f"rules.{suffix}").is_file()
    payload = json.loads((tmp_path / "reports" / "rules.json").read_text(encoding="utf-8"))
    assert {"summary", "results"} <= set(payload)
    assert "# 数据质量报告" in (tmp_path / "reports" / "rules.md").read_text(encoding="utf-8")
    assert "<table" in (tmp_path / "reports" / "rules.html").read_text(encoding="utf-8")


def test_console_report_shows_summary_and_dim(tmp_path):
    report = run_case_check(tmp_path, """
  - name: 行数
    source: users
    type: row_count
    min: 1
    severity: high
""")
    text = common.render_console(report, failed_rows=True)
    assert "门禁：PASS（exit 0）" in text
    assert "按维度" in text


# ------------------------------------------------------- 规则集 / CLI ----
def test_case_resolution_by_uuid_and_name():
    """文件名是 uuid，人类可读名字写在 yaml 的 name 里，两种都能指定用例。"""
    by_name = common.case_yaml("用户数据质量")
    assert by_name == case_path("用户数据质量")
    assert len(by_name.stem) == 36                       # uuid
    assert common.case_yaml(by_name.stem) == by_name      # 完整 uuid
    assert common.case_yaml(by_name.stem[:8]) == by_name  # uuid 前缀
    assert common.case_python(by_name).suffix == ".py"
    assert {common.case_name(item) for item in common.list_cases()} >= {
        "用户数据质量", "用户数据质量_脏数据", "大数据连接示例"}


def test_testcase_python_can_be_run(tmp_path):
    module = common.load_case_module(case_path("用户数据质量", ".py"))
    report = module.run(formats=("md",), output_dir=tmp_path, show_console=False)
    assert report["summary"]["status"] == "PASS"
    assert report["name"] == "用户数据质量"               # 报告里带用例名
    assert (tmp_path / f"{case_path('用户数据质量').stem}.md").is_file()   # 报告名 = uuid


def test_cli_exit_codes_and_only(tmp_path):
    config = make_case(tmp_path, """
  - name: 行数下限
    source: users
    type: row_count
    min: 100
    severity: high
  - name: id 非空
    source: users
    type: not_null
    column: id
    severity: high
""")
    fail = run_cli(["-c", str(config), "--report", "md", "-o", str(tmp_path / "out")], cwd=tmp_path)
    assert fail.returncode == 1 and (tmp_path / "out" / "rules.md").is_file()

    one = run_cli(["-c", str(config), "--only", "行数", "--report", "json",
                   "-o", str(tmp_path / "one")], cwd=tmp_path)
    assert json.loads((tmp_path / "one" / "rules.json").read_text(encoding="utf-8"))["summary"]["total"] == 1
    assert one.returncode == 1

    nothing = run_cli(["-c", str(config), "--only", "不存在", "--report", "none"], cwd=tmp_path)
    assert nothing.returncode == 2 and "--only 没匹配到任何规则" in nothing.stderr


def test_cli_lists_and_case_dispatch(tmp_path):
    assert "用户数据质量" in run_cli(["--list-cases"], cwd=tmp_path).stdout
    assert "not_null" in run_cli(["--list-checks"], cwd=tmp_path).stdout
    listed = run_cli(["--case", "用户数据质量", "--list-rules"], cwd=tmp_path)
    assert "status 枚举合法" in listed.stdout


def test_sample_cases_and_reports_named_by_case():
    """仓库里的示例规则集：干净数据 PASS，脏数据 FAIL。"""
    assert common.run(common.load_config(case_path("用户数据质量")))["summary"]["status"] == "PASS"
    dirty = common.run(common.load_config(case_path("用户数据质量_脏数据")))
    assert dirty["summary"]["status"] == "FAIL" and dirty["summary"]["exit_code"] == 1
