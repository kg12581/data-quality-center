# data-quality-center

数据质量校验工具。约定很简单：

**一个规则集 = `testcase/<uuid>.yaml` + 同名 `testcase/<uuid>.py`**，公共代码都在 `common/`，
报告按用例文件名生成：`reports/<uuid>.json`、`reports/<uuid>.md`、`reports/<uuid>.html`。

## 目录结构

```
data-quality-center/
├── main.py                      # 主入口：改 RULE_SET 变量或命令行 --case 指定跑哪个规则集
├── common/                      # 公共方法（能被提取出来的都放这里）
│   ├── case.py                  #   规则集入口：找同名 yaml、跑规则、写报告
│   ├── config.py                #   读 YAML + 校验规则（报错会告诉你哪里写错）
│   ├── db.py                    #   数据源与数据库连接（csv/parquet/sqlite + JDBC 大数据）
│   ├── checks.py                #   把规则翻译成 SQL、执行、和 expected 比较
│   ├── runner.py                #   跑规则集并汇总
│   └── report.py                #   控制台 + json / md / html 报告
├── testcase/                    # 规则集：yaml 与 py 同名，只差后缀
│   ├── bec36bd4-….yaml / .py          #   用户数据质量（干净数据）
│   ├── 4c307f9c-….yaml / .py          #   用户数据质量_脏数据（演示失败、退出码 1）
│   └── 1a572f6e-….yaml / .py          #   大数据连接示例（hive/hudi/odps/doris/…）
│   ├── 6e49032b-….yaml / .py          #   中华保险_业务数据质量（ODPS：保单/赔案/客户）
│   └── 4d1c7884-….yaml / .py          #   华为南方工厂_制造数据质量（Hive：工单/报工/设备/质检）
├── data/                        # 测试数据（csv）
├── reports/                     # 生成的报告（文件名 = 规则集名）
├── tests/                       # pytest（只测 common/，不参与运行，可删）
├── .env.example                 # 数据库连接配置模板（复制成 .env 用）
├── .env                         # 本地连接配置：IP/端口/账号/密码（已 gitignore）
├── requirements.txt / pyproject.toml
```

## 文件命名：uuid + yaml 里的 name

- `testcase/` 下的 yaml 与 py 用 **uuid** 命名（同名、只差后缀），程序引用方便，也避免中文/空格在服务器上的编码坑
- 人能看懂的名字写在 YAML 顶部（报告和控制台都会显示）：

  ```yaml
  name: 用户数据质量
  description: CSV 用户表示例：id 唯一非空、年龄区间、邮箱格式、状态枚举、行数下限
  ```

- 报告文件名 = 用例文件名：`reports/bec36bd4-5b66-474b-a163-1fa6c5d23b2f.json / .md / .html`
- `--case` 支持三种写法（精确优先）：

  ```bash
  python main.py --case bec36bd4-5b66-474b-a163-1fa6c5d23b2f   # 完整 uuid
  python main.py --case bec36bd4                               # uuid 前缀
  python main.py --case 用户数据质量                            # yaml 里的 name（精确优先，再模糊）
  ```

- `python main.py --list-cases` 会同时列出 uuid、name 和同名 py 是否存在

## 内置的业务规则集

| uuid | 用例名 | 数据源 | 规则数 | 覆盖内容 |
| --- | --- | --- | --- | --- |
| `6e49032b-ea6e-4621-8dbc-bf4bfc3a832a` | 中华保险_业务数据质量 | **ODPS / MaxCompute**（保单 `ods_policy_di`、赔案 `ods_claim_di`、客户 `ods_customer_di`） | 17 | 保单号/证件号唯一与非空、保单号与证件号/手机号格式、保费非负、保额>0、起终保日期逻辑、保单与赔案状态枚举、保费勾稽（应收=实收+欠收）、赔案日期逻辑、赔付不超保额（跨表关联）、分区完整性与及时性 |
| `4d1c7884-d049-48a4-bd86-17fdbd95f495` | 华为南方工厂_制造数据质量 | **Hive**（工单 `ods_mes_work_order_di`、报工 `ods_mes_production_di`、设备 `ods_mes_equipment_df`、质检 `ods_qc_inspection_di`） | 19 | 工单号唯一与非空、计划数量>0、完成不超计划、生产数=良品+不良、良率区间与口径一致、不良率上限（阈值参数化）、报工时间逻辑与未来日期、产线覆盖数、设备编码唯一、设备状态与停机时长、质检结果枚举与不良代码完整性、分区完整性 |

