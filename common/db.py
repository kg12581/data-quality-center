"""数据源与数据库连接（一个文件搞定）。

- 本地文件：csv / parquet / sqlite
- 数据库与大数据：mysql、oracle、postgresql、doris、hive、spark、trino、hudi、paimon、odps、自定义 jdbc
- 密码只从环境变量读（password_env）；JDBC 驱动 jar 用 driver_path / driver_path_env

对外的函数很少：``load_frame`` 取数、``open_conn`` 拿数据库连接、``frame_conn`` 把 DataFrame
注册成内存表（csv / parquet 也能写 SQL）、``query`` 统一执行 SQL。
"""

from __future__ import annotations

import os
import re
import sqlite3
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import pandas as pd

from . import env


class DbError(Exception):
    """数据源或连接出错。"""


# 每种数据库的默认参数：后端、JDBC 驱动类、url 模板、默认端口、SQLAlchemy dialect、需要的 jar
PROFILES: dict = {
    "mysql": {"backend": "sqlalchemy", "driver": "com.mysql.cj.jdbc.Driver",
              "url": "jdbc:mysql://{host}:{port}/{database}", "port": 3306,
              "dialect": "mysql+pymysql", "jars": "mysql-connector-j.jar"},
    "doris": {"backend": "sqlalchemy", "driver": "com.mysql.cj.jdbc.Driver",
              "url": "jdbc:mysql://{host}:{port}/{database}", "port": 9030,
              "dialect": "mysql+pymysql", "jars": "mysql-connector-j.jar"},
    "oracle": {"backend": "jdbc", "driver": "oracle.jdbc.OracleDriver",
               "url": "jdbc:oracle:thin:@{host}:{port}/{service}", "port": 1521,
               "dialect": "oracle+oracledb", "jars": "ojdbc8.jar"},
    "postgresql": {"backend": "sqlalchemy", "driver": "org.postgresql.Driver",
                   "url": "jdbc:postgresql://{host}:{port}/{database}", "port": 5432,
                   "dialect": "postgresql+psycopg2", "jars": "postgresql.jar"},
    "hive": {"backend": "jdbc", "driver": "org.apache.hive.jdbc.HiveDriver",
             "url": "jdbc:hive2://{host}:{port}/{database}", "port": 10000, "jars": "hive-jdbc.jar"},
    "spark": {"backend": "jdbc", "driver": "org.apache.hive.jdbc.HiveDriver",
              "url": "jdbc:hive2://{host}:{port}/{database}", "port": 10000, "jars": "hive-jdbc.jar"},
    "trino": {"backend": "jdbc", "driver": "io.trino.jdbc.TrinoDriver",
              "url": "jdbc:trino://{host}:{port}/{catalog}/{schema}", "port": 8080, "jars": "trino-jdbc.jar"},
    "hudi": {"backend": "jdbc", "driver": "io.trino.jdbc.TrinoDriver",
             "url": "jdbc:trino://{host}:{port}/{catalog}/{schema}", "port": 8080, "jars": "trino-jdbc.jar",
             "note": "Hudi 没有独立 JDBC，默认经 Trino 查（query_engine 可改 hive / spark）"},
    "paimon": {"backend": "jdbc", "driver": "io.trino.jdbc.TrinoDriver",
               "url": "jdbc:trino://{host}:{port}/{catalog}/{schema}", "port": 8080, "jars": "trino-jdbc.jar",
               "note": "Paimon 同理，默认经 Trino 查（query_engine 可改 hive / spark）"},
    "odps": {"backend": "jdbc", "driver": "com.aliyun.odps.jdbc.OdpsDriver",
             "url": "jdbc:odps:https://{host}/api?project={project}", "jars": "odps-jdbc.jar"},
    "jdbc": {"backend": "jdbc"},
}
BIGDATA_TYPES = tuple(PROFILES)
DB_TYPES = ("sqlite", "sqlalchemy") + BIGDATA_TYPES

