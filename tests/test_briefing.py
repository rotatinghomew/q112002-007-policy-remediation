"""简报引擎口径测试：用资料中的真实时间线锁定每条业务规则。"""

import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from briefing import (  # noqa: E402
    BEHAVIOR,
    COMPOSITION,
    POLICY_CHANGE,
    build_dispatch,
    diff_editions,
    evaluate_claims,
    indicator_rates,
    observation_window,
    resolve_applicability,
    supervisor_packet,
)
from briefing.compliance import (  # noqa: E402
    MISSING,
    OK,
    PENDING,
    UNCERTAIN,
    evaluate_all,
    index_tasks,
    owner_at,
)
from briefing.policy import CONFLICT  # noqa: E402
from briefing.remediation import (  # noqa: E402
    CLAIM_CONTESTED,
    CLAIM_DEFECTIVE,
    CLAIM_LOST_RULING,
    CLAIM_VALID,
    OPEN,
    RESOLVED_MAKEUP,
    WITHDRAWN_POLICY,
    followup_ledger,
    remediation_rate,
)
from record_reader import read_records  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


class EngineTest(unittest.TestCase):
    def setUp(self):
        self.tables = read_records(ROOT / "records")
        self.tasks = index_tasks(self.tables)

    def _rates(self, as_of):
        return {
            (r["policy"], r["indicator"]): (r["numerator"], r["denominator"])
            for r in indicator_rates(self.tables, as_of)
        }

    def _pair(self, as_of, task_id, student):
        pairs, _, _ = evaluate_all(self.tables, as_of)
        return next(p for p in pairs if p.task == task_id and p.student == student)

    # 1. 观察期：重开延长同一条观察期，不产生新任务/新分母
    def test_reopen_extends_single_window(self):
        self.assertEqual(observation_window(self.tasks["t1"]), (5, 15))
        self.assertEqual(observation_window(self.tasks["t2"]), (8, 13))

    def test_pending_pairs_excluded_from_denominator(self):
        pending = {
            (p.task, p.student)
            for p in evaluate_all(self.tables, 13)[0]
            if p.state == PENDING
        }
        # t1 观察期到 15、t4 到 16，第 13 日都未到观察点
        self.assertTrue(all(task == "t1" for task, _ in pending if task == "t1"))
        self.assertIn(("t4", "s9"), pending)
        self.assertEqual(self._rates(13).get(("p1", "提交")), None)

    def test_t1_submissions_after_extended_window(self):
        # 与 released.json 历史简报一致：s6 始终无提交记录，重开后仍是 5/6
        rates = self._rates(20)
        self.assertEqual(rates[("p1", "提交")], (5, 6))

    # 2. 规范适用：开放日选定、例外文件按收到日生效、冲突挂起
    def test_base_policy_by_opening_day(self):
        self.assertEqual(
            resolve_applicability(self.tables, self.tasks["t1"], 13).policy["policy"],
            "p1",
        )
        self.assertEqual(
            resolve_applicability(self.tables, self.tasks["t4"], 20).policy["policy"],
            "p3",
        )

    def test_late_approval_not_effective_before_received(self):
        # e4 第 25 日才收到：第 20 日 t3 仍按 p2，草稿缺失是有效待办
        verdict = resolve_applicability(self.tables, self.tasks["t3"], 20)
        self.assertEqual(verdict.policy["policy"], "p2")
        self.assertIn("独立草稿", verdict.required)

    def test_conflicting_documents_suspend_without_default_priority(self):
        verdict = resolve_applicability(self.tables, self.tasks["t2"], 15)
        self.assertEqual(verdict.state, CONFLICT)
        pair = self._pair(15, "t2", "s3")
        self.assertEqual(pair.state, UNCERTAIN)

    def test_cancellation_resolves_conflict(self):
        # e3 第 17 日撤销 e1，此后 t2 确定适用 p2
        verdict = resolve_applicability(self.tables, self.tasks["t2"], 17)
        self.assertEqual(verdict.policy["policy"], "p2")
        self.assertEqual(self._pair(20, "t2", "s3").state, MISSING)

    def test_conflict_pairs_never_counted_as_failures_or_compliant(self):
        rates15 = self._rates(15)
        # 冲突期 t2 六个配对整体退出 p2 口径，只剩 t3 的 3 个
        self.assertEqual(rates15[("p2", "提交")], (3, 3))
        self.assertEqual(rates15[("p2", "独立草稿")], (1, 3))

    # 3. 步骤履行：按 happened 判定，补传不改判
    def test_happened_governs_ontime_not_received(self):
        # t2/s2 草稿 happened=11 在窗内、received=16 补传：仍为按时
        pair = self._pair(20, "t2", "s2")
        self.assertEqual(pair.steps["独立草稿"], OK)

    def test_late_happened_is_followup_not_ontime(self):
        # 构造一个超期发生的步骤：临时改大 happened 验证 late 分支
        tables = _clone_with_late_step(self.tables)
        pairs, _, _ = evaluate_all(tables, 20)
        pair = next(p for p in pairs if p.task == "t3" and p.student == "s7")
        self.assertEqual(pair.steps["独立草稿"], "late")
        self.assertEqual(pair.state, MISSING)

    # 4. 口径冻结：历史分子分母不被后续改判搬迁
    def test_history_caliber_frozen_after_window(self):
        # 第 25 日 e4 把 t3 改判 p1，但第 27 日 p2 草稿口径仍保留 t2+t3 的 3/9
        rates = self._rates(27)
        self.assertEqual(rates[("p2", "独立草稿")], (3, 9))
        self.assertEqual(rates[("p2", "提交")], (8, 9))
        # t1 始终是 p1 口径；t3 的提交历史留在 p2，没有新增第二条 p1 提交行
        p1_submission = [r for r in indicator_rates(self.tables, 27)
                         if r["policy"] == "p1" and r["indicator"] == "提交"]
        self.assertEqual(len(p1_submission), 1)
        self.assertEqual(
            {(p["task"], p["student"]) for p in p1_submission[0]["pairs"]},
            {("t1", s) for s in ("s1", "s2", "s3", "s4", "s5", "s6")},
        )

    # 5. 补救：窗口、提示顺序、一次一认领、竞争裁定
    def test_claim_verdicts(self):
        verdicts = {v.claim: v.state for v in evaluate_claims(self.tables, 27)}
        self.assertEqual(verdicts["c1"], CLAIM_VALID)
        self.assertEqual(verdicts["c2"], CLAIM_LOST_RULING)
        self.assertEqual(verdicts["c3"], CLAIM_DEFECTIVE)   # 提示早于独立做
        self.assertEqual(verdicts["c4"], CLAIM_VALID)
        self.assertEqual(verdicts["c5"], CLAIM_VALID)

    def test_contested_claims_block_both_sides_before_ruling(self):
        verdicts = {v.claim: v.state for v in evaluate_claims(self.tables, 20)}
        self.assertEqual(verdicts["c1"], CLAIM_CONTESTED)
        self.assertEqual(verdicts["c2"], CLAIM_CONTESTED)
        # 裁定前 t2/s4 与 t3/s4 的草稿待办都不能结案
        ledger, _ = followup_ledger(self.tables, 20)
        for task_id, student in (("t2", "s4"), ("t3", "s4")):
            entry = next(
                e for e in ledger
                if e["task"] == task_id and e["student"] == student
                and e["step"] == "独立草稿"
            )
            self.assertEqual(entry["state"], OPEN)

    def test_ruling_grants_one_side_and_keeps_other_open(self):
        ledger, _ = followup_ledger(self.tables, 27)
        t2s4 = next(e for e in ledger if e["task"] == "t2"
                    and e["student"] == "s4" and e["step"] == "独立草稿")
        t3s4 = next(e for e in ledger if e["task"] == "t3"
                    and e["student"] == "s4" and e["step"] == "独立草稿")
        self.assertEqual(t2s4["state"], RESOLVED_MAKEUP)
        self.assertEqual(t2s4["resolution"]["claim"], "c1")
        # c2 败诉：t3/s4 转由 e4 规范改判撤回，而不是被 w1 结案
        self.assertEqual(t3s4["state"], WITHDRAWN_POLICY)

    def test_remediation_rate_separate_from_ontime_indicator(self):
        at20 = remediation_rate(self.tables, 20)
        self.assertEqual((at20["numerator"], at20["denominator"]), (1, 11))
        at27 = remediation_rate(self.tables, 27)
        # t3 两项被规范撤回，退出补救口径：3 / 9
        self.assertEqual((at27["numerator"], at27["denominator"]), (3, 9))
        # 原按时指标不随补救增加
        self.assertEqual(self._rates(27)[("p2", "独立草稿")], (3, 9))

    def test_policy_correction_withdraws_and_preserves_records(self):
        ledger, _ = followup_ledger(self.tables, 27)
        withdrawn = [
            e for e in ledger
            if e["state"] == WITHDRAWN_POLICY and e["task"] == "t3"
        ]
        self.assertEqual({e["student"] for e in withdrawn}, {"s4", "s8"})
        for entry in withdrawn:
            self.assertEqual(entry["resolution"]["window_policy"], "p2")
            self.assertEqual(entry["resolution"]["policy"], "p1")

    # 6. 任课变动：当日生效、新老师只接办未结案事项
    def test_owner_change_effective_same_day(self):
        self.assertEqual(owner_at(self.tables, "t2", 17), "a")
        self.assertEqual(owner_at(self.tables, "t2", 18), "d")

    def test_resolution_notice_sent_once_across_editions(self):
        i1 = build_dispatch(
            self.tables,
            next(e for e in self.tables["editions"] if e["issue"] == "i1"),
        )
        i2 = build_dispatch(
            self.tables,
            next(e for e in self.tables["editions"] if e["issue"] == "i2"),
        )
        i1_resolved = {
            (n["item"]["task"], n["item"]["student"])
            for n in i1["resolution_notices"]
        }
        i2_resolved = {
            (n["item"]["task"], n["item"]["student"])
            for n in i2["resolution_notices"]
        }
        # s5 在 i1 已结案通知，i2 不得重复；i2 只发本期新成立的 s4 与 t4/s9
        self.assertIn(("t2", "s5"), i1_resolved)
        self.assertNotIn(("t2", "s5"), i2_resolved)
        self.assertEqual(i2_resolved, {("t2", "s4"), ("t4", "s9")})

    def test_new_owner_does_not_rereceive_closed_makeup(self):
        dispatch = build_dispatch(
            self.tables,
            next(e for e in self.tables["editions"] if e["issue"] == "i2"),
        )
        d_items = {
            (i["task"], i["student"], i["step"])
            for i in dispatch["teacher_lists"].get("d", [])
        }
        # s4 已经 c1 结案、s5 已经 c4 结案，都不得再发给 d
        self.assertNotIn(("t2", "s4", "独立草稿"), d_items)
        self.assertNotIn(("t2", "s5", "独立草稿"), d_items)
        # 仍开放的 s3/s6 必须接办
        self.assertIn(("t2", "s3", "独立草稿"), d_items)

    # 7. 排程：备好后按发送日新事实核定；两期差异三分类
    def test_prepared_edition_rebased_to_send_day_facts(self):
        dispatch = build_dispatch(
            self.tables,
            next(e for e in self.tables["editions"] if e["issue"] == "i1"),
        )
        changes = {tuple(c["key"]): c["change"] for c in dispatch["prep_to_send_changes"]}
        # 备好日（16）冲突在挂起；发送日（20）已撤销并出现补救结案
        self.assertIn(("t2", "s5", "独立草稿"), changes)
        self.assertEqual(changes[("t2", "s5", "独立草稿")], "新增结案记录")

    def test_diff_classifies_three_bases(self):
        diff = diff_editions(self.tables, 13, 20)
        bases = {}
        for change in diff["item_changes"]:
            bases.setdefault(tuple(change["key"]), set()).add(change["basis"])
        # 新到观察点 -> 任务构成
        self.assertEqual(bases[("t4", "s9", "提交")], {COMPOSITION})
        # 冲突解除补判 -> 规范改判（同一配对可能同时挂补救行为标签）
        self.assertIn(POLICY_CHANGE, bases[("t2", "s3", "独立草稿")])
        # 补救结案 -> 教学行为与补救
        self.assertEqual(bases[("t2", "s5", "独立草稿")], {BEHAVIOR})

        diff2 = diff_editions(self.tables, 20, 27)
        bases2 = {}
        for change in diff2["item_changes"]:
            bases2.setdefault(tuple(change["key"]), set()).add(change["basis"])
        self.assertEqual(bases2[("t3", "s4", "独立草稿")], {POLICY_CHANGE})
        self.assertEqual(bases2[("t2", "s4", "独立草稿")], {BEHAVIOR})

    def test_supervisor_packet_exposes_uncertain_and_pending(self):
        packet = supervisor_packet(self.tables, 15)
        self.assertEqual({u["task"] for u in packet["uncertain"]}, {"t2"})
        self.assertTrue(packet["pending_observation"])

    def test_rates_keep_calibers_separate_for_fair_comparison(self):
        rates = indicator_rates(self.tables, 27)
        calibers = {(r["policy"], r["indicator"]) for r in rates}
        # p1 与 p2/p3 不合并；不存在跨口径总量行
        self.assertIn(("p2", "独立草稿"), calibers)
        self.assertIn(("p3", "独立草稿"), calibers)
        self.assertTrue(all(r["denominator"] > 0 for r in rates))


def _clone_with_late_step(tables):
    """把 t3/s7 的草稿发生日挪到观察期之后，验证 late 判定。"""
    clone = {key: [dict(row) for row in rows] for key, rows in tables.items()}
    for row in clone["steps"]:
        if row["task"] == "t3" and row["student"] == "s7" and row["step"] == "独立草稿":
            row["happened"] = 14
    return clone


if __name__ == "__main__":
    unittest.main(verbosity=2)
