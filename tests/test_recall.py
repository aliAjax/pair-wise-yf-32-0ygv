import sys, tempfile, unittest
from datetime import timedelta
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import ApiError, OrganAllocationService, iso, utcnow


class RecallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.svc = OrganAllocationService(Path(self.tmp.name) / "test.db")
        self.now = utcnow()

    def tearDown(self): self.tmp.cleanup()

    def donor(self, hospital="H1"):
        return self.svc.register_donor("coord", "coordinator", {"blood_type": "O", "organ": "kidney", "hospital": hospital,
            "region": "East", "available_at": iso(self.now - timedelta(days=1)), "expires_at": iso(self.now + timedelta(days=2)), "clinical_match": 8})

    def candidate(self, name="患者甲", hospital="H2"):
        return self.svc.register_candidate("coord", "coordinator", {"patient_name": name, "blood_type": "B", "organ": "kidney",
            "hospital": hospital, "region": "East", "urgency": 5, "wait_days": 500, "willing": True, "clinical_match": 9})

    def propose_for(self, donor, candidate):
        return self.svc.propose("allocator", "allocation_officer", {"donor_id": donor["id"], "candidate_id": candidate["id"]})

    def run_to_transit(self):
        donor, candidate = self.donor(), self.candidate()
        allocation = self.propose_for(donor, candidate)
        self.svc.accept(allocation["id"], "h2", "hospital", "H2", {"expected_revision": 1})
        self.svc.mark_transit(allocation["id"], "allocator", "allocation_officer", {"cold_chain_temp": 3.5})
        return donor, candidate, allocation

    def run_to_implanted(self):
        donor, candidate = self.donor(), self.candidate()
        allocation = self.propose_for(donor, candidate)
        self.svc.accept(allocation["id"], "h2", "hospital", "H2", {"expected_revision": 1})
        transit = self.svc.mark_transit(allocation["id"], "allocator", "allocation_officer", {"cold_chain_temp": 3.5})
        self.svc.initiate_handoff(allocation["id"], "h1", "hospital", "H1",
                                  {"expected_revision": transit["revision"], "to_hospital": "H2", "cold_chain_temp": 3.0})
        self.svc.accept_handoff(allocation["id"], "h2", "hospital", "H2", {})
        self.svc.implant(allocation["id"], "allocator", "allocation_officer", {})
        return donor, candidate, allocation

    def recall_body(self, donor, risk="hiv_positive", when=None):
        return {"donor_id": donor["id"], "risk_type": risk, "report_source": "实验室补报",
                "occurred_at": when or iso(self.now - timedelta(hours=2))}

    def test_unimplanted_allocations_stopped_and_ack_and_close(self):
        donor, _, allocation = self.run_to_transit()
        recall, created = self.svc.recalls.register_recall("coord", "coordinator", self.recall_body(donor))
        self.assertTrue(created)
        self.assertEqual(recall["status"], "open")
        self.assertEqual(recall["affected_hospitals"], ["H2"])
        self.assertEqual(recall["stopped_count"], 1)
        self.assertEqual(recall["followup_count"], 0)
        self.assertEqual(recall["pending_count"], 1)
        note = recall["notifications"][0]
        self.assertEqual(note["disposition"], "stopped")
        self.assertEqual(note["allocation_status"], "in_transit")
        # 未植入分配被停住，后续交接全部被阻止
        refreshed = self.svc.get_allocation(allocation["id"], "auditor", "")
        self.assertEqual(refreshed["status"], "recalled")
        with self.assertRaises(ApiError) as ctx:
            self.svc.initiate_handoff(allocation["id"], "h1", "hospital", "H1",
                                      {"expected_revision": refreshed["revision"], "to_hospital": "H2", "cold_chain_temp": 3.0})
        self.assertEqual(ctx.exception.code, "allocation_closed")
        # 别的医院不能确认；接收医院逐条确认
        with self.assertRaises(ApiError) as ctx:
            self.svc.recalls.acknowledge(note["id"], "h9", "hospital", "H9")
        self.assertEqual(ctx.exception.status, 403)
        ack = self.svc.recalls.acknowledge(note["id"], "h2", "hospital", "H2")
        self.assertEqual(ack["notification"]["acknowledged"], 1)
        self.assertEqual(ack["pending"], 0)
        # 重复确认保持幂等
        self.svc.recalls.acknowledge(note["id"], "h2", "hospital", "H2")
        closed = self.svc.recalls.close_recall(recall["id"], "coord", "coordinator")
        self.assertEqual(closed["status"], "closed")
        # 关闭重复提交幂等
        self.svc.recalls.close_recall(recall["id"], "coord", "coordinator")

    def test_implanted_keeps_status_and_only_creates_followup(self):
        donor, _, allocation = self.run_to_implanted()
        recall, created = self.svc.recalls.register_recall("coord", "coordinator", self.recall_body(donor))
        self.assertTrue(created)
        refreshed = self.svc.get_allocation(allocation["id"], "auditor", "")
        self.assertEqual(refreshed["status"], "implanted")
        self.assertEqual(recall["stopped_count"], 0)
        self.assertEqual(recall["followup_count"], 1)
        self.assertEqual(len(recall["recipient_followups"]), 1)
        followup = recall["recipient_followups"][0]
        self.assertEqual(followup["allocation_id"], allocation["id"])
        self.assertEqual(followup["hospital"], "H2")
        self.assertEqual(followup["risk_type"], "hiv_positive")
        note = recall["notifications"][0]
        self.assertEqual(note["disposition"], "followup_only")
        self.assertEqual(note["allocation_status"], "implanted")
        # 受者随访出现在接收医院的状态视图
        state = self.svc.state("hospital", "H2")
        self.assertEqual(len(state["recipient_followups"]), 1)
        self.assertEqual(state["recall_notifications"][0]["allocation_id"], allocation["id"])

    def test_close_blocked_until_all_acknowledged(self):
        donor = self.donor(); cand_a = self.candidate("患者甲", "H2")
        alloc_a = self.propose_for(donor, cand_a)
        recall, _ = self.svc.recalls.register_recall("coord", "coordinator", self.recall_body(donor))
        self.assertEqual(recall["pending_count"], 1)
        with self.assertRaises(ApiError) as ctx:
            self.svc.recalls.close_recall(recall["id"], "coord", "coordinator")
        self.assertEqual(ctx.exception.code, "recall_pending_ack")
        note = recall["notifications"][0]
        self.svc.recalls.acknowledge(note["id"], "h2", "hospital", "H2")
        closed = self.svc.recalls.close_recall(recall["id"], "coord", "coordinator")
        self.assertEqual(closed["status"], "closed")
        self.assertEqual(alloc_a and self.svc.get_allocation(alloc_a["id"], "auditor", "")["status"], "recalled")

    def test_duplicate_registration_returns_first_result(self):
        donor, _, allocation = self.run_to_transit()
        first, created = self.svc.recalls.register_recall("coord", "coordinator", self.recall_body(donor))
        self.assertTrue(created)
        again, created2 = self.svc.recalls.register_recall("coord", "coordinator", self.recall_body(donor, when=iso(self.now)))
        self.assertFalse(created2)
        self.assertEqual(again["id"], first["id"])
        self.assertTrue(again["duplicate"])
        # 同一供体的另一类风险仍可登记
        other, created3 = self.svc.recalls.register_recall("coord", "coordinator", self.recall_body(donor, risk="hbv_positive"))
        self.assertTrue(created3)
        self.assertNotEqual(other["id"], first["id"])

    def test_recall_roles_and_donor_blocked_for_new_allocation(self):
        with self.assertRaises(ApiError) as ctx:
            self.svc.recalls.register_recall("h2", "hospital", {"donor_id": 1})
        self.assertEqual(ctx.exception.status, 403)
        donor = self.donor()
        with self.assertRaises(ApiError) as ctx:
            self.svc.recalls.register_recall("coord", "coordinator", {"donor_id": donor["id"], "risk_type": "x", "report_source": ""})
        self.assertEqual(ctx.exception.status, 400)
        with self.assertRaises(ApiError) as ctx:
            self.svc.recalls.register_recall("coord", "coordinator",
                                            {"donor_id": 999, "risk_type": "x", "report_source": "lab",
                                             "occurred_at": iso(self.now)})
        self.assertEqual(ctx.exception.status, 404)
        recall, _ = self.svc.recalls.register_recall("coord", "coordinator", self.recall_body(donor))
        # 没有任何分配的供体：零通知可直接关闭；新分配被冻结拦截
        self.assertEqual(recall["total_notifications"], 0)
        self.svc.recalls.close_recall(recall["id"], "coord", "coordinator")
        candidate = self.candidate()
        with self.assertRaises(ApiError) as ctx:
            self.propose_for(donor, candidate)
        self.assertEqual(ctx.exception.code, "donor_under_recall")

    def test_console_and_hospital_visibility(self):
        donor, _, allocation = self.run_to_transit()
        recall, _ = self.svc.recalls.register_recall("coord", "coordinator", self.recall_body(donor))
        # 协调台：受影响医院、当前状态、未确认数量
        state = self.svc.state("coordinator", "")
        row = state["recalls"][0]
        self.assertEqual(row["affected_hospitals"], ["H2"])
        self.assertEqual(row["status"], "open")
        self.assertEqual(row["pending_count"], 1)
        self.assertEqual(row["stopped_count"], 1)
        # 无关医院看不到，接收医院只看到自己的通知
        self.assertEqual(self.svc.recalls.list_recalls("hospital", "H9")["recalls"], [])
        own = self.svc.recalls.get_recall(recall["id"], "hospital", "H2")
        self.assertEqual(len(own["notifications"]), 1)
        with self.assertRaises(ApiError) as ctx:
            self.svc.recalls.get_recall(recall["id"], "hospital", "H9")
        self.assertEqual(ctx.exception.status, 403)
        with self.assertRaises(ApiError) as ctx:
            self.svc.recalls.list_recalls("viewer", "")
        self.assertEqual(ctx.exception.status, 403)

    def test_audit_trail_recorded(self):
        donor, _, allocation = self.run_to_transit()
        recall, _ = self.svc.recalls.register_recall("coord", "coordinator", self.recall_body(donor))
        note_id = recall["notifications"][0]["id"]
        self.svc.recalls.acknowledge(note_id, "h2", "hospital", "H2")
        self.svc.recalls.close_recall(recall["id"], "coord", "coordinator")
        conn = self.svc.repo.conn
        actions = [r["action"] for r in conn.execute("SELECT action FROM audit_log ORDER BY id")]
        self.assertIn("donor_recall_registered", actions)
        self.assertIn("allocation_recalled", actions)
        self.assertIn("recall_notification_acknowledged", actions)
        self.assertIn("donor_recall_closed", actions)
        # 重复登记也留审计
        self.svc.recalls.register_recall("coord", "coordinator", self.recall_body(donor))
        actions = [r["action"] for r in conn.execute("SELECT action FROM audit_log ORDER BY id")]
        self.assertIn("donor_recall_duplicate", actions)


if __name__ == "__main__": unittest.main()
