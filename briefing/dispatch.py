"""周一简报排程：督导口径比较、教师待办名单与两期差异。

排程规则：
- editions 中 prepared 是排程备好日、send_day 是实际分发日；名单已备好但未发出时，
  若发生补交、任课变动或认领裁定，当次分发按 send_day 的新事实重新核定，
  并同时保留"备好口径 vs 发出口径"的变化依据。
- 已发出待办的责任/状态变化要生成撤回或变更通知；新任课老师只接办仍开放的事项，
  已结案的补救不重复领取。
- 两期差异必须把变化拆成三类依据：任务构成（新到观察点/重开）、规范改判
  （批准文件到达、撤销、裁定）、教学行为与补救实际变化（步骤补传、补救认领）。
"""

from .compliance import (
    UNCERTAIN,
    evaluate_all,
    index_tasks,
    indicator_rates,
    owner_at,
)
from .policy import resolve_applicability
from .remediation import (
    OPEN,
    RESOLVED_MAKEUP,
    WITHDRAWN_POLICY,
    evaluate_claims,
    followup_ledger,
    remediation_rate,
)

COMPOSITION = "任务构成"
POLICY_CHANGE = "规范改判"
BEHAVIOR = "教学行为与补救"


def _teacher_view(tables, as_of):
    """按 as_of 任课老师归集本周待办；含撤回/变更通知素材。"""
    ledger, claims = followup_ledger(tables, as_of)
    # 竞争待裁定的认领精确到 (任务, 补做活动)，再借 work 找到当事学生
    work_students = {row["work"]: row["student"] for row in tables["work"]}
    contested_pairs = {
        (verdict.task, work_students[verdict.work])
        for verdict in claims
        if verdict.state == "contested"
    }
    open_items, notices = [], []
    for entry in ledger:
        item = {
            "task": entry["task"],
            "student": entry["student"],
            "step": entry["step"],
            "owner": entry["owner"],
            "state": entry["state"],
            "resolution": entry["resolution"],
        }
        if entry["state"] == OPEN:
            item["contested_work"] = sorted(
                work_id
                for verdict in claims
                if verdict.state == "contested"
                and verdict.task == entry["task"]
                and work_students[verdict.work] == entry["student"]
                for work_id in [verdict.work]
            )
            open_items.append(item)
        else:
            notices.append(item)

    # 冲突任务的缺失项不在确定底账内，但必须以"待确认"身份发给任课老师，
    # 既不记教师失误，也不伪装达标。
    from .compliance import evaluate_all
    pairs, _, tasks = evaluate_all(tables, as_of)
    existing = {(item["task"], item["student"]) for item in open_items}
    for status in pairs:
        if status.state != UNCERTAIN or (status.task, status.student) in existing:
            continue
        open_items.append(
            {
                "task": status.task,
                "student": status.student,
                "step": "待确认（批准文件冲突）",
                "owner": owner_at(tables, status.task, as_of),
                "state": OPEN,
                "resolution": None,
                "contested_work": [],
                "note": status.note,
            }
        )
    return open_items, notices


def _prior_delivery_index(tables):
    """已发出名单索引：(task, student) -> 最近一次发送记录。"""
    index = {}
    for row in sorted(tables["deliveries"], key=lambda item: item["sent"]):
        index[(row["task"], row["student"])] = row
    return index


def _full_view(tables, as_of):
    """完整底账视图：确定待办 + 冲突待确认 + 已结案/撤回项，统一按 key 索引。"""
    open_items, notices = _teacher_view(tables, as_of)
    view = {}
    for item in open_items:
        view[(item["task"], item["student"], item["step"])] = item
    for item in notices:
        view[(item["task"], item["student"], item["step"])] = item
    return view