_PLACEHOLDERS = ("host", "port", "database", "schema", "catalog", "project", "service")
_IDENTIFIER = re.compile(r"^[A-Za-z_]\w*$")
_INLINE_PWD = re.compile(r"://[^/\s:@]+:[^/\s@]+@")
_CONN_CACHE: dict = {}
_FRAME_CACHE: dict = {}


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #
def resolve_path(path: Any, base_dir: Any = None) -> Path:
    """相对路径依次按「配置文件目录 → 上一级 → 当前目录」查找。"""
    raw = Path(str(path)).expanduser()
    if raw.is_absolute():
        return raw
    bases = [Path(base_dir), Path(base_dir).parent] if base_dir else []
    bases.append(Path.cwd())
    candidates = []
    for base in bases:
        candidate = (base / raw).resolve()
        if candidate not in candidates:
            candidates.append(candidate)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def quote(name: str) -> str:
    """给表名/列名加引号，顺便挡掉拼 SQL 的注入。"""
    parts = str(name).split(".")
    if not parts or any(not _IDENTIFIER.match(part) for part in parts):
        raise DbError(f"非法的表名/列名：{name!r}（只允许字母、数字、下划线，可用 db.table）")
    return ".".join(f'"{part}"' for part in parts)


def _env(source: Mapping[str, Any], field: str, purpose: str) -> Any:
    """先读 ``<field>_env`` 指向的环境变量，再退回字面量 ``<field>``。"""
    env_key = source.get(f"{field}_env")
    if env_key:
        value = os.environ.get(str(env_key))
        if value is None or str(value).strip() == "":
            raise DbError(f"环境变量 {env_key} 未设置或为空（{purpose}）")
        return str(value).strip()
    return source.get(field)


def _check_inline_password(text: str, source: Mapping[str, Any]) -> None:
    if text and not source.get("allow_inline_password") and _INLINE_PWD.search(str(text)):
        raise DbError(
            "连接串里检测到明文密码。请改用 url_env / password_env 从环境变量读取；"
            "确需写死请显式设置 allow_inline_password: true"
        )


def table_name(source_name: str, source: Mapping[str, Any] | None) -> str:
    """SQL 里的内置变量 ${table}：数据库数据源用配置的 table，否则用数据源名。"""
    return str((source or {}).get("table") or source_name)


def supports_conn(source: Mapping[str, Any]) -> bool:
    """该数据源能否直接执行 SQL（决定 run_on: source 是否可用）。"""
    return _kind(source) in DB_TYPES


def _kind(source: Mapping[str, Any]) -> str:
    """数据源类型：YAML 里没写 type 时，从 .env 的连接配置里取。"""
    kind = str(source.get("type") or "").lower()
    if not kind and source.get("conn"):
        try:
            kind = str(env.get_conn(str(source["conn"])).get("type") or "").lower()
        except env.EnvError:
            kind = ""
    return kind


def with_conn(source: Mapping[str, Any]) -> dict:
    """把 ``conn: 名字`` 引用的连接信息合并进来（YAML 里写的字段优先）。"""
    source = dict(source)
    name = source.pop("conn", None)
    if not name:
        return source
    merged = env.get_conn(str(name))
    merged.update({key: value for key, value in source.items() if value is not None})
    return merged


# --------------------------------------------------------------------------- #
# 连接参数与连接对象
# --------------------------------------------------------------------------- #
def resolve(source: Mapping[str, Any]) -> dict:
    """把 YAML 里的 source 配置解析成连接参数。"""
    source = with_conn(source)
    kind = str(source.get("type", "")).lower()
    backend = str(source.get("backend") or "").lower()

    if kind == "sqlite":  # sqlite 用 url 时（例如 sqlite:///data/users.db）走 SQLAlchemy
        url = source.get("url") or (_env(source, "url", "连接串") if source.get("url_env") else "")
        if not url:
            raise DbError("sqlite 用 path 时由 load_frame 直接读取；要直接执行 SQL 请提供 url / url_env")
        return {"kind": kind, "backend": "sqlalchemy", "driver": "", "url": str(url),
                "user": None, "password": None, "jars": [], "project": None,
                "connect_args": {}, "engine_options": {}}

    profile = dict(PROFILES[kind])
    engine = str(source.get("query_engine") or "").lower()
    if kind in ("hudi", "paimon") and engine in ("hive", "spark", "trino"):
        profile.update({key: PROFILES[engine][key] for key in ("driver", "url", "port")})
    backend = backend or profile["backend"]
    user = _env(source, "username", "数据库用户名") or source.get("user")
    password = _env(source, "password", "数据库密码")

    if source.get("url_env"):
        url = _env(source, "url", "数据库连接串")          # 来自环境变量：不检查明文密码
    elif source.get("url"):
        url = str(source["url"]).strip()
        _check_inline_password(url, source)
    elif backend == "sqlalchemy":
        dialect = str(source.get("dialect") or profile.get("dialect") or "")
        host = str(source.get("host") or "")
        if not dialect or not host:
            raise DbError(f"{kind} 的 sqlalchemy 后端需要 dialect 与 host，或直接给 url / url_env")
        port = source.get("port") or profile.get("port")
        auth = ""
        if user:
            auth = quote_plus(str(user)) + (f":{quote_plus(str(password))}" if password else "") + "@"
        database = str(source.get("database") or source.get("schema") or "")
        url = f"{dialect}://{auth}{host}{':' + str(port) if port else ''}/{database}"
    else:
        template = str(source.get("url_template") or profile.get("url") or "")
        if not template:
            raise DbError(f"{kind} 需要 url / url_env / url_template" + (f"（{profile.get('note')}）" if profile.get("note") else ""))
        parts = {name: source.get(name) for name in _PLACEHOLDERS}
        parts["port"] = parts["port"] or profile.get("port")
        parts["schema"] = parts["schema"] or source.get("database")
        parts["catalog"] = parts["catalog"] or (kind if kind in ("hudi", "paimon") else None)
        missing = [name for name in _PLACEHOLDERS if f"{{{name}}}" in template and not parts.get(name)]
        if missing:
            raise DbError(f"{kind} 的连接串缺少字段 {missing}（模板：{template}）")
        url = template.format(**{name: parts.get(name) or "" for name in _PLACEHOLDERS})

    driver = str(source.get("driver_class") or profile.get("driver") or "")
    if backend == "jdbc" and not driver:
        raise DbError(f"{kind} 需要 driver_class（JDBC 驱动类名）")
    return {"kind": kind, "backend": backend, "driver": driver, "url": str(url),
            "user": user, "password": password, "jars": _jars(source),
            "project": source.get("project"), "connect_args": dict(source.get("connect_args") or {}),
            "engine_options": dict(source.get("engine_options") or {})}


