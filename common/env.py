"""数据库连接配置：从 ``.env`` 文件或环境变量读取，支持配置多个连接。

``.env`` 里每个连接用前缀 ``DQ_CONN_<连接名>_<字段>``，例如::

    DQ_CONN_DW_HIVE_TYPE=hive                     # 也可以整串 URL
    DQ_CONN_DW_HIVE_URL=jdbc:hive2://hive-server.example.com:10000/dw
    DQ_CONN_DW_HIVE_USER=etl
    DQ_CONN_DW_HIVE_PASSWORD=xxx
    DQ_CONN_DW_HIVE_DRIVER_PATH=/opt/jdbc/hive-jdbc.jar

    DQ_CONN_DORIS_DW_TYPE=doris                   # 也可以分字段写
    DQ_CONN_DORIS_DW_HOST=doris-fe.example.com
    DQ_CONN_DORIS_DW_PORT=9030
    DQ_CONN_DORIS_DW_DATABASE=dw

规则集里只写连接名::

    sources:
      hive_dw: { conn: dw_hive, table: dim_user }

优先级：代码里传入的 ``conns`` > 进程环境变量 > ``.env`` 文件。
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENV_FILE = ROOT / ".env"
PREFIX = "DQ_CONN_"

# 允许出现在 .env 里的字段（都对应 source 配置里的同名键）
FIELDS = (
    "TYPE", "BACKEND", "URL", "HOST", "PORT", "DATABASE", "SCHEMA", "CATALOG", "PROJECT",
    "SERVICE", "USER", "USERNAME", "PASSWORD", "DRIVER_CLASS", "DRIVER_PATH", "JARS",
    "QUERY_ENGINE", "DIALECT",
)

_state: dict = {"env_file": None, "conns": {}, "loaded": None}


class EnvError(Exception):
    """连接配置缺失或写错。"""


# --------------------------------------------------------------------------- #
# 读 .env / 环境变量
# --------------------------------------------------------------------------- #
def parse_env_file(path: Any) -> dict:
    """解析 .env：KEY=VALUE，支持 # 注释、export 前缀和引号。"""
    values: dict = {}
    text = Path(path).read_text(encoding="utf-8")
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if value[:1] in ("'", '"'):                     # 引号里的内容原样保留
            quote = value[0]
            end = value.find(quote, 1)
            value = value[1:end] if end != -1 else value[1:]
        elif " #" in value:                             # 去掉行尾注释（# 前有空格才算）
            value = value.split(" #", 1)[0].strip()
        values[key.strip()] = value
    return values


def env_file_path(explicit: Any = None) -> Path | None:
    """返回要用的 .env 路径：显式指定的必须存在；默认的可以不存在。"""
    if explicit:
        path = Path(str(explicit)).expanduser()
        if not path.is_file():
            raise EnvError(f"找不到连接配置文件：{path}")
        return path
    if DEFAULT_ENV_FILE.is_file():
        return DEFAULT_ENV_FILE
    return None


def _merged_values() -> dict:
    """进程环境变量 + .env 文件（环境变量优先）。"""
    values: dict = {}
    path = _state.get("env_file")
    if path:
        values.update(parse_env_file(path))
    values.update({key: value for key, value in os.environ.items() if key.startswith(PREFIX)})
    return values


def _split_key(key: str):
    """把 DQ_CONN_DW_HIVE_URL 拆成 (连接名, 字段)。"""
    rest = key[len(PREFIX):]
    for field in sorted(FIELDS, key=len, reverse=True):
        suffix = f"_{field}"
        if rest.endswith(suffix) and len(rest) > len(suffix):
            return rest[: -len(suffix)].lower(), field.lower()
    return None, None


def build_conns() -> dict:
    """把所有 DQ_CONN_* 组装成 ``{连接名: {字段: 值}}``。"""
    conns: dict = {}
    for key, value in _merged_values().items():
        name, field = _split_key(key)
        if not name:
            continue
        conns.setdefault(name, {})[field] = value
    # 代码里传进来的连接（优先级最高）
    for name, config in dict(_state.get("conns") or {}).items():
        conns.setdefault(str(name).lower(), {}).update(dict(config or {}))
    for config in conns.values():                      # 字段名对齐 db.resolve
        if config.get("jars") and not config.get("driver_path"):
            config["driver_path"] = config.pop("jars")
    return conns


def available() -> list:
    """当前可用的连接名。"""
    return sorted(build_conns())


def get_conn(name: str) -> dict:
    """取一个连接的全部字段。"""
    conns = build_conns()
    key = str(name).lower()
    if key not in conns:
        raise EnvError(
            f"没有找到数据库连接 {name!r}；当前可用：{available() or '（空）'}。"
            f"请在 .env（或环境变量）里写 {PREFIX}{str(name).upper()}_HOST 等字段，"
            "或者用 --env 指定配置文件。"
        )
    return dict(conns[key])


def configure(env_file: Any = None, conns: Mapping[str, Any] | None = None) -> dict:
    """设定本次运行用的 .env 文件与代码传入的连接，返回加载信息。"""
    path = env_file_path(env_file)
    _state["env_file"] = path
    _state["conns"] = dict(conns or {})
    return {"env_file": path, "conns": available()}


def describe() -> str:
    """一行说明当前连接来源（日志里好用）。"""
    path = _state.get("env_file")
    names = available()
    where = str(path) if path else "（没有 .env，只能用环境变量）"
    return f"连接配置：{where}；可用连接：{', '.join(names) if names else '（无）'}"


def check_sources(sources: Mapping[str, Any]) -> None:
    """跑之前先确认规则集里引用的 conn 都能找到（早报错）。"""
    used = sorted({str(item["conn"]) for item in sources.values() if item.get("conn")})
    missing = []
    for name in used:
        try:
            get_conn(name)
        except EnvError:
            missing.append(name)
    if missing:
        raise EnvError(
            f"规则集引用的连接在 .env / 环境变量里没有：{missing}；"
            f"当前可用：{available() or '（空）'}。"
            "请把连接信息写进 .env（参考 .env.example），或用 --env 指定配置文件。"
        )