def _resolution_inside_window(tables, item, prepared_day, send_day):
    """结案/撤回结论是否在本期窗口 (prepared_day, send_day] 内首次成立。"""
    resolution = item.get("resolution") or {}
    if resolution.get("type") == "makeup":
        claim = next(
            row for row in tables["claims"] if row["claim"] == resolution["claim"]
        )
        # 竞争认领以裁定日为结论成立日；普通认领以认领日为准
        ruling = next(
            (row for row in tables["rulings"] if row["granted"] == claim["claim"]),
            None,
        )
        day = ruling["day"] if ruling else claim["day"]
    elif resolution.get("type") == "policy_change":
        # 撤回依据文件的实际收到日即结论成立日
        docs = {
            row["document"]: row for row in tables["exceptions"]
            if row["task"] == item["task"]
        }
        day = max(docs[doc]["received"] for doc in resolution["documents"])
    else:
        day = send_day
    return prepared_day < day <= send_day


def build_dispatch(tables, edition_row):
    """为某一期简报生成分发包：以 send_day 事实核定，附备好日对照。"""
    send_day = edition_row["send_day"]
    prepared_day = edition_row["prepared"]

    open_at_send, notices_at_send = _teacher_view(tables, send_day)
    prep_view = _full_view(tables, prepared_day)
    send_view = _full_view(tables, send_day)
    prep_keys = set(prep_view)
    send_keys = {
        (item["task"], item["student"], item["step"]) for item in open_at_send
    }

    prep_to_send = []
    all_keys = sorted(set(prep_view) | set(send_view))
    for key in all_keys:
        before, after = prep_view.get(key), send_view.get(key)
        if before is not None and after is None:
            prep_to_send.append({"key": key, "change": "退出底账", "detail": None})
            continue
        if before is None and after is not None:
            prep_to_send.append(
                {"key": key,
                 "change": "新增待办" if key in send_keys else "新增结案记录",
                 "detail": after.get("resolution")}
            )
            continue
        if before["state"] != after["state"]:
            prep_to_send.append(
                {"key": key,
                 "change": f"{before['state']} -> {after['state']}",
                 "detail": after.get("resolution")}
            )
        if before["owner"] != after["owner"]:
            prep_to_send.append(
                {"key": key, "change": "任课变动",
                 "detail": {"from": before["owner"], "to": after["owner"]}}
            )

    prior = _prior_delivery_index(tables)
    withdrawals, changes, new_assignments = [], [], []
    for item in notices_at_send:
        old = prior.get((item["task"], item["student"]))
        record = {
            "item": item,
            "previous_teacher": old["teacher"] if old else "(本期前未发送)",
            "issue": old["issue"] if old else None,
            "current_owner": item["owner"],
        }
        # 结案/撤回只在"结论首次成立"的那一期通知；更早期次已成立的不重复发。
        if not _resolution_inside_window(tables, item, prepared_day, send_day):
            continue
        if item["state"] == WITHDRAWN_POLICY:
            # 规范改判撤回：原收件老师拿撤回通知；若任课已变动，新老师同步状态，
            # 但该补救/待办已结案，不进新老师的待办名单。
            withdrawals.append(record)
        elif item["state"] == RESOLVED_MAKEUP:
            changes.append(record)

    for item in open_at_send:
        old = prior.get((item["task"], item["student"]))
        if old is not None and old["teacher"] != item["owner"]:
            new_assignments.append(
                {"item": item, "previous_teacher": old["teacher"],
                 "issue": old["issue"]}
            )

    by_owner = {}
    for item in open_at_send:
        by_owner.setdefault(item["owner"], []).append(item)

    return {
        "issue": edition_row["issue"],
        "state": edition_row["state"],
        "prepared_day": prepared_day,
        "send_day": send_day,
        "teacher_lists": by_owner,
        "withdrawals": withdrawals,          # 给原收件老师的撤回通知
        "resolution_notices": changes,       # 补救结案通知
        "owner_change_notices": new_assignments,  # 新老师接办（不含已结案）
        "prep_to_send_changes": prep_to_send,
    }


