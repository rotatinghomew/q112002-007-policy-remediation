"""补救（补办）机会、认领与裁定。

规则：
- 学生在补办窗 [start, end]（含首尾）内完成另一个独立任务，且提示日 hint 晚于
  独立完成日 independent（hint 为空表示监考确认未提供提示，同样满足），补救才有效。
- 一次补做只能认领一次明确允许的原任务：work 必须经 makeup_options 登记允许、
  学生属于该原任务名单、认领日在补办窗之后（窗口含首尾，认领不早于完成日）。
- 同一 work 被两位老师认领且未裁定前，双方都不能结案；裁定 granted 指向的一方
  解除待办，另一方仍需处理。裁定只解决该 work 的竞争，不替另一方补出补救。
- 规范更正后原任务不再要求某步骤的，待办以"规范改判撤回"结案；已经做过的补救
  保留在学生记录中，不抹除、不自动挪去结另一项。
- 原按期指标不因补救而增加；补救完成率另算。
"""

from dataclasses import dataclass, field

from .compliance import MISSING, OK, evaluate_all, index_tasks, owner_at
from .policy import resolve_applicability

# 待办最终去向
OPEN = "open"
RESOLVED_MAKEUP = "resolved_makeup"
WITHDRAWN_POLICY = "withdrawn_policy"

# 认领状态
CLAIM_VALID = "valid"                 # 可解除待办
CLAIM_CONTESTED = "contested"         # 同 work 多认领带裁定
CLAIM_LOST_RULING = "lost_ruling"     # 裁定给了竞争方
CLAIM_DEFECTIVE = "defective"         # work 本身不满足补救条件


def _options_index(tables):
    index = {}
    for row in tables["makeup_options"]:
        index.setdefault((row["task"], row["exercise"]), row)
    return index


def _work_index(tables):
    return {row["work"]: row for row in tables["work"]}


def evaluate_work_quality(work_row, option):
    """一次补做活动本身是否满足"独立且提示在后"。"""
    if not (option["start"] <= work_row["independent"] <= option["end"]):
        return False, "独立完成日不在补办窗内"
    if work_row["hint"] is not None and work_row["hint"] <= work_row["independent"]:
        return False, "提示不晚于独立完成"
    return True, ""


@dataclass
class ClaimVerdict:
    claim: str
    task: str
    work: str
    teacher: str
    state: str
    reasons: list = field(default_factory=list)


def evaluate_claims(tables, as_of):
    """逐条评估认领在 as_of 的效力；竞争与裁定按 work 归并判断。"""
    options = _options_index(tables)
    works = _work_index(tables)
    rulings = {
        row["work"]: row for row in tables["rulings"]
        if row["day"] is not None and row["day"] <= as_of
    }
    roster_pairs = {(row["task"], row["student"]) for row in tables["roster"]}

    by_work = {}
    for row in tables["claims"]:
        by_work.setdefault(row["work"], []).append(row)

    verdicts = []
    for work_id, claims in by_work.items():
        work_row = works[work_id]
        granted = rulings.get(work_id, {}).get("granted")
        contested = len(claims) > 1
        for claim in claims:
            reasons = []
            option = options.get((claim["task"], work_row["exercise"]))
            if option is None:
                reasons.append("该原任务未登记允许此补救练习")
            elif (claim["task"], work_row["student"]) not in roster_pairs:
                reasons.append("补做学生不在原任务名单")
            else:
                ok, why = evaluate_work_quality(work_row, option)
                if not ok:
                    reasons.append(why)
            if claim["day"] > as_of:
                reasons.append("认领尚未发生")
            elif work_row["independent"] > as_of:
                reasons.append("补做尚未完成")

            if reasons:
                state = CLAIM_DEFECTIVE
            elif contested and granted is None:
                state = CLAIM_CONTESTED
            elif contested and granted is not None and claim["claim"] != granted:
                state = CLAIM_LOST_RULING
            else:
                state = CLAIM_VALID
            verdicts.append(
                ClaimVerdict(claim["claim"], claim["task"], work_id,
                             claim["teacher"], state, reasons)
            )
    return verdicts


def followup_ledger(tables, as_of):
    """把缺失待办与补救、规范更正合并成待办底账。

    每个 (task, student, step) 一条：
    - state: open / resolved_makeup / withdrawn_policy
    - resolution 记录解除依据（认领编号或撤回文件），便于两期差异溯源。
    """
    pairs, _, tasks = evaluate_all(tables, as_of)
    claim_verdicts = evaluate_claims(tables, as_of)
    valid_by_target = {}
    for verdict in claim_verdicts:
        if verdict.state == CLAIM_VALID:
            work_row = next(row for row in tables["work"] if row["work"] == verdict.work)
            valid_by_target.setdefault(
                (verdict.task, work_row["student"]), []
            ).append(verdict)

    ledger = []
    for status in pairs:
        if status.state != MISSING:
            continue
        current = resolve_applicability(tables, tasks[status.task], as_of)
        for step, result in status.steps.items():
            if result == OK:
                continue
            entry = {
                "task": status.task,
                "student": status.student,
                "step": step,
                "result": result,
                "state": OPEN,
                "resolution": None,
                "owner": owner_at(tables, status.task, as_of),
                "window_policy": status.policy_id,
            }
            # 规范更正：观察期结束时曾要求、as_of 已不要求 -> 撤回，不记教师失误。
            # 历史分子分母保留在原口径，不搬迁、不抹除已发生的补救记录。
            if current.state == "determined" and step not in current.required:
                entry["state"] = WITHDRAWN_POLICY
                entry["resolution"] = {
                    "type": "policy_change",
                    "documents": [doc["document"] for doc in current.documents],
                    "policy": current.policy["policy"],
                    "window_policy": status.policy_id,
                }
            else:
                usable = [
                    verdict
                    for verdict in valid_by_target.get(
                        (status.task, status.student), []
                    )
                    if _claim_allowed_for_step(tables, verdict, step)
                ]
                if usable:
                    winner = sorted(usable, key=lambda item: item.claim)[0]
                    entry["state"] = RESOLVED_MAKEUP
                    entry["resolution"] = {
                        "type": "makeup",
                        "claim": winner.claim,
                        "work": winner.work,
                        "teacher": winner.teacher,
                    }
            ledger.append(entry)
    return ledger, claim_verdicts


def _claim_allowed_for_step(tables, verdict, step):
    """补救只针对"独立草稿"环节；数据中 makeup_options 即独立任务的补办通道。"""
    return step == "独立草稿"


def remediation_rate(tables, as_of):
    """补救完成率另算：已解除的草稿待办 / 进入补救观察的草稿待办总数。

    分母 = 原始口径下出现过草稿缺失、且该要求未被规范撤回的配对；
    被撤回的不进补救口径；冲突任务在裁定前同样不进（不能伪装达标）。
    """
    ledger, _ = followup_ledger(tables, as_of)
    draft = [entry for entry in ledger if entry["step"] == "独立草稿"]
    eligible = [
        entry for entry in draft if entry["state"] != WITHDRAWN_POLICY
    ]
    resolved = [entry for entry in eligible if entry["state"] == RESOLVED_MAKEUP]
    denominator = len(eligible)
    return {
        "numerator": len(resolved),
        "denominator": denominator,
        "rate": round(len(resolved) / denominator, 4) if denominator else None,
        "resolved": resolved,
        "open": [entry for entry in eligible if entry["state"] == OPEN],
    }
