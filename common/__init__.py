"""data-quality-center：可配置的数据质量校验工具。

一个规则集 = ``testcase/<名字>.yaml`` + 同名 ``testcase/<名字>.py``；
公共代码都在 common/ 里，规则集文件只负责调用 :func:`run_case`。
报告写到 ``reports/<名字>.json / .md / .html``。
"""

from .case import (CASE_DIR, DEFAULT_FORMATS, REPORT_DIR, case_entry, case_name, case_python,
                   case_yaml, find_cases, list_cases, load_case_module, load_rules, parse_only,
                   parse_reports, parse_vars, run_case, run_case_by_name)
from .checks import CheckResult, run_check
from .config import Config, ConfigError, load_config
from .db import DbError, load_frame, open_conn, reset
from .report import render_console, render_failed_rows, to_html, to_markdown, write_reports
from .runner import Sources, run, select_checks
from . import env

__version__ = "3.0.0"

__all__ = [
    "CheckResult", "run_check", "Config", "ConfigError", "load_config",
    "DbError", "load_frame", "open_conn", "reset", "Sources", "run", "select_checks",
    "render_console", "render_failed_rows", "to_html", "to_markdown", "write_reports",
    "run_case", "run_case_by_name", "list_cases", "case_yaml", "case_python",
    "case_name", "find_cases",
    "load_case_module", "CASE_DIR", "REPORT_DIR", "DEFAULT_FORMATS",
    "case_entry", "load_rules", "parse_reports", "parse_vars", "parse_only", "env",
    "__version__",
]