def _jars(source: Mapping[str, Any]) -> list:
    """JDBC jar：driver_path / driver_path_env / jars_env（多个用路径分隔符）。"""
    if source.get("driver_path_env") or source.get("jars_env"):
        raw = _env(source, "driver_path", "JDBC jar 路径")
    else:
        raw = source.get("driver_path")
    if not raw:
        return []
    jars = []
    for item in str(raw).split(os.pathsep):
        item = item.strip()
        if not item:
            continue
        path = Path(item).expanduser()
        if not path.is_file():
            raise DbError(f"JDBC jar 不存在：{path}")
        jars.append(str(path))
    return jars


class Conn:
    """统一连接对象：底层是 JDBC / SQLAlchemy / pyodps，对外只有 ``query(sql)``。"""

    def __init__(self, params: Mapping[str, Any]) -> None:
        self.params = dict(params)
        self._conn = None

    def _open(self) -> Any:
        backend = self.params["backend"]
        if backend == "jdbc":
            return self._open_jdbc()
        if backend == "sqlalchemy":
            return self._open_engine()
        if backend == "pyodps":
            return self._open_odps()
        raise DbError(f"不支持的 backend：{backend}（可用 jdbc / sqlalchemy / pyodps）")

    def _open_jdbc(self) -> Any:
        try:
            import jaydebeapi
        except ImportError as exc:
            raise DbError(
                "JDBC 连接需要依赖：pip install jaydebeapi JPype1（并保证 JAVA_HOME 可用）"
            ) from exc
        args = [str(self.params["user"])] if self.params.get("user") is not None else []
        if self.params.get("password") is not None:
            args.append(str(self.params["password"]))
        try:
            return jaydebeapi.connect(
                self.params["driver"], self.params["url"], args or None, self.params.get("jars") or None
            )
        except Exception as exc:
            raise DbError(
                f"JDBC 连接失败：{type(exc).__name__}: {exc}"
                f"（driver={self.params['driver']}, url={self.params['url']}）"
            ) from exc

    def _open_engine(self) -> Any:
        try:
            from sqlalchemy import create_engine
        except ImportError as exc:
            raise DbError("sqlalchemy 后端需要安装：pip install sqlalchemy") from exc
        options = dict(self.params.get("engine_options") or {})
        if self.params.get("connect_args"):
            options["connect_args"] = dict(self.params["connect_args"])
        try:
            return create_engine(self.params["url"], **options)
        except Exception as exc:
            raise DbError(f"创建数据库连接失败：{type(exc).__name__}: {exc}") from exc

    def _open_odps(self) -> Any:
        try:
            from odps import ODPS
        except ImportError as exc:
            raise DbError("pyodps 后端需要安装：pip install pyodps") from exc
        endpoint = str(self.params.get("url") or "").replace("jdbc:odps:", "")
        return ODPS(self.params.get("user"), self.params.get("password"),
                    str(self.params.get("project") or ""), endpoint=endpoint)

    def raw(self) -> Any:
        if self._conn is None:
            self._conn = self._open()
        return self._conn

    def query(self, sql: str) -> pd.DataFrame:
        backend = self.params["backend"]
        if backend == "pyodps":
            instance = self.raw().execute_sql(str(sql))
            with instance.open_reader() as reader:
                return reader.to_pandas()
        if backend == "sqlalchemy":
            with self.raw().connect() as connection:
                return pd.read_sql_query(str(sql), connection)
        cursor = self.raw().cursor()
        try:
            cursor.execute(str(sql))
            if not cursor.description:
                return pd.DataFrame()
            columns = [item[0] for item in cursor.description]
            return pd.DataFrame([list(row) for row in cursor.fetchall()], columns=columns)
        finally:
            cursor.close()

    def close(self) -> None:
        if self._conn is None:
            return
        for method in ("close", "dispose"):
            func = getattr(self._conn, method, None)
            if callable(func):
                try:
                    func()
                except Exception:
                    pass
        self._conn = None