跑法（按天 T+1 建议显式指定分区和业务日期）：

```bash
# 中华保险（ODPS）
python main.py --case 中华保险_业务数据质量 \
  --var partition="dt = '2026-09-16'" --var bizdate=2026-09-16

# 华为南方工厂（Hive）
python main.py --case 华为南方工厂_制造数据质量 \
  --var partition="dt = '2026-09-16'" --var bizdate=2026-09-16

# 服务器定时任务（连接配置放服务器上）
python testcase/6e49032b-ea6e-4621-8dbc-bf4bfc3a832a.py --env /opt/conf/zhonghua.env \
  --var partition="dt = '2026-09-16'" --report json,md -o /data/dq/reports
```

连接信息在 `.env.example` 里已经写好两段（`DQ_CONN_ZHONGHUA_ODPS_*`、`DQ_CONN_HUAWEI_HIVE_*`），
复制成 `.env` 填上真实值即可；规则集里只引用连接名（`conn: zhonghua_odps` / `conn: huawei_hive`）。

## 怎么运行

```bash
pip install -r requirements.txt          # 只跑 csv 的话，pandas + PyYAML 就够
```

**方式一：PyCharm 里直接 Run（最常用）**

打开 `testcase/bec36bd4-5b66-474b-a163-1fa6c5d23b2f.py`（用例名是"用户数据质量"，见 yaml 里的 `name`），
直接点运行。它读同名的 `bec36bd4-….yaml` → 执行 → 报告写到 `reports/bec36bd4-….json / .md / .html`。
数据库连接自动读仓库根目录的 `.env`。

**方式二：改主文件里的变量**

```python
# main.py
RULE_SET = "用户数据质量"      # ← 改成你想跑的规则集名字（testcase/ 下 yaml 的文件名）
```

然后 Run `main.py`。

**方式三：命令行**

```bash
python main.py                                  # 跑 RULE_SET
python main.py --case 4c307f9c-2e9e-4d9d-9130-dd71b6c2da57   # 跑指定用例（uuid）
python main.py --case 用户数据质量_脏数据                      # 也认 yaml 里的 name
python main.py --all                            # 跑 testcase/ 下所有规则集
python main.py --list-cases                     # 有哪些规则集（以及有没有对应的 py）
python main.py --case bec36bd4 --list-rules      # 这个用例里有哪些规则
python main.py --case bec36bd4 --only 邮箱        # 只跑某几条规则（子串/通配符/逗号分隔）
python main.py --case bec36bd4 --var partition="dt = '2026-09-16'"
python main.py --case bec36bd4 --report console,md      # 只要控制台 + Markdown
python main.py --case bec36bd4 --report none            # 不输出，只看退出码（CI 用）
python main.py --case bec36bd4 --failed-rows -o reports  # 控制台打印失败样本
python main.py --env /opt/conf/prod.env                       # 用指定的连接配置文件
```

> `python main.py --all` 会把 `testcase/` 下所有用例跑一遍；引用数据库连接的用例需要先配好 `.env`
> （没配好会在跑之前报"连接缺失"，退出码 2）。

| 退出码 | 含义 |
| --- | --- |
| `0` | 全部通过（或只有 warn / info 级别的失败） |
| `1` | 有 `severity: error` 的规则失败或执行出错 |
| `2` | 规则文件或参数写错 |

## 怎么新增一个规则集

1. 复制一对文件并改名（**yaml 和 py 必须同名**，用新的 uuid）：

   ```bash
   cd testcase
   uuid=$(python -c "import uuid; print(uuid.uuid4())")
   cp bec36bd4-5b66-474b-a163-1fa6c5d23b2f.yaml "$uuid.yaml"
   cp bec36bd4-5b66-474b-a163-1fa6c5d23b2f.py   "$uuid.py"
   ```