def _rate_basis(tables, key, before, after, left_day, right_day):
    """同一口径指标变化的主依据。

    分母变化只可能来自观察期新结束（任务构成）；分子变化来自步骤实际发生
    （教学行为）。规范改判表现为旧口径缩小/新口径出现，不在同口径行内发生。
    """
    if before["denominator"] != after["denominator"]:
        return COMPOSITION
    if before["numerator"] != after["numerator"]:
        return BEHAVIOR
    return "无变化"


def _item_basis(tables, key, before_state, after_state, left_day, right_day):
    """待办条目变化的依据分类。"""
    task_id, student, step = key
    tasks = index_tasks(tables)
    task_row = tasks[task_id]

    # 撤回结案只可能由规范文件到达/撤销引起
    if after_state == WITHDRAWN_POLICY or before_state == WITHDRAWN_POLICY:
        return POLICY_CHANGE

    left_app = resolve_applicability(tables, task_row, left_day)
    right_app = resolve_applicability(tables, task_row, right_day)
    policy_moved = (
        (left_app.policy or {}).get("policy")
        != (right_app.policy or {}).get("policy")
    ) or {doc["document"] for doc in left_app.documents} != {
        doc["document"] for doc in right_app.documents
    }

    if before_state != after_state:
        # 状态本身变化：规范文件两期间有动作才算规范改判；
        # 冲突解除后补判出的待办、补救结案等，按实际驱动因素归类。
        if policy_moved and after_state != RESOLVED_MAKEUP:
            return POLICY_CHANGE
        from .compliance import observation_window
        if observation_window(task_row)[1] > left_day:
            return COMPOSITION
        return BEHAVIOR

    # 状态未变：只剩任课变动等教学组织事实
    return BEHAVIOR


