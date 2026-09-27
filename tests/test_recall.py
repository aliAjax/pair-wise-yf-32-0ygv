import sys, tempfile, unittest
from datetime import timedelta
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import ApiError, OrganAllocationService, iso, utcnow


class DonorRecallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.svc = OrganAllocationService(Path(self.tmp.name) / "test.db"); self.now = utcnow()
        self.occurred = iso(self.now - timedelta(hours=6))

    def tearDown(self): self.tmp.cleanup()

    def donor(self, expires_days=2):
        return self.svc.register_donor("coord", "coordinator", {"blood_type": "O", "organ": "kidney", "hospital": "H1", "region": "East", "available_at": iso(self.now - timedelta(days=3)), "expires_at": iso(self.now + timedelta(days=expires_days)), "clinical_match": 8})

    def candidate(self, name="患者甲", hospital="H2", urgency=5, wait=500):
        return self.svc.register_candidate("coord", "coordinator", {"patient_name": name, "blood_type": "B", "organ": "kidney", "hospital": hospital, "region": "East", "urgency": urgency, "wait_days": wait, "willing": True, "clinical_match": 9})

    def allocate(self):
        donor, candidate = self.donor(), self.candidate()
        allocation = self.svc.propose("allocator", "allocation_officer", {"donor_id": donor["id"], "candidate_id": candidate["id"]})
        return donor, candidate, allocation

    def recall_body(self, donor_id, occurred=None):
        return {"donor_id": donor_id, "risk_type": "hbv", "report_source": "实验室补报", "occurred_at": occurred or self.occurred}

    def test_recall_halts_unimplanted_flow_until_acknowledged(self):
        donor, _, allocation = self.allocate()
        self.svc.accept(allocation["id"], "h2", "hospital", "H2", {"expected_revision": 1})
        transit = self.svc.mark_transit(allocation["id"], "allocator", "allocation_officer", {"cold_chain_temp": 3.5})
        recall = self.svc.register_recall("coord", "coordinator", self.recall_body(donor["id"]))
        self.assertEqual(recall["status"], "open")
        self.assertEqual(recall["affected_hospitals"], ["H2"])
        self.assertEqual(recall["pending_count"], 1)
        self.assertEqual(recall["notifications"][0]["kind"], "hold")
        # 在途分配立即停住后续交接
        with self.assertRaises(ApiError) as ctx:
            self.svc.initiate_handoff(allocation["id"], "h1", "hospital", "H1", {"expected_revision": transit["revision"], "to_hospital": "H2", "cold_chain_temp": 3.0})
        self.assertEqual(ctx.exception.code, "recall_hold")
        # 接收医院未确认时召回不能关闭
        with self.assertRaises(ApiError) as ctx:
            self.svc.close_recall(recall["id"], "coord", "coordinator")
        self.assertEqual(ctx.exception.code, "recall_unacknowledged")
        # 非接收医院不能确认
        with self.assertRaises(ApiError) as ctx:
            self.svc.acknowledge_recall(recall["id"], "h1", "hospital", "H1")
        self.assertEqual(ctx.exception.status, 403)
        # 接收医院逐条确认后召回可关闭
        acked = self.svc.acknowledge_recall(recall["id"], "h2", "hospital", "H2")
        self.assertEqual(acked["pending_count"], 0)
        self.assertEqual(acked["notifications"][0]["status"], "acknowledged")
        closed = self.svc.close_recall(recall["id"], "coord", "coordinator")
        self.assertEqual(closed["status"], "closed")
        self.assertIsNotNone(closed["closed_at"])
        # 召回登记后未植入分配保持冻结
        with self.assertRaises(ApiError) as ctx:
            self.svc.implant(allocation["id"], "allocator", "allocation_officer", {})
        self.assertEqual(ctx.exception.code, "recall_hold")
        actions = [item["action"] for item in self.svc.audit(allocation["id"], "auditor")]
        self.assertIn("recall_acknowledged", actions)

    def test_proposed_allocation_cannot_be_accepted_after_recall(self):
        donor, _, allocation = self.allocate()
        self.svc.register_recall("coord", "coordinator", self.recall_body(donor["id"]))
        with self.assertRaises(ApiError) as ctx:
            self.svc.accept(allocation["id"], "h2", "hospital", "H2", {"expected_revision": 1})
        self.assertEqual(ctx.exception.code, "recall_hold")

    def test_implanted_allocation_only_generates_follow_up(self):
        donor, _, allocation = self.allocate()
        self.svc.accept(allocation["id"], "h2", "hospital", "H2", {"expected_revision": 1})
        transit = self.svc.mark_transit(allocation["id"], "allocator", "allocation_officer", {"cold_chain_temp": 3.5})
        self.svc.initiate_handoff(allocation["id"], "h1", "hospital", "H1", {"expected_revision": transit["revision"], "to_hospital": "H2", "cold_chain_temp": 3.0})
        self.svc.accept_handoff(allocation["id"], "h2", "hospital", "H2", {})
        implanted = self.svc.implant(allocation["id"], "allocator", "allocation_officer", {})
        recall = self.svc.register_recall("coord", "coordinator", self.recall_body(donor["id"]))
        self.assertEqual(recall["notifications"][0]["kind"], "follow_up")
        # 已植入分配保留原状态
        after = self.svc.get_allocation(allocation["id"], "auditor", "")
        self.assertEqual(after["status"], "implanted")
        self.assertEqual(after["implanted_at"], implanted["implanted_at"])
        # 接收医院确认随访通知后召回关闭
        self.svc.acknowledge_recall(recall["id"], "h2", "hospital", "H2")
        closed = self.svc.close_recall(recall["id"], "coord", "coordinator")
        self.assertEqual(closed["status"], "closed")

    def test_duplicate_registration_returns_first_result(self):
        donor, _, _ = self.allocate()
        first = self.svc.register_recall("coord", "coordinator", self.recall_body(donor["id"]))
        again = self.svc.register_recall("coord", "coordinator", self.recall_body(donor["id"]))
        self.assertTrue(again["duplicate"])
        self.assertEqual(again["id"], first["id"])
        self.assertEqual(len(again["notifications"]), 1)
        # 发生时刻不同属于不同风险，生成独立召回
        other = self.svc.register_recall("coord", "coordinator", self.recall_body(donor["id"], iso(self.now - timedelta(hours=2))))
        self.assertNotEqual(other["id"], first["id"])
        self.assertEqual(len(self.svc.recall_summaries("coordinator")), 2)

    def test_recall_without_allocation_closes_and_blocks_new_proposal(self):
        donor = self.donor()
        recall = self.svc.register_recall("coord", "coordinator", self.recall_body(donor["id"]))
        self.assertEqual(recall["notifications"], [])
        self.assertEqual(recall["pending_count"], 0)
        closed = self.svc.close_recall(recall["id"], "coord", "coordinator")
        self.assertEqual(closed["status"], "closed")
        candidate = self.candidate()
        with self.assertRaises(ApiError) as ctx:
            self.svc.propose("allocator", "allocation_officer", {"donor_id": donor["id"], "candidate_id": candidate["id"]})
        self.assertEqual(ctx.exception.code, "recall_hold")

    def test_recall_permissions_and_console_view(self):
        donor, _, _ = self.allocate()
        with self.assertRaises(ApiError) as ctx:
            self.svc.register_recall("allocator", "allocation_officer", self.recall_body(donor["id"]))
        self.assertEqual(ctx.exception.status, 403)
        with self.assertRaises(ApiError) as ctx:
            self.svc.register_recall("coord", "coordinator", {"donor_id": donor["id"], "risk_type": "hbv"})
        self.assertEqual(ctx.exception.code, "missing_fields")
        with self.assertRaises(ApiError) as ctx:
            self.svc.register_recall("coord", "coordinator", self.recall_body(9999))
        self.assertEqual(ctx.exception.code, "donor_not_found")
        recall = self.svc.register_recall("coord", "coordinator", self.recall_body(donor["id"]))
        with self.assertRaises(ApiError) as ctx:
            self.svc.recall_detail(recall["id"], "viewer")
        self.assertEqual(ctx.exception.status, 403)
        with self.assertRaises(ApiError) as ctx:
            self.svc.close_recall(recall["id"], "allocator", "allocation_officer")
        self.assertEqual(ctx.exception.status, 403)
        # 协调台：受影响医院、当前状态、未确认数量
        state = self.svc.state("coordinator", "")
        entry = next(r for r in state["recalls"] if r["id"] == recall["id"])
        self.assertEqual(entry["affected_hospitals"], ["H2"])
        self.assertEqual(entry["status"], "open")
        self.assertEqual(entry["pending_count"], 1)
        # 接收医院在自己的视图中看到召回通知
        notices = self.svc.state("hospital", "H2")["recall_notifications"]
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0]["kind"], "hold")
        self.assertEqual(notices[0]["risk_type"], "hbv")
        self.assertEqual(notices[0]["status"], "pending")


if __name__ == "__main__": unittest.main()
