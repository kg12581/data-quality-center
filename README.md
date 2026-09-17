# data-quality-center

可配置的数据质量校验工具：**规则写在 YAML，工具执行 SQL 并出报告**。

- 一个用例 = `testcase/<uuid>.yaml`（规则）+ 同名 `testcase/<uuid>.py`（入口，可直接 Run）
- 数据库连接集中在 `.env`（支持多套连接），规则集里只写连接名
- 报告三份：`reports/<uuid>.json` / `.md` / `.html`，外加控制台输出
- 退出码可直接接 CI / 调度告警：`0` 通过、`1` 数据质量问题、`2` 配置或参数问题

## 5 分钟上手

```bash
pip install -r requirements.txt          # 只跑 csv 的话，pandas + PyYAML 就够

python main.py                          # 跑 main.py 里 RULE_SET 指定的用例
python main.py --list-cases             # 有哪些用例（uuid + 名字）
python main.py --case 用户数据质量 --failed-rows     # 跑指定用例，控制台带失败样本
python main.py --all                    # 跑 testcase/ 下全部用例
```

在 PyCharm 里也可以直接 Run `testcase/<uuid>.py`（文件顶部配置区能改 YAML、连接文件、报告格式等）。

## 目录结构

```
data-quality-center/
├── main.py                      # 入口：RULE_SET 变量 / --case 指定跑哪个用例
├── common/                      # 公共代码（改这里要跑 pytest）
│   ├── case.py                  #   用例入口：找 YAML、加载 .env、跑规则、写报告
│   ├── config.py                #   读 YAML + 校验（报错会告诉你哪里写错）
│   ├── db.py                    #   数据源与连接（csv/parquet/sqlite + JDBC 大数据）
│   ├── env.py                   #   .env 多连接解析（DQ_CONN_*）
│   ├── checks.py                #   规则 → SQL → 断言（指标 / 明细）
│   ├── runner.py                #   跑规则并汇总（含按维度、按级别统计）
│   └── report.py                #   控制台 + json / md / html（含历史归档）
├── testcase/                    # 用例：yaml 与 py 同名，只差后缀
│   ├── bec36bd4-….yaml / .py    #   用户数据质量（csv 示例，7 条规则）
│   ├── 4c307f9c-….yaml / .py    #   用户数据质量_脏数据（演示失败、退出码 1）
│   ├── 1a572f6e-….yaml / .py    #   大数据连接示例（hive/hudi/paimon/doris/odps…）
│   ├── 6e49032b-….yaml / .py    #   保险_承保理赔数据质量（ODPS，17 条）
│   └── 4d1c7884-….yaml / .py    #   制造业_工单制造数据质量（Hive，19 条）
├── data/                        # 示例数据（csv）
├── reports/                     # 报告输出（已 gitignore，含 history/ 历史归档）
├── docs/                        # 手册：规则编写 / 连接配置 / 运维调度 / 常见问题
├── scripts/new_case.py          # 一键生成新用例（uuid + yaml + py）
├── tests/                       # pytest（67 个用例，只测 common/）
├── .github/workflows/           # ci.yml（提交跑测试）、nightly.yml（定时跑用例）
├── .env.example / .env          # 连接配置模板 / 本地真实值（.env 已 gitignore）
├── Makefile                     # make test / run / all / list / new / clean
├── CHANGELOG.md / requirements.txt / pyproject.toml
```

## 文件命名：uuid + yaml 里的 name

- 用例文件名用 **uuid**（程序引用方便，避免中文/空格在服务器上的编码问题）
- 人能看懂的名字写在 YAML 顶部，报告与控制台都会显示：

  ```yaml
  name: 用户数据质量
  description: CSV 用户表示例：id 唯一非空、年龄区间、邮箱格式、状态枚举、行数下限
  ```

- `--case` 三种写法（精确优先）：完整 uuid、uuid 前缀、yaml 里的 name

  ```bash
  python main.py --case bec36bd4-5b66-474b-a163-1fa6c5d23b2f
  python main.py --case bec36bd4
  python main.py --case 用户数据质量
  ```

## 规则怎么写

三种形态，都能直接跑（详见 [docs/01-规则编写手册.md](docs/01-规则编写手册.md)）：

