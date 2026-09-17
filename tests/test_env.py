"""连接配置（.env）相关测试：多连接、引用、优先级、缺失报错、端到端联调。"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

import common
from common import db, env

ENV_FILE = """\
# 注释会被忽略
export DQ_CONN_DW_HIVE_TYPE=hive
DQ_CONN_DW_HIVE_URL="jdbc:hive2://hive-server.example.com:10000/dw"
DQ_CONN_DW_HIVE_USER=etl
DQ_CONN_DW_HIVE_PASSWORD='pwd#1'          # 引号里的内容原样保留
DQ_CONN_DW_HIVE_DRIVER_PATH=/opt/jdbc/hive-jdbc.jar

DQ_CONN_DORIS_DW_TYPE=doris
DQ_CONN_DORIS_DW_HOST=doris-fe.example.com
DQ_CONN_DORIS_DW_PORT=9030
DQ_CONN_DORIS_DW_DATABASE=dw
DQ_CONN_DORIS_DW_USER=etl
DQ_CONN_DORIS_DW_PASSWORD=secret
"""

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "main.py"


@pytest.fixture(autouse=True)
def _clean():
    yield
    db.reset()
    env.configure(env_file=None, conns={})


def write_env(tmp_path: Path, body: str = ENV_FILE) -> Path:
    jar = tmp_path / "hive-jdbc.jar"
    jar.write_bytes(b"jar")                             # 假装有一个驱动 jar
    path = tmp_path / "test.env"
    path.write_text(body.replace("/opt/jdbc/hive-jdbc.jar", str(jar)), encoding="utf-8")
    return path


def test_parse_env_file(tmp_path):
    values = env.parse_env_file(write_env(tmp_path))
    assert values["DQ_CONN_DW_HIVE_URL"] == "jdbc:hive2://hive-server.example.com:10000/dw"
    assert values["DQ_CONN_DW_HIVE_PASSWORD"] == "pwd#1"
    assert all(not key.startswith("#") for key in values)


def test_multiple_connections_and_yaml_reference(tmp_path):
    env.configure(env_file=write_env(tmp_path))
    assert env.available() == ["doris_dw", "dw_hive"]

    source = db.with_conn({"conn": "dw_hive", "table": "dim_user", "type": "hive"})
    params = db.resolve(source)
    assert params["url"] == "jdbc:hive2://hive-server.example.com:10000/dw"
    assert params["driver"] == "org.apache.hive.jdbc.HiveDriver"
    assert params["user"] == "etl"
    assert params["jars"] == [str(tmp_path / "hive-jdbc.jar")]

    doris = db.resolve({"conn": "doris_dw"})           # type 来自 .env
    assert doris["url"] == "mysql+pymysql://etl:secret@doris-fe.example.com:9030/dw"


def test_env_var_overrides_env_file_and_conns_win(tmp_path, monkeypatch):
    env.configure(env_file=write_env(tmp_path))
    monkeypatch.setenv("DQ_CONN_DORIS_DW_HOST", "doris-override.example.com")
    assert "doris-override.example.com" in db.resolve({"conn": "doris_dw"})["url"]

    env.configure(env_file=write_env(tmp_path),
                  conns={"doris_dw": {"host": "doris-cli.example.com", "port": 9031}})
    assert "doris-cli.example.com:9031" in db.resolve({"conn": "doris_dw"})["url"]


def test_missing_conn_is_reported_early(tmp_path):
    path = tmp_path / "config" / "rules.yaml"
    path.parent.mkdir()
    (path.parent / "users.csv").write_text("id\n1\n", encoding="utf-8")
    path.write_text(
        "version: 1\n"
        "sources:\n"
        "  hive_dw:\n"
        "    type: hive\n"
        "    conn: not_exists\n"
        "    table: dim_user\n"
        "checks:\n"
        "  - name: 行数\n"
        "    source: hive_dw\n"
        "    type: row_count\n"
        "    min: 1\n"
        "    severity: high\n",
        encoding="utf-8",
    )
    with pytest.raises(common.ConfigError, match="没有找到数据库连接|没有：\\['not_exists'\\]"):
        common.run_case(path, env_file=write_env(tmp_path), show_console=False)


def test_sqlite_connection_from_env_end_to_end(tmp_path):
    """没集群时也能验证：.env 里配一个 sqlite 连接，规则集里用 conn 引用。"""
    db_path = tmp_path / "users.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE users (id INTEGER, age INTEGER)")
        connection.executemany("INSERT INTO users VALUES (?, ?)", [(1, 20), (2, -5)])
        connection.commit()

    env_path = tmp_path / "test.env"
    env_path.write_text(
        f"DQ_CONN_LOCAL_DB_TYPE=sqlite\nDQ_CONN_LOCAL_DB_URL=sqlite:///{db_path}\n",
        encoding="utf-8",
    )
    case = tmp_path / "sqlite_用例.yaml"
    case.write_text(
        "version: 1\n"
        "sources:\n"
        "  users_db:\n"
        "    type: sqlite\n"
        "    conn: local_db\n"
        "    table: users\n"
        "checks:\n"
        "  - name: 负数年龄\n"
        "    source: users_db\n"
        "    dim: validity\n"
        "    severity: high\n"
        "    sql: SELECT COUNT(*) FROM ${table} WHERE age < 0\n"
        "    expected: 0\n",
        encoding="utf-8",
    )
    report = common.run_case(case, env_file=env_path, output_dir=tmp_path / "reports",
                             show_console=False)
    result = report["results"][0]
    assert result["status"] == "FAIL" and result["actual"] == 1        # 走的是真库
    assert (tmp_path / "reports" / "sqlite_用例.json").is_file()


def test_case_entry_accepts_env_argument(tmp_path):
    """testcase/xxx.py --env 的入口（服务器定时任务用法）。"""
    env_path = write_env(tmp_path)
    py_file = common.case_python("用户数据质量")
    code = common.case_entry(py_file, ["--env", str(env_path), "--report", "none"])
    assert code == 0            # 用例本身通过；--env 被正确接收

    bad = common.case_entry(py_file, ["--report", "md", "-o", str(tmp_path / "out")])
    assert bad == 0
    assert (tmp_path / "out" / f"{py_file.stem}.md").is_file()   # 报告名 = uuid


def test_cli_env_file_missing_is_clean_error(tmp_path):
    """--env 指向不存在的文件：友好报错 + 退出码 2，不能抛堆栈。"""
    proc = subprocess.run(
        [sys.executable, str(MAIN), "--env", str(tmp_path / "nope.env"), "--report", "none"],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    assert proc.returncode == 2
    assert "找不到连接配置文件" in proc.stderr
    assert "Traceback" not in proc.stderr


def test_cli_missing_conn_name_lists_available(tmp_path):
    env_path = write_env(tmp_path)
    case = tmp_path / "case.yaml"
    case.write_text(
        "version: 1\n"
        "sources:\n"
        "  users_db:\n"
        "    type: sqlite\n"
        "    conn: local_db_typo\n"
        "    table: users\n"
        "checks:\n"
        "  - name: 行数\n"
        "    source: users_db\n"
        "    type: row_count\n"
        "    min: 1\n"
        "    severity: high\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [sys.executable, str(MAIN), "--case", str(case), "--env", str(env_path),
         "--report", "none"],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    assert proc.returncode == 2
    assert "local_db_typo" in proc.stderr and "可用" in proc.stderr
