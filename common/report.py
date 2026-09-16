"""报告输出：控制台 + result.json / result.md / result.html。"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any, Iterable, Sequence

_BADGE = {"PASS": "PASS", "FAIL": "FAIL", "ERROR": "ERROR"}


# --------------------------------------------------------------------------- #
# 控制台
# --------------------------------------------------------------------------- #
def render_console(report: dict, failed_rows: bool = False, color: bool | None = None) -> str:
    """控制台报告（纯文本，CI 日志里也能看）。"""
    summary = report["summary"]
    lines = [
        "=" * 78,
        _title_line(report),
        f"时间：{report['time']}    总耗时：{report['duration_ms']:.1f} ms",
        "=" * 78,
        f"合计 {summary['total']} 项 | 通过 {summary['passed']} | 失败 {summary['failed']} "
        f"| 错误 {summary['errors']} | 门禁：{summary['status']}（exit {summary['exit_code']}）",
    ]
    if summary["by_dim"]:
        detail = "；".join(f"{dim} {item['passed']}/{item['total']} 通过"
                          for dim, item in sorted(summary["by_dim"].items()))
        lines.append(f"按维度：{detail}")
    if summary["blocking"]:
        lines.append(f"触发门禁的规则：{', '.join(summary['blocking'])}")

    headers = ["检查项", "数据源", "维度", "类型", "级别", "状态", "实际", "期望", "说明", "耗时ms"]
    rows = [[
        item["name"], item["source"], item["dim"], item["type"], item["severity"], item["status"],
        _short(item["actual"], 26), _short(item["expected"], 26), _short(item["message"], 46),
        f"{item['duration_ms']:.1f}",
    ] for item in report["results"]]
    lines.append(_table(headers, rows))

    if failed_rows:
        lines.append("")
        lines.append("-" * 78)
        lines.append("失败明细（每条规则最多 10 行）")
        lines.append("-" * 78)
        lines.append(render_failed_rows(report))
    return "\n".join(lines)


def render_failed_rows(report: dict) -> str:
    """失败规则的样本行。"""
    blocks = []
    for item in report["results"]:
        if item["status"] == "PASS":
            continue
        block = [f"[{_BADGE[item['status']]}] {item['name']}"
                 f"（{item['source']} / {item['type']} / {item['severity']}）",
                 f"    {item['message']}"]
        if item["failed_rows"]:
            columns = list(item["failed_rows"][0].keys())
            rows = [[_short(row.get(column), 30) for column in columns] for row in item["failed_rows"]]
            block.append(_table(columns, rows))
        blocks.append("\n".join(block))
    return "\n\n".join(blocks) if blocks else "所有规则都通过了。"


def _short(value: Any, limit: int) -> str:
    if value is None:
        return "-"
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _title_line(report: dict) -> str:
    """报告头：用例名（写在 yaml 的 name 里）+ 规则文件名（uuid）。"""
    name = report.get("name") or ""
    config = report.get("config") or ""
    if name:
        return f"数据质量报告    用例：{name}    规则文件：{Path(config).name if config else '-'}"
    return f"数据质量报告    规则文件：{config}"


def _table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    """优先用 tabulate，没装就自己画。"""
    if not rows:
        return "（无数据）"
    try:
        from tabulate import tabulate
    except ImportError:
        pass
    else:
        return tabulate(list(rows), headers=list(headers), tablefmt="github")
    widths = [len(str(head)) for head in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(str(cell)))
    line = lambda cells: "| " + " | ".join(str(c).ljust(widths[i]) for i, c in enumerate(cells)) + " |"
    sep = "+" + "+".join("-" * (width + 2) for width in widths) + "+"
    return "\n".join([sep, line(headers), sep] + [line(row) for row in rows] + [sep])


def table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    """公开的表格渲染（命令行里列清单也用它）。"""
    return _table(headers, rows)


# --------------------------------------------------------------------------- #
# 三种报告文件
# --------------------------------------------------------------------------- #
def write_reports(report: dict, output_dir: Any = "reports",
                  formats: Iterable[str] = ("json", "md", "html"),
                  prefix: str = "result") -> list:
    """写报告文件，文件名用 ``<prefix>.json/.md/.html``，返回写出的路径。"""
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    wanted = {str(item).strip().lower() for item in formats}
    written = []
    if "json" in wanted or "all" in wanted:
        written.append(_write(directory / f"{prefix}.json", json.dumps(report, ensure_ascii=False, indent=2)))
    if "md" in wanted or "all" in wanted:
        written.append(_write(directory / f"{prefix}.md", to_markdown(report)))
    if "html" in wanted or "all" in wanted:
        written.append(_write(directory / f"{prefix}.html", to_html(report)))
    return written


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def to_markdown(report: dict) -> str:
    """Markdown 报告：适合贴到 PR / 群里，也方便归档。"""
    summary = report["summary"]
    lines = [
        "# 数据质量报告",
        "",
        f"- 用例：{report.get('name') or '（未命名）'}（规则文件 `{Path(report['config']).name}`）",
        f"- 配置：`{report['config']}`",
        f"- 时间：{report['time']}（耗时 {report['duration_ms']:.1f} ms）",
        f"- 门禁结果：**{summary['status']}**（exit code {summary['exit_code']}）",
        f"- 合计：{summary['total']} 项（通过 {summary['passed']} / 失败 {summary['failed']} "
        f"/ 错误 {summary['errors']}）",
    ]
    if summary["blocking"]:
        lines.append(f"- 触发门禁的规则：{', '.join(summary['blocking'])}")
    if summary["by_dim"]:
        lines += ["", "## 按维度", "", "| 维度 | 通过 | 失败 | 错误 | 总数 |", "| --- | --- | --- | --- | --- |"]
        for dim, item in sorted(summary["by_dim"].items()):
            lines.append(f"| {dim} | {item['passed']} | {item['failed']} | {item['errors']} | {item['total']} |")
    lines += ["", "## 检查明细", "",
              "| 检查项 | 数据源 | 维度 | 类型 | 级别 | 状态 | 实际 | 期望 | 说明 | 耗时(ms) |",
              "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for item in report["results"]:
        lines.append("| {name} | {source} | {dim} | {type} | {severity} | {status} | {actual} | "
                     "{expected} | {message} | {duration_ms} |".format(
                         **{**item, "actual": _md(item["actual"]), "expected": _md(item["expected"]),
                            "message": _md(item["message"])}))
    failures = [item for item in report["results"] if item["status"] != "PASS"]
    if failures:
        lines += ["", "## 失败明细", ""]
        for item in failures:
            lines += [f"### {item['name']}（{item['source']} / {item['type']} / {item['severity']}）", "",
                      f"{item['message']}", ""]
            if item["failed_rows"]:
                columns = list(item["failed_rows"][0].keys())
                lines += ["| " + " | ".join(columns) + " |",
                          "| " + " | ".join("---" for _ in columns) + " |"]
                for row in item["failed_rows"]:
                    lines.append("| " + " | ".join(_md(row.get(column)) for column in columns) + " |")
                lines.append("")
    return "\n".join(lines) + "\n"


def _md(value: Any) -> str:
    text = _short(value, 200)
    return text.replace("|", "\\|")


def to_html(report: dict) -> str:
    """HTML 报告：单文件，双击就能看。"""
    summary = report["summary"]
    rows = []
    for item in report["results"]:
        detail = html.escape(_short(item["message"], 200))
        if item["failed_rows"]:
            sample = html.escape(json.dumps(item["failed_rows"], ensure_ascii=False, indent=2))
            detail += f"<details><summary>失败样本（{len(item['failed_rows'])} 行）</summary><pre>{sample}</pre></details>"
        rows.append("<tr>" + "".join(
            f"<td>{html.escape(str(cell))}</td>" for cell in (
                item["name"], item["source"], item["dim"], item["type"], item["severity"],
            )
        ) + f'<td class="{item["status"]}">{item["status"]}</td>'
          + f"<td>{html.escape(_short(item['actual'], 60))}</td>"
          + f"<td>{html.escape(_short(item['expected'], 60))}</td>"
          + f"<td>{detail}</td>"
          + f"<td>{item['duration_ms']:.1f}</td></tr>")
    dim_rows = "".join(
        f"<tr><td>{html.escape(dim)}</td><td>{item['passed']}</td><td>{item['failed']}</td>"
        f"<td>{item['errors']}</td><td>{item['total']}</td></tr>"
        for dim, item in sorted(summary["by_dim"].items())
    )
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>数据质量报告</title>
<style>
 body {{ font-family: -apple-system, "PingFang SC", Arial, sans-serif; margin: 28px; color: #222; }}
 h1 {{ font-size: 20px; }} table {{ border-collapse: collapse; width: 100%; font-size: 13px; margin: 10px 0 20px; }}
 th, td {{ border: 1px solid #d0d7de; padding: 5px 8px; text-align: left; vertical-align: top; }}
 th {{ background: #f6f8fa; }} .PASS {{ color: #1a7f37; font-weight: 600; }}
 .FAIL {{ color: #cf222e; font-weight: 600; }} .ERROR {{ color: #9a6700; font-weight: 600; }}
 pre {{ background: #f6f8fa; padding: 8px; overflow-x: auto; }}
 .meta {{ color: #555; font-size: 13px; }}
</style></head><body>
<h1>数据质量报告</h1>
<p class="meta">用例：{html.escape(report.get('name') or '（未命名）')} ｜
规则文件：{html.escape(Path(report['config']).name)} ｜ 时间：{report['time']} ｜
耗时 {report['duration_ms']:.1f} ms ｜ 门禁：<span class="{summary['status']}">{summary['status']}</span>
（exit {summary['exit_code']}）<br>
合计 {summary['total']} 项：通过 {summary['passed']} / 失败 {summary['failed']} / 错误 {summary['errors']}</p>
<h2>按维度</h2>
<table><thead><tr><th>维度</th><th>通过</th><th>失败</th><th>错误</th><th>总数</th></tr></thead>
<tbody>{dim_rows}</tbody></table>
<h2>检查明细</h2>
<table><thead><tr><th>检查项</th><th>数据源</th><th>维度</th><th>类型</th><th>级别</th><th>状态</th>
<th>实际</th><th>期望</th><th>说明</th><th>耗时(ms)</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table>
</body></html>
"""