def open_conn(source: Mapping[str, Any], cache: bool = True) -> Conn:
    """按 source 配置打开（并缓存）数据库连接。"""
    params = resolve(source)
    key = "|".join([params["backend"], params["driver"], params["url"], str(params["project"])])
    if cache and key in _CONN_CACHE:
        return _CONN_CACHE[key]
    conn = Conn(params)
    if cache:
        _CONN_CACHE[key] = conn
    return conn


def frame_conn(frame: pd.DataFrame, tables) -> Any:
    """把 DataFrame 注册成内存 SQLite 表，返回可执行 SQL 的连接。"""
    names = [str(name) for name in dict.fromkeys(tables) if name]
    key = f"{id(frame)}|{'|'.join(names)}"
    if key in _FRAME_CACHE:
        return _FRAME_CACHE[key]
    try:
        from sqlalchemy import create_engine
    except ImportError:
        connection = sqlite3.connect(":memory:")
        for name in names:
            frame.to_sql(name, connection, index=False, if_exists="replace")
    else:
        connection = create_engine("sqlite://")
        for name in names:
            frame.to_sql(name, connection, index=False, if_exists="replace")
    _FRAME_CACHE[key] = connection
    return connection


def query(connection: Any, sql: str) -> pd.DataFrame:
    """统一执行 SQL：Conn 走自己的实现，其它连接（Engine / DBAPI）交给 pandas。"""
    if hasattr(connection, "query") and callable(connection.query):
        return connection.query(str(sql))
    return pd.read_sql_query(str(sql), connection)


def reset() -> None:
    """关闭所有缓存的连接（测试或多次运行之间用）。"""
    for conn in list(_CONN_CACHE.values()):
        conn.close()
    _CONN_CACHE.clear()
    for connection in list(_FRAME_CACHE.values()):
        for method in ("close", "dispose"):
            func = getattr(connection, method, None)
            if callable(func):
                try:
                    func()
                except Exception:
                    pass
    _FRAME_CACHE.clear()


# --------------------------------------------------------------------------- #
# 取数
# --------------------------------------------------------------------------- #
def _select(source: Mapping[str, Any]) -> str:
    if source.get("query"):
        return str(source["query"])
    if source.get("table"):
        return f"SELECT * FROM {quote(source['table'])}"
    raise DbError("数据库数据源需要提供 table 或 query")


def load_frame(name: str, source: Mapping[str, Any], base_dir: Any = None) -> pd.DataFrame:
    """把数据源加载成 DataFrame（csv / parquet / sqlite / 数据库 / 大数据）。"""
    kind = str(source.get("type", "")).lower()
    if kind in ("csv", "parquet"):
        path = resolve_path(source.get("path"), base_dir)
        if not path.is_file():
            raise DbError(f"文件不存在：{path}")
        reader = pd.read_csv if kind == "csv" else pd.read_parquet
        return reader(path, **dict(source.get("read_options") or {}))
    if kind == "sqlite" and source.get("path"):
        path = resolve_path(source.get("path"), base_dir)
        if not path.is_file():
            raise DbError(f"SQLite 文件不存在：{path}")
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            return pd.read_sql_query(_select(source), connection)
        finally:
            connection.close()
    if kind in DB_TYPES:
        return open_conn(source).query(_select(source))
    raise DbError(f"不支持的数据源类型：{kind!r}（可用 csv / parquet / sqlite / sqlalchemy / {' / '.join(BIGDATA_TYPES)}）")