def diff_editions(tables, left_day, right_day):
    """两期简报差异：指标、待办底账各自给出变化量与依据分类。"""
    left_rates = indicator_rates(tables, left_day)
    right_rates = indicator_rates(tables, right_day)
    rate_index = {(row["policy"], row["grade"], row["category"], row["indicator"]): row
                  for row in left_rates}

    rate_changes = []
    for row in right_rates:
        key = (row["policy"], row["grade"], row["category"], row["indicator"])
        before = rate_index.get(key)
        if before is None:
            basis = COMPOSITION
        else:
            basis = _rate_basis(tables, key, before, row, left_day, right_day)
        rate_changes.append(
            {
                "cohort": key,
                "before": None if before is None else
                {"numerator": before["numerator"], "denominator": before["denominator"]},
                "after": {"numerator": row["numerator"],
                          "denominator": row["denominator"]},
                "basis": basis,
            }
        )
    # 右期消失的口径行（被规范改判搬走的旧口径）也要列出
    right_keys = {
        (row["policy"], row["grade"], row["category"], row["indicator"])
        for row in right_rates
    }
    for key, before in sorted(rate_index.items()):
        if key not in right_keys:
            rate_changes.append(
                {
                    "cohort": key,
                    "before": {"numerator": before["numerator"],
                               "denominator": before["denominator"]},
                    "after": None,
                    "basis": POLICY_CHANGE,
                }
            )

    left_ledger, _ = followup_ledger(tables, left_day)
    right_ledger, _ = followup_ledger(tables, right_day)
    left_keys = {
        (row["task"], row["student"], row["step"]): row for row in left_ledger
    }
    right_keys = {
        (row["task"], row["student"], row["step"]): row for row in right_ledger
    }

    # 冲突待确认不进确定底账，但两期间冲突解除必须作为"规范改判"显式入账
    left_pairs, _, _ = evaluate_all(tables, left_day)
    right_pairs, _, _ = evaluate_all(tables, right_day)
    left_uncertain = {
        (status.task, status.student)
        for status in left_pairs if status.state == UNCERTAIN
    }
    right_uncertain = {
        (status.task, status.student)
        for status in right_pairs if status.state == UNCERTAIN
    }

    item_changes = []
    for pair in sorted(left_uncertain - right_uncertain):
        task_id, student = pair
        for key, entry in sorted(right_keys.items()):
            if (key[0], key[1]) != pair:
                continue
            item_changes.append(
                {"key": key, "before": "uncertain",
                 "after": entry["state"],
                 "owner_before": None, "owner_after": entry["owner"],
                 "resolution": entry["resolution"],
                 "basis": POLICY_CHANGE}
            )
    for pair in sorted(right_uncertain - left_uncertain):
        item_changes.append(
            {"key": (pair[0], pair[1], "待确认（批准文件冲突）"),
             "before": None, "after": "uncertain",
             "owner_before": None, "owner_after": None,
             "resolution": None, "basis": POLICY_CHANGE}
        )

    # 两端都不处于冲突、但两期之间发生过"冲突爆发又解除"的任务：
    # 其确定待办的依据必须标注规范改判（发送日快照看不到中间挂起）。
    task_ids = {key[0] for key in set(left_keys) | set(right_keys)}
    flipped_tasks = set()
    for task_id in task_ids:
        task_row = index_tasks(tables)[task_id]
        days = range(left_day + 1, right_day + 1)
        if any(
            resolve_applicability(tables, task_row, day).state == "conflict"
            for day in days
        ):
            flipped_tasks.add(task_id)

    handled = {change["key"] for change in item_changes}
    for key in sorted(set(left_keys) | set(right_keys)):
        if key in handled:
            continue
        before, after = left_keys.get(key), right_keys.get(key)
        if before is None or after is None:
            basis = _item_basis(
                tables, key,
                None if before is None else before["state"],
                None if after is None else after["state"],
                left_day, right_day,
            )
        elif key[0] in flipped_tasks and before["state"] == after["state"]:
            # 两期之间经历过文件冲突爆发与解除：即使待办状态未变，
            # 该条目存在的依据首先是规范改判
            basis = POLICY_CHANGE
        elif before["state"] != after["state"]:
            basis = _item_basis(
                tables, key, before["state"], after["state"], left_day, right_day
            )
        elif before["owner"] != after["owner"]:
            basis = BEHAVIOR
        else:
            continue
        item_changes.append(
            {"key": key, "before": None if before is None else before["state"],
             "after": None if after is None else after["state"],
             "owner_before": None if before is None else before["owner"],
             "owner_after": None if after is None else after["owner"],
             "resolution": None if after is None else after["resolution"],
             "basis": basis}
        )

    return {
        "left_day": left_day,
        "right_day": right_day,
        "rate_changes": rate_changes,
        "item_changes": item_changes,
        "remediation": {
            "left": remediation_rate(tables, left_day),
            "right": remediation_rate(tables, right_day),
        },
    }


def _rate_basis(tables, key, before, after, left_day, right_day):
    """指标口径行变化的主依据。"""
    if before["denominator"] != after["denominator"]:
        return COMPOSITION
    if before["numerator"] != after["numerator"]:
        return BEHAVIOR
    return "无变化"


def supervisor_packet(tables, as_of):
    """督导视角：可比口径执行率、冲突清单、未到观察点清单与补救完成率。"""
    pairs, by_task, tasks = evaluate_all(tables, as_of)
    pending = [
        {"task": status.task, "student": status.student,
         "window_end": status.window_end}
        for status in pairs if status.state == "pending_observation"
    ]
    uncertain = [
        {"task": status.task, "student": status.student, "note": status.note,
         "documents": [doc["document"] for doc in status.documents]}
        for status in pairs if status.state == UNCERTAIN
    ]
    return {
        "as_of": as_of,
        "rates": indicator_rates(tables, as_of),
        "remediation": remediation_rate(tables, as_of),
        "uncertain": uncertain,
        "pending_observation": pending,
    }
