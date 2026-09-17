# 更新记录

## 3.1.0

**新增能力**

- 报告与排查：`--include-sql`（报告带渲染后的 SQL）、`--archive`（`reports/history/` 留带时间戳的历史报告）、
  `--summary-json`（stdout 一行 JSON 摘要，供调度器/监控解析）
- 门禁：`--fail-on-warn`（warn 级失败也让退出码变成 1）
- 规则：`sample_limit` 对所有规则类型生效（失败样本行数，默认 10，上限 1000）
- 报告内容：结果里增加 `source_type` / `conn_name`（不含主机与账号），报告头增加数据源清单
- `--summary-json` 会带上本次写出的报告文件路径（`reports` 字段）
- 工具：`scripts/new_case.py` 一键生成新用例、`Makefile` 常用命令
- 工程化：`.github/workflows/ci.yml`（push/PR 跑测试 + 跑示例用例）、`nightly.yml`（定时跑用例并上传报告）
- 文档：`docs/01-规则编写手册`、`docs/02-连接配置手册`、`docs/03-运维与调度`、`docs/04-常见问题`

**改进**

- 主入口与用例 py 的调用增加了参数签名过滤：以后新增命令行参数不会弄坏旧用例
- 用例 py 的 `run()` 统一支持 `include_sql` / `fail_on_warn` / `archive`
- README 重写为「门面 + 文档索引」，细节拆到 `docs/`；新增 `ruff` 配置与 Makefile

## 3.0.0

- 目录规范：一个用例 = `testcase/<uuid>.yaml` + 同名 `.py`；公共代码集中在 `common/`
- 数据库连接集中到 `.env`（支持多连接、JDBC/大数据、`--env` 与环境变量/代码参数三种传入方式）
- 报告：控制台 + `reports/<uuid>.json / .md / .html`

## 2.0.0

- 精简为 6 个模块（config/db/checks/runner/report + main），一切检查都翻译成 SQL 执行
- `dim`（数据质量维度）、severity 别名（high/medium/low）、SQL 指标/明细两种模式、9 种 operator

## 1.0.0

- 初版：YAML 规则 + pandas/SQL 混合执行、控制台与 JSON/HTML 报告