2. 在 YAML 顶部写 `name: 订单数据质量`（人类可读），再改 `sources` 和 `checks`（见下节）；
   py 文件一般不用动，需要时改改顶部配置区。

   每个 testcase 的 py 长这样（**自己指定 YAML，自己调 common 的方法**，main.py 调它）：

   ```python
   import sys
   from pathlib import Path

   sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # 直接 Run 时也能 import common
   import common

   # ============================ 本用例的配置 ============================
   YAML = Path(__file__).with_suffix(".yaml")   # 规则集文件（也可以写死别的路径）
   ENV_FILE = None                              # 连接配置：None = 仓库根 .env；服务器写 /opt/conf/prod.env
   REPORT_FORMATS = ("json", "md", "html")      # 要生成哪几份报告
   OUTPUT_DIR = None                            # None = reports/
   EXTRA_VARS = {}                              # 例如 {"partition": "dt = '2026-09-16'"}
   ONLY = []                                    # 只跑某几条规则，例如 ["邮箱"]
   SHOW_FAILED_ROWS = False                     # 控制台是否打印失败样本


   def run(variables=None, formats=REPORT_FORMATS, output_dir=OUTPUT_DIR, show_console=True,
           failed_rows=SHOW_FAILED_ROWS, strict_fields=False, only=None, env_file=ENV_FILE):
       """执行本规则集：读 YAML → 跑规则 → 出报告，返回 report。"""
       config = common.load_rules(YAML, env_file=env_file, strict_fields=strict_fields)
       report = common.run(config, variables={**EXTRA_VARS, **(variables or {})}, only=only or ONLY)
       written = common.write_reports(report, output_dir or common.REPORT_DIR, formats,
                                      prefix=YAML.stem)      # 报告名 = yaml 文件名
       if show_console:
           print(common.render_console(report, failed_rows=failed_rows))
       for path in written:
           print(f"报告已写入：{path}")
       return report


   def main(argv=None):
       return common.case_entry(YAML, argv, run_func=run)


   if __name__ == "__main__":
       raise SystemExit(main())
   ```

   调用链是：`main.py`（或 PyCharm / 服务器）→ `testcase/xxx.py` → `common/` 里的公共方法。

3. 运行：

   ```bash
   python main.py --case "$uuid"
   # 或直接在 PyCharm 里 Run testcase/$uuid.py
   ```

   报告会写成 `reports/<uuid>.json / .md / .html`。

## 数据库连接怎么配（.env，支持多个）

连接信息（IP / 端口 / 账号 / 密码 / jar 路径）全部写在 `.env` 里，**规则集只写连接名**。
复制模板后改值即可（`.env` 已 gitignore，不会提交）：

```bash
cp .env.example .env
```

```ini
# 一个连接一段，名字随便起（这里是 dw_hive、doris_dw）
DQ_CONN_DW_HIVE_TYPE=hive
DQ_CONN_DW_HIVE_URL=jdbc:hive2://10.0.0.5:10000/dw      # 也可以分字段写
DQ_CONN_DW_HIVE_USER=etl
DQ_CONN_DW_HIVE_PASSWORD=xxx
DQ_CONN_DW_HIVE_DRIVER_PATH=/opt/jdbc/hive-jdbc.jar

DQ_CONN_DORIS_DW_TYPE=doris
DQ_CONN_DORIS_DW_HOST=10.0.0.6
DQ_CONN_DORIS_DW_PORT=9030
DQ_CONN_DORIS_DW_DATABASE=dw
DQ_CONN_DORIS_DW_USER=etl
DQ_CONN_DORIS_DW_PASSWORD=xxx
```

规则集里只写连接名，`type` 可以写在 YAML（更直观）也可以写在 .env：

```yaml
sources:
  hive_dw:                    # 用 .env 里的 dw_hive 连接
    type: hive
    conn: dw_hive
    table: dim_user
  hudi_dw:                    # 同一个连接可以给多个数据源用
    type: hudi
    conn: lake_trino
    schema: dw
    table: hudi_user_rt
```

要点：

- 字段名就是 source 的字段：`TYPE / BACKEND / URL / HOST / PORT / DATABASE / SCHEMA / CATALOG /
  PROJECT / SERVICE / USER / PASSWORD / DRIVER_CLASS / DRIVER_PATH / QUERY_ENGINE / DIALECT`
  （`DRIVER_PATH` 多个 jar 用路径分隔符）