```yaml
name: 用户数据质量
version: 1
vars:
  partition: "1=1"          # 内置变量：source / table / partition；--var 可覆盖
sources:
  users:
    type: csv               # csv / parquet / sqlite / hive / odps / doris / mysql …
    path: data/users.csv    # 数据库类型改成：conn: <连接名> + table: <表名>
checks:
  # ① SQL 断言：expected + operator（SQL 返回一行）
  - name: status 枚举合法
    source: users
    dim: validity           # 维度：completeness/uniqueness/validity/consistency/accuracy/timeliness
    severity: high          # high=error；也支持 error/warn/info、medium/low
    sql: |
      SELECT COUNT(*) FROM ${table}
      WHERE ${partition} AND status NOT IN ('active','inactive','pending')
    expected: 0

  # ② SQL 明细：返回的行就是失败明细（自带样本，可容忍失败率）
  - name: 邮箱格式非法明细
    source: users
    dim: validity
    severity: high
    sql: SELECT id, email FROM ${table} WHERE email NOT GLOB '*@*.*'
    mode: rows
    threshold: 0.01         # 失败率 ≤1% 仍算通过
    sample_limit: 20        # 失败样本最多留 20 行（默认 10）

  # ③ 快捷方式：不写 SQL，自动生成
  - { name: 用户 id 非空, source: users, dim: completeness, severity: high,
      type: not_null, column: id }
  - { name: 行数下限, source: users, dim: completeness, severity: medium,
      type: row_count, min: 100 }
```

常用字段：`name / source / severity`（必填）、`dim / description / vars / enabled`、
`sql / sql_file / query`、`expected`（别名 `expect`）、`operator`（`eq/ne/gt/gte/lt/lte/between/in/not_in`）、
`mode`（`metric`/`rows`）、`threshold`、`sample_limit`、`filter`、`run_on`（`auto`/`frame`/`source`）。

> `actual` / `result` / `message` 是运行结果，由工具回填到报告；规则里写了会被忽略并提示一行
> （`--strict-fields` 直接报错），只写 `actual` 而没有 `expected` 会报错。

## 数据源与连接

| type | 说明 |
| --- | --- |
| `csv` / `parquet` | 本地文件；会注册成内存表，所以也能写 SQL（SQLite 语法） |
| `sqlite` | `path: data/x.db`（只读）或 `url: sqlite:///...` |
| `mysql` `doris` `postgresql` `oracle` | 关系库；mysql/doris/pg 默认纯 Python 驱动，oracle 默认 JDBC |
| `hive` `spark` `trino` `hudi` `paimon` | 大数据，走 JDBC；Hudi/Paimon 默认经 Trino 查（`query_engine` 可切） |
| `odps` | 阿里云 MaxCompute；JDBC 或 `backend: pyodps` |
| `jdbc` | 自定义：`driver_class` + `url` |

连接信息写在 `.env`（复制 `.env.example` 改值即可，`.env` 已 gitignore），规则集里只写连接名：

```ini
DQ_CONN_ODPS_DW_TYPE=odps
DQ_CONN_ODPS_DW_PROJECT=ins_ods
DQ_CONN_ODPS_DW_HOST=service.<region>.maxcompute.aliyun.com
DQ_CONN_ODPS_DW_USER=your_access_id
DQ_CONN_ODPS_DW_PASSWORD=your_access_key
```

```yaml
sources:
  policy: { type: odps, conn: odps_dw, table: ods_policy_di }
```

优先级：**代码传入的 conns > 进程环境变量 > `.env` 文件**；三种传入方式见
[docs/02-连接配置手册.md](docs/02-连接配置手册.md)。`python main.py --list-connectors` 可查每种类型的默认 driver、端口、所需 jar。

## 运行与参数

| 参数 | 作用 |
| --- | --- |
| `--case/-c <uuid或名字>` | 跑哪个用例（默认取 `main.py` 里的 `RULE_SET`） |
| `--all` | 跑 `testcase/` 下全部用例 |
| `--report console,json,md,html` | 输出方式（`all` / `none` 也可） |
| `-o/--output-dir` | 报告目录（默认 `reports/`） |
| `--env <file>` | 用指定的连接配置文件（默认仓库根 `.env`） |
| `--var NAME=VALUE` | 覆盖规则变量，可重复（如 `--var partition="dt = '2026-09-16'"`） |
| `--only <名字>` | 只跑某几条规则（子串 / 通配符 / 逗号分隔） |
| `--failed-rows` | 控制台打印失败样本行 |
| `--include-sql` | 报告里带上渲染后的 SQL（排查用） |
| `--archive` | 额外往 `<报告目录>/history/` 留一份带时间戳的历史报告 |
| `--summary-json` | stdout 打印一行 JSON 摘要（给调度器/监控解析） |
| `--fail-on-warn` | warn 级失败也让退出码变成 1 |
| `--strict-fields` | 规则里出现 `actual`/`result` 等运行结果字段时直接报错 |
| `--list-cases` / `--list-rules` / `--list-checks` / `--list-connectors` | 各类清单 |

