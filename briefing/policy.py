"""规范适用与批准文件裁定。

口径要点（见 docs/spec.md）：
- 普通在途任务按首次开放日适用当时有效的规范；重开不改变适用规范，只延长观察期。
- 例外文件有两个时间：approval_day 是文件写明时间，received 是督导实际收到时间。
  在 as_of 当日能据以执行的，只有 received <= as_of 且未被（同样已收到的）撤销文件
  取消的文件。迟到的批准按"规范改判"处理，不追溯改判教师前期的执行结论。
- 同一任务存在互相冲突、均有效且无撤销/裁定的批准文件时，状态为 CONFLICT：
  既不能把缺失记成教师失误，也不能把班级伪装成全数达标，该任务整体退出确定口径，
  待办仍照常列出并挂"待发文单位确认"。
"""

from dataclasses import dataclass, field


CONFLICT = "conflict"          # 互相冲突的批准文件，等待发文单位确认
DETERMINED = "determined"      # 适用要求已确定


def _base_policy(tables, task_row):
    """按任务首次开放日选择当时有效的规范（年级 + 任务类别）。"""
    candidates = [
        row
        for row in tables["policies"]
        if row["grade"] == task_row["grade"]
        and row["category"] == task_row["category"]
        and row["effective"] <= task_row["opened"]
    ]
    if not candidates:
        raise ValueError(f"任务 {task_row['task']} 在开放日没有适用规范")
    chosen = max(candidates, key=lambda row: row["effective"])
    return chosen


def _active_documents(tables, task_id, as_of):
    """as_of 当日已收到且未被撤销的批准文件。

    撤销文件本身也必须已经收到才生效；被取消的文件无论到达时间一律剔除。
    policy 为空的文件只承担撤销作用（如 e3）。
    """
    rows = [
        row
        for row in tables["exceptions"]
        if row["task"] == task_id and row["received"] is not None
        and row["received"] <= as_of
    ]
    cancelled = {row["cancels"] for row in rows if row["cancels"]}
    return [row for row in rows if row["document"] not in cancelled]


@dataclass
class Applicability:
    task: str
    state: str
    policy: object                       # 确定时为 policies 行；冲突时为 None
    required: list = field(default_factory=list)
    documents: list = field(default_factory=list)   # 参与判断的有效文件
    note: str = ""


def resolve_applicability(tables, task_row, as_of):
    """返回任务在 as_of 业务日的适用结论。"""
    task_id = task_row["task"]
    base = _base_policy(tables, task_row)
    docs = _active_documents(tables, task_id, as_of)
    approving = [row for row in docs if row["policy"]]

    if not approving:
        return Applicability(task_id, DETERMINED, base, list(base["required"]), docs)

    policies = {row["policy"] for row in approving}
    if len(policies) == 1:
        policy_id = next(iter(policies))
        chosen = next(row for row in tables["policies"] if row["policy"] == policy_id)
        return Applicability(task_id, DETERMINED, chosen, list(chosen["required"]), docs)

    return Applicability(
        task_id,
        CONFLICT,
        None,
        [],
        docs,
        note="批准文件互相冲突且无撤销/裁定，需发文单位确认："
        + ",".join(sorted(policies)),
    )


def applicability_series(tables, task_row):
    """返回任务适用状态随业务日变化的区间序列，供两期差异标注"规范改判"。

    元素：(start_day, end_day_or_None, state, policy_id_or_None, note)
    把收到/撤销事件日作为切换点；切换日当天按新事实处理（文件 received 当日即有效）。
    """
    task_id = task_row["task"]
    event_days = sorted(
        {
            row["received"]
            for row in tables["exceptions"]
            if row["task"] == task_id and row["received"] is not None
        }
        | {task_row["opened"]}
    )
    series = []
    for index, day in enumerate(event_days):
        end = event_days[index + 1] - 1 if index + 1 < len(event_days) else None
        verdict = resolve_applicability(tables, task_row, day)
        series.append(
            (
                day,
                end,
                verdict.state,
                verdict.policy["policy"] if verdict.policy else None,
                verdict.note,
            )
        )
    return series
