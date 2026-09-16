"""业务规则集自检：中华保险（ODPS）与华为南方工厂（Hive）。

没有真实集群也能做的两件事：
  1) 规则集配置能加载（字段、变量、枚举都合法）
  2) 每条 SQL 按 Hive 方言解析通过（MaxCompute SQL 与 Hive 语法兼容）
"""

from __future__ import annotations

import pytest

import common
from common import checks

sqlglot = pytest.importorskip("sqlglot", reason="需要 sqlglot 才能做 SQL 语法自检：pip install sqlglot")

INSURANCE = "中华保险_业务数据质量"
MANUFACTURING = "华为南方工厂_制造数据质量"
CASES = {
    INSURANCE: {"sources": {"policy", "claim", "customer"}, "min_checks": 15},
    MANUFACTURING: {"sources": {"work_order", "production", "equipment", "inspection"},
                    "min_checks": 15},
}


@pytest.mark.parametrize("title", list(CASES))
def test_business_case_loads(title):
    config = common.load_config(common.case_yaml(title))
    assert config.name == title
    assert CASES[title]["sources"] <= set(config.sources)
    assert len(config.checks) >= CASES[title]["min_checks"]
    # 每条规则都要有维度和级别，且覆盖基本维度
    dims = {check.get("dim") for check in config.checks}
    assert {"validity", "completeness", "uniqueness", "consistency"} <= dims
    assert all(check["severity"] in ("error", "warn", "info") for check in config.checks)
    # SQL 规则里不允许出现未定义的变量（配置阶段已经校验）
    assert not config.warnings


@pytest.mark.parametrize("title", list(CASES))
def test_business_case_sql_parses(title):
    config = common.load_config(common.case_yaml(title))
    yaml_path = common.case_yaml(title)
    for check in config.checks:
        source = config.sources[check["source"]]
        sql, mode, expected, _operator = checks.build_sql(
            check, check["source"], source, yaml_path.parent, config.vars_for(check)
        )
        try:
            sqlglot.parse_one(sql, dialect="hive")     # MaxCompute SQL 与 Hive 语法兼容
        except Exception as exc:                        # pragma: no cover - 只为报错更可读
            raise AssertionError(f"规则「{check['name']}」的 SQL 解析失败：{exc}\n{sql}") from exc
        assert mode in ("metric", "rows") and expected is not None or mode == "rows"


def test_business_case_partition_and_vars_render():
    """按天跑批的写法：--var partition / bizdate 能正确替换进 SQL。"""
    config = common.load_config(common.case_yaml(INSURANCE))
    variables = config.vars_for(config.checks[0], {"partition": "dt = '2026-09-16'",
                                                   "bizdate": "2026-09-16"})
    sql, *_ = checks.build_sql(config.checks[0], config.checks[0]["source"],
                               config.sources[config.checks[0]["source"]],
                               common.case_yaml(INSURANCE).parent, variables)
    assert "dt = '2026-09-16'" in sql