退出码：`0` 全部通过（或只有 warn/info 失败）；`1` 有 error 级规则失败或执行出错；`2` 规则或参数写错。

## 报告

默认输出控制台 + 三份文件（文件名 = 用例文件名）：

```
reports/<uuid>.json    # summary / results / sources（含 actual、expected、operator、result、failed_rows）
reports/<uuid>.md      # Markdown：汇总 → 按维度 → 检查明细 → 失败明细（贴群/PR/归档）
reports/<uuid>.html    # 单文件页面，失败样本可展开
```

控制台示例：

```
数据质量报告    用例：用户数据质量    规则文件：bec36bd4-….yaml
合计 7 项 | 通过 7 | 失败 0 | 错误 0 | 门禁：PASS（exit 0）
按维度：completeness 2/2 通过；uniqueness 1/1 通过；validity 4/4 通过
| 检查项          | 数据源 | 维度     | 规则类型 | 级别  | 状态 | 实际 | 期望 | 说明 |
| status 枚举合法 | users  | validity | sql      | error | PASS | 0    | 0    | 指标 COUNT(*)=0，期望 eq 0 通过 |
```

`--archive` 会额外留下 `reports/history/<uuid>_20260917-020000.json` 这样的历史快照，方便对比。

## 提交到服务器定时运行

```bash
# ① 指定服务器上的连接配置（推荐）
python testcase/bec36bd4-5b66-474b-a163-1fa6c5d23b2f.py --env /opt/conf/prod.env \
  --var partition="dt = '2026-09-16'" --report json,md --archive --summary-json

# ② 连接信息用环境变量注入（不落盘）
DQ_CONN_ODPS_DW_HOST=prod-odps DQ_CONN_ODPS_DW_USER=etl DQ_CONN_ODPS_DW_PASSWORD=xxx \
python main.py --case 保险_承保理赔数据质量 --report none

# ③ 平台集成：代码里传 conns
python -c "import common; common.run_case('保险_承保理赔数据质量', conns={'odps_dw': {...}})"
```

调度与监控（crontab 示例、告警、历史对比）见 [docs/03-运维与调度.md](docs/03-运维与调度.md)。

## 内置业务规则集

| uuid | 用例名 | 数据源 | 规则数 |
| --- | --- | --- | --- |
| `6e49032b-ea6e-4621-8dbc-bf4bfc3a832a` | 保险_承保理赔数据质量 | ODPS（保单/赔案/客户） | 17 |
| `4d1c7884-d049-48a4-bd86-17fdbd95f495` | 制造业_工单制造数据质量 | Hive（工单/报工/设备/质检） | 19 |

覆盖唯一性、非空、格式、金额勾稽、日期逻辑、跨表关联、良率/不良率阈值、分区完整性与及时性等。
仓库内所有主机、项目、账号、表名均为示例占位，不含真实环境信息。

## 常用脚本与 CI

```bash
python scripts/new_case.py --name 订单数据质量 --type odps --conn odps_dw --table ods_order_di
make test                                  # pytest
make run CASE=bec36bd4 ARGS="--failed-rows"
make new NAME=订单数据质量 TYPE=hive CONN=hive_mfg TABLE=ods_order_di
```

- `.github/workflows/ci.yml`：push/PR 时在 Python 3.10/3.11/3.12 上跑 pytest + 跑一遍 CSV 示例用例
- `.github/workflows/nightly.yml`：每天定时（或手动触发）跑示例用例，报告作为 artifact 上传

## 开发者

```bash
pytest -q                    # 67 passed
ruff check .                 # 代码风格（pip install ruff）
```

`common/` 的公共方法：`run_case()` / `load_rules()`（case）、`load_config()`（config）、
`load_frame()` / `open_conn()` / `query()`（db）、`configure()` / `get_conn()`（env）、
`run_check()` / `build_sql()` / `compare()`（checks）、`run()` / `select_checks()`（runner）、
`render_console()` / `write_reports()` / `to_markdown()` / `to_html()`（report）。

## 文档

| 文档 | 内容 |
| --- | --- |
| [docs/01-规则编写手册.md](docs/01-规则编写手册.md) | 三种规则形态、字段速查、变量、阈值、写完怎么验证 |
| [docs/02-连接配置手册.md](docs/02-连接配置手册.md) | `.env` 命名规则、各数据库要点、优先级与安全约定、排错 |
| [docs/03-运维与调度.md](docs/03-运维与调度.md) | 本地/PyCharm 运行、报告与归档、crontab 示例、CI、告警 |
| [docs/04-常见问题.md](docs/04-常见问题.md) | 运行、规则、连接类常见报错的处理 |
| [CHANGELOG.md](CHANGELOG.md) | 版本变更记录 |