- **优先级**：代码传入的 `conns` > 进程环境变量 > `.env` 文件
- 跑之前会打印一行 `[连接] 连接配置：/path/.env；可用连接：dw_hive, doris_dw`，
  方便确认服务器上用的是哪份配置；规则集引用了不存在的连接会直接报错并列出可用连接
- 没有集群时想联调：配一个 sqlite 连接就能跑通全流程
  ```ini
  DQ_CONN_LOCAL_DB_TYPE=sqlite
  DQ_CONN_LOCAL_DB_URL=sqlite:///data/users.db
  ```

## 提交到服务器定时运行

把一个规则集对应的 **yaml + py 两个文件**（必要时加 `common/`）放到服务器，然后：

```bash
# 方式一：指定服务器上的连接配置（推荐，一台机器多套环境）
python bec36bd4-5b66-474b-a163-1fa6c5d23b2f.py --env /opt/conf/prod.env

# 方式二：连接信息直接用环境变量传（调度器里配）
DQ_CONN_DW_HIVE_URL=jdbc:hive2://prod-hive:10000/dw \
DQ_CONN_DW_HIVE_USER=etl DQ_CONN_DW_HIVE_PASSWORD=xxx \
python bec36bd4-5b66-474b-a163-1fa6c5d23b2f.py --report json,md -o /data/dq/reports

```

```python
# 方式三：在调度脚本里以参数传入（导入那个 py，用 conns 直接传连接信息）
import importlib.util
spec = importlib.util.spec_from_file_location("case", "/opt/dq/testcase/bec36bd4-5b66-474b-a163-1fa6c5d23b2f.py")
case = importlib.util.module_from_spec(spec); spec.loader.exec_module(case)
report = case.run(
    conns={"dw_hive": {"type": "hive", "host": "prod-hive", "port": 10000,
                       "database": "dw", "user": "etl", "password": "xxx",
                       "driver_path": "/opt/jdbc/hive-jdbc.jar"}},
    formats=("json", "md"), output_dir="/data/dq/reports",
    show_console=False)
print(report["summary"]["status"], report["summary"]["exit_code"])
```

退出码同样是 `0 / 1 / 2`，调度器直接用 `|| 告警` 就能接监控。

## 规则怎么写（YAML）

```yaml
version: 1

vars:
  partition: "1=1"           # 分区条件，SQL 里用 ${partition}；内置变量还有 ${table}、${source}

sources:
  users:
    type: csv                # csv / parquet / sqlite / sqlalchemy / mysql / doris / oracle / postgresql
    path: data/users.csv     # hive / spark / trino / hudi / paimon / odps / jdbc
    read_options: { encoding: utf-8 }

checks:
  # ① SQL 断言：expected + operator
  - name: status 枚举合法
    source: users
    dim: validity            # 维度，报告里按维度汇总
    severity: high           # high = error；也可写 error / warn / info
    description: status 只允许 active / inactive / pending
    sql: |
      SELECT COUNT(*) FROM ${table}
      WHERE ${partition} AND status NOT IN ('active','inactive','pending')
    expected: 0              # 也可写 {cnt: 0} 逐列比较；支持 "${min_rows}" 这种带变量的
    operator: eq             # eq/ne/gt/gte/lt/lte/between/in/not_in

  # ② SQL 明细：SQL 返回的行就是失败明细（自带最多 10 行样本）
  - name: 邮箱格式非法明细
    source: users
    dim: validity
    severity: high
    sql: SELECT id, email FROM ${table} WHERE email NOT GLOB '*@*.*'
    mode: rows
    threshold: 0.01          # 允许 1% 的失败率

  # ③ 快捷方式：不写 SQL，自动生成
  - { name: 用户 id 非空, source: users, dim: completeness, severity: high,
      type: not_null, column: id }
  - { name: 年龄区间, source: users, dim: validity, severity: medium,
      type: range, column: age, min: 0, max: 120 }
  - { name: 行数下限, source: users, dim: completeness, severity: medium,
      type: row_count, min: 100 }
```

要点：

