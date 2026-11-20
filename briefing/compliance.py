"""观察期、步骤履行与指标分母。

统计单位是"学生 × 原任务"配对；重开只延长原登记的观察，不产生新任务。

观察期口径（与 released.json 历史简报互相印证）：
- 首次观察期为 [opened, due]，长度 L = due - opened（业务日）。
- 重开后只有一条连续观察期 [opened, reopened + L]，重开日之后的步骤与首次开放
  同等计入"按时履行"，但只在新截止日之前有效；重开不产生第二个分母。
- 步骤以 happened（行为发生日）判断是否落在观察期内；received 更晚只是补传，
  补传不能把超期行为改判为按时完成。同一配对同一步骤取最早 happened。

配对在 as_of 的状态：
- pending_observation：观察期尚未结束，不进入任何指标分母（未到观察点）。
- ok / missing：观察期已结束且适用要求确定；missing 即待跟进（教师失误只在此处认定）。
- uncertain：适用规范处于文件冲突未裁定状态，确定口径的分子分母都不计入，
  待办单列、挂"待确认"，不得伪装达标。
"""

from dataclasses import dataclass, field

from .policy import CONFLICT, DETERMINED, resolve_applicability

PENDING = "pending_observation"
OK = "ok"
MISSING = "missing"
UNCERTAIN = "uncertain"


def observation_window(task_row):
    """返回任务唯一观察期 (start, end)，重开延长同一条观察期。"""
    start = task_row["opened"]
    length = task_row["due"] - task_row["opened"]
    end = task_row["due"]
    if task_row["reopened"] is not None:
        end = task_row["reopened"] + length
    return start, end


def index_steps(tables):
    """(task, student, step) -> 最早发生日；另保留补传日供溯源。"""
    earliest = {}
    received = {}
    for row in tables["steps"]:
        key = (row["task"], row["student"], row["step"])
        if key not in earliest or row["happened"] < earliest[key]:
            earliest[key] = row["happened"]
            received[key] = row["received"]
    return earliest, received


def index_tasks(tables):
    return {row["task"]: row for row in tables["tasks"]}


def owner_at(tables, task_id, as_of):
    """as_of 当日的任课老师：owners 变动当日开始生效，取最晚一条；否则用原 owner。"""
    task_row = index_tasks(tables)[task_id]
    changes = [
        row
        for row in tables["owners"]
        if row["task"] == task_id
        and row["effective"] <= as_of
        and row["received"] is not None
        and row["received"] <= as_of
    ]
    if changes:
        return max(changes, key=lambda row: row["effective"])["owner"]
    return task_row["owner"]


@dataclass
class PairStatus:
    task: str
    student: str
    state: str
    required: list
    steps: dict                     # step -> ok / missing / late
    window_end: int
    policy_id: object = None
    documents: list = field(default_factory=list)
    note: str = ""
    window_state: str = ""          # 观察期结束当日的适用状态（口径冻结依据）
    current_state: str = ""         # as_of 当日的适用状态（撤回判断依据）


def evaluate_pair(tables, task_row, student, as_of, step_index):
    """评估单个学生×任务配对在 as_of 的履行状态。

    口径冻结：观察期一旦结束，配对按"观察期结束当日有效"的规范归并，
    之后到达的批准文件只产生规范改判（撤回待办/解除冲突），不搬迁历史分子分母。
    窗口结束后才爆发的文件冲突，在 as_of 仍未解除时，配对回到 uncertain：
    既不认定教师失误，也不算达标。
    """
    _, window_end = observation_window(task_row)
    window_verdict = resolve_applicability(tables, task_row, window_end)
    current_verdict = resolve_applicability(tables, task_row, as_of)

    if as_of < window_end:
        return PairStatus(
            task_row["task"], student, PENDING, window_verdict.required, {},
            window_end,
            window_verdict.policy["policy"] if window_verdict.policy else None,
            window_verdict.documents, window_verdict.note,
            window_verdict.state, current_verdict.state,
        )

    # as_of 当日仍处于冲突：无论窗口结束时状态如何，一律挂起等待发文单位确认
    if current_verdict.state == CONFLICT:
        return PairStatus(
            task_row["task"], student, UNCERTAIN, [], {}, window_end,
            None, current_verdict.documents, current_verdict.note,
            window_verdict.state, CONFLICT,
        )

    # 窗口结束时冲突、其后解除：按解除后的规范补判，但保留曾挂起的轨迹
    verdict = current_verdict if window_verdict.state == CONFLICT else window_verdict

    steps = {}
    for step in verdict.required:
        happened = step_index.get((task_row["task"], student, step))
        if happened is None:
            steps[step] = MISSING
        elif happened > window_end:
            steps[step] = "late"          # 超期发生：补传也不能改判按时
        else:
            steps[step] = OK
    state = OK if all(value == OK for value in steps.values()) else MISSING
    return PairStatus(
        task_row["task"], student, state, verdict.required, steps, window_end,
        verdict.policy["policy"], verdict.documents, verdict.note,
        window_verdict.state, current_verdict.state,
    )


def evaluate_all(tables, as_of):
    """返回全部配对评估、按任务分组，以及任务级适用结论。"""
    step_index, _ = index_steps(tables)
    tasks = index_tasks(tables)
    pairs = []
    by_task = {}
    for row in tables["roster"]:
        status = evaluate_pair(tables, tasks[row["task"]], row["student"],
                               as_of, step_index)
        pairs.append(status)
        by_task.setdefault(row["task"], []).append(status)
    return pairs, by_task, tasks


def indicator_rates(tables, as_of):
    """按"适用要求集合"分口径计算各步骤执行率。

    返回列表，每行一个可比口径（policy + grade + category + step）：
    分母只含观察期已结束、适用要求确定的配对；冲突与未到观察点一律排除。
    不同口径不得合并成一条总量折线。
    """
    pairs, by_task, tasks = evaluate_all(tables, as_of)
    cohorts = {}
    for status in pairs:
        if status.state not in (OK, MISSING):
            continue
        task_row = tasks[status.task]
        for step in status.required:
            key = (status.policy_id, task_row["grade"], task_row["category"], step)
            bucket = cohorts.setdefault(
                key, {"numerator": 0, "denominator": 0, "pairs": []}
            )
            bucket["denominator"] += 1
            if status.steps[step] == OK:
                bucket["numerator"] += 1
            bucket["pairs"].append(
                {"task": status.task, "student": status.student,
                 "result": status.steps[step]}
            )

    rates = []
    for (policy_id, grade, category, step), bucket in sorted(cohorts.items()):
        rates.append(
            {
                "policy": policy_id,
                "grade": grade,
                "category": category,
                "indicator": step,
                "numerator": bucket["numerator"],
                "denominator": bucket["denominator"],
                "rate": (
                    round(bucket["numerator"] / bucket["denominator"], 4)
                    if bucket["denominator"]
                    else None
                ),
                "pairs": bucket["pairs"],
            }
        )
    return rates


def open_followups(tables, as_of):
    """观察期已结束但存在缺失/超期步骤的确定口径配对，以及冲突待确认配对。

    补救解除逻辑在 remediation 模块处理；这里给出"原始要求下"的待办底账。
    """
    pairs, by_task, tasks = evaluate_all(tables, as_of)
    missing, uncertain = [], []
    for status in pairs:
        if status.state == MISSING:
            missing.append(status)
        elif status.state == UNCERTAIN:
            uncertain.append(status)
    return missing, uncertain
