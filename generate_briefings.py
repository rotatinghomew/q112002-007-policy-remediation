"""生成各期简报包与两期差异（不接真实邮箱，只落本地文件）。

用法：python3 -I generate_briefings.py [资料目录] [输出目录]
标准库实现；读取入口沿用 record_reader.read_records 的字段契约校验。
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from briefing import (  # noqa: E402
    applicability_series,
    build_dispatch,
    diff_editions,
    evaluate_claims,
    indicator_rates,
    resolve_applicability,
    supervisor_packet,
)
from briefing.compliance import index_tasks, owner_at  # noqa: E402
from record_reader import read_records  # noqa: E402


def _routing(tables, dispatch):
    """按 recipients 登记给出分发路由；未登记的新任课老师标记为待补登记。"""
    recipients = tables.get("recipients", [])
    known = {row["recipient"] for row in recipients}
    routes = []
    for teacher, items in sorted(dispatch["teacher_lists"].items()):
        routes.append(
            {
                "recipient": teacher,
                "role": "教师",
                "routable": teacher in known,
                "item_count": len(items),
                "note": "" if teacher in known else "任课变动后到，recipients 尚未登记，需补录",
            }
        )
    return routes


def build_packets(tables):
    tasks = index_tasks(tables)
    packets = {
        "applicability_series": {
            task_id: applicability_series(tables, task_row)
            for task_id, task_row in sorted(tasks.items())
        },
        "claims": [
            verdict.__dict__
            for verdict in evaluate_claims(
                tables, max(row["send_day"] for row in tables["editions"])
            )
        ],
        "editions": {},
    }

    for edition in tables["editions"]:
        as_of = edition["send_day"]
        packet = {
            "edition": edition,
            "supervisor": supervisor_packet(tables, as_of),
            "dispatch": build_dispatch(tables, edition),
            "routing": _routing(tables, build_dispatch(tables, edition)),
        }
        packets["editions"][edition["issue"]] = packet

    send_days = sorted(row["send_day"] for row in tables["editions"])
    packets["diffs"] = [
        diff_editions(tables, send_days[i - 1], send_days[i])
        for i in range(1, len(send_days))
    ]
    return packets


def _load_side_tables(folder, tables):
    """读取未列入 catalog 的旁路表（收件路由、历史发布留痕）。

    这些表不参与 record_reader 的字段契约；缺失时降级为空表，不影响主计算。
    """
    for name in ("recipients", "released"):
        path = Path(folder) / f"{name}.json"
        tables[name] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    return tables


def main():
    folder = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("records")
    out_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("out")
    tables = _load_side_tables(folder, read_records(folder))
    packets = build_packets(tables)
    out_dir.mkdir(parents=True, exist_ok=True)

    payload = json.dumps(packets, ensure_ascii=False, indent=2, default=str)
    (out_dir / "briefing_packets.json").write_text(payload, encoding="utf-8")
    _write_console_summary(tables, packets, out_dir)
    print(f"已写出 {out_dir / 'briefing_packets.json'}")


def _write_console_summary(tables, packets, out_dir):
    lines = ["# 简报执行摘要（本地生成，不发送邮件）", ""]
    for issue, packet in packets["editions"].items():
        edition = packet["edition"]
        lines.append(f"## {issue}（备好 {edition['prepared']} / 发出 {edition['send_day']}，"
                     f"状态 {edition['state']}）")
        lines.append("### 督导：可比口径执行率")
        for row in packet["supervisor"]["rates"]:
            lines.append(
                f"- {row['policy']} {row['grade']}年级{row['category']}·{row['indicator']}："
                f"{row['numerator']}/{row['denominator']} = {row['rate']}"
            )
        remedy = packet["supervisor"]["remediation"]
        lines.append(f"- 补救完成率（另算）：{remedy['numerator']}/{remedy['denominator']}"
                     f" = {remedy['rate']}")
        if packet["supervisor"]["uncertain"]:
            lines.append("- 冲突待发文单位确认（不计教师失误、不算达标）：")
            for row in packet["supervisor"]["uncertain"]:
                lines.append(f"  - {row['task']}/{row['student']}：{row['note']}")
        if packet["supervisor"]["pending_observation"]:
            lines.append("- 未到观察点（不进分母）："
                         + ", ".join(f"{r['task']}/{r['student']}"
                                      for r in packet["supervisor"]["pending_observation"]))
        lines.append("### 教师本周待办（按现任任课老师）")
        for teacher, items in sorted(packet["dispatch"]["teacher_lists"].items()):
            lines.append(f"- {teacher}：{len(items)} 项")
            for item in items:
                tag = "（补救认领竞争待裁定）" if item["contested_work"] else ""
                lines.append(f"  - {item['task']}/{item['student']} 缺 {item['step']}{tag}")
        for notice in packet["dispatch"]["withdrawals"]:
            item = notice["item"]
            lines.append(
                f"- 撤回通知（原收件 {notice['previous_teacher']}，"
                f"现任 {notice['current_owner']}）："
                f"{item['task']}/{item['student']} 因规范改判不再要求 {item['step']}，"
                f"已做记录保留不抹除"
            )
        for notice in packet["dispatch"]["resolution_notices"]:
            item = notice["item"]
            lines.append(
                f"- 结案通知（原收件 {notice['previous_teacher']}，"
                f"现任 {notice['current_owner']}）："
                f"{item['task']}/{item['student']} 经 {item['resolution']['claim']} 补救解除"
            )
        for notice in packet["dispatch"]["owner_change_notices"]:
            item = notice["item"]
            lines.append(
                f"- 接办通知 -> {item['owner']}（原 {notice['previous_teacher']}）："
                f"{item['task']}/{item['student']} 仍缺 {item['step']}"
            )
        if packet["dispatch"]["prep_to_send_changes"]:
            lines.append("### 备好→发出之间的事实变化")
            for change in packet["dispatch"]["prep_to_send_changes"]:
                lines.append(f"- {change['key']}：{change['change']} {change['detail'] or ''}")
        lines.append("")

    lines.append("## 两期差异依据")
    for diff in packets["diffs"]:
        lines.append(f"### 第 {diff['left_day']} 日 -> 第 {diff['right_day']} 日")
        for change in diff["rate_changes"]:
            lines.append(f"- 指标 {change['cohort']}：{change['before']} -> {change['after']}"
                         f"（依据：{change['basis']}）")
        for change in diff["item_changes"]:
            lines.append(f"- 待办 {change['key']}：{change['before']} -> {change['after']}"
                         f"（依据：{change['basis']}）")
        lines.append("")

    (out_dir / "summary.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