- **断言就是 `expected` + `operator`**；`actual` / `result` / `message` 由工具回填到报告（`result` 取值 `passed/failed/error`）。
  老规则文件里带着 `actual` / `result` 也能直接跑（会提示一行），只写 `actual` 没写 `expected` 会报错，`--strict-fields` 可强制报错。
- SQL 默认直接写在规则里（一个规则集一个文件，最省事）；几百行的长 SQL 想复用可以拆到
  `sql/xxx.sql` 用 `sql_file: sql/xxx.sql` 引用，需要时再建这个目录即可。
- `filter: "..."` 是额外的 WHERE 条件（支持 `${变量}`）。
- 变量优先级：内置(`source`/`table`/`partition`) < 顶层 `vars` < 数据源 `vars` < 检查项 `vars` < 环境变量 `DQ_VAR_<NAME>` < `--var`。
- 变量没定义、字段写错、列名写错，都会在**运行前**报错并说明怎么改。

## 数据源

| type | 说明 |
| --- | --- |
| `csv` / `parquet` | 本地文件；会注册成内存表，所以也能写 SQL（SQLite 语法） |
| `sqlite` | `path: data/x.db`（只读）或 `url: sqlite:///...` |
| `sqlalchemy` | 通用数据库：`url_env` / `url` / `dialect`+`host`+`database` |
| `mysql` `doris` `postgresql` `oracle` | 关系库；mysql/doris/pg 默认纯 Python 驱动，oracle 默认 JDBC |
| `hive` `spark` `trino` `hudi` `paimon` | 大数据，走 JDBC；Hudi/Paimon 默认经 Trino 查（`query_engine` 可切） |
| `odps` | 阿里云 MaxCompute；JDBC 或 `backend: pyodps` |
| `jdbc` | 自定义：`driver_class` + `url` |

密码只从环境变量读（`username_env` / `password_env`），JDBC jar 用 `driver_path_env`。
完整写法见 `testcase/大数据连接示例.yaml`，连接清单用 `python main.py --list-connectors` 看。

## 报告

三份文件同名不同后缀，放在 `reports/`：

- `reports/用户数据质量.json`：`summary` + `results`，每条含 `name/source/dim/severity/status/result/actual/expected/operator/message/failed_rows/duration_ms`
- `reports/用户数据质量.md`：Markdown（汇总 → 按维度 → 检查明细 → 失败明细），适合贴群/PR/归档
- `reports/用户数据质量.html`：单文件页面，失败样本可展开
- 控制台：一行汇总 + 按维度 + 明细表格

## common/ 里的公共方法

| 文件 | 提供什么 |
| --- | --- |
| `common/case.py` | `run_case(名字或路径)`、`list_cases()`、`case_yaml/case_python()` —— 规则集入口 |
| | 还有 `load_rules()`（加载 .env + 校验规则）、`case_entry()`（testcase py 的命令行入口） |
| `common/config.py` | `load_config(yaml)` —— 读规则并校验 |
| `common/db.py` | `load_frame()` / `open_conn()` / `query()` / `frame_conn()` —— 数据源与连接 |
| `common/env.py` | `configure()` / `get_conn()` / `available()` —— 读 .env、组装多个数据库连接 |
| `common/checks.py` | `run_check()` / `compare()` / `build_sql()` —— 规则执行与断言 |
| `common/runner.py` | `run(config)` / `select_checks()` —— 跑规则集并汇总 |
| `common/report.py` | `render_console()` / `write_reports()` / `to_markdown()` / `to_html()` |

在自己的脚本里复用：

```python
import common

report = common.run_case("用户数据质量", formats=("json", "md"))   # 等价于 Run 那个 py 文件
print(report["summary"]["status"], report["summary"]["exit_code"])

# 只跑一条规则、并且代码里拿结果
config = common.load_config(common.case_yaml("用户数据质量"))   # 也支持传 uuid
one = common.run(config, only="邮箱")
print(one["results"][0])
```

## 测试

```bash
pytest        # 49 passed（覆盖断言、明细模式、变量、快捷方式、连接、.env、报告、命令行、
              #            以及两套业务规则集的配置与 SQL 语法自检）
```

`tests/` 跟运行完全无关：删掉它工具照样跑，只是改了 `common/` 之后少一层保险。
不想要就直接 `rm -rf tests/`。
