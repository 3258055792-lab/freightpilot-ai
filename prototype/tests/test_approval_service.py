import concurrent.futures
import sqlite3
import json
import threading
import tempfile
import unittest
from pathlib import Path
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from prototype.approval_service import ApprovalStore, Principal, ServiceError, SYNTHETIC_CASE, SYNTHETIC_SOURCE, SYNTHETIC_TENANT, make_handler


class ApprovalServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name) / "approval.sqlite3"
        self.store = ApprovalStore(self.database)
        self.store.seed_demo()
        self.model = Principal("model-test-token", "model-a", SYNTHETIC_TENANT, "model")
        self.human = Principal("human-test-token", "human-a", SYNTHETIC_TENANT, "human")
        self.executor = Principal("executor-test-token", "executor-a", SYNTHETIC_TENANT, "executor")
        self.viewer = Principal("viewer-test-token", "viewer-a", SYNTHETIC_TENANT, "viewer")
        self.other_tenant = Principal("other-tenant-token", "human-b", "another-tenant", "human")

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def create_request(key="create-key-0001", product="合成塑料收纳盒"):
        return {
            "idempotency_key": key,
            "case": {"id": SYNTHETIC_CASE, "version": 1},
            "scope": "classification_trial",
            "source_ref": SYNTHETIC_SOURCE,
            "source_version": "v1",
            "input": {"product_name": product},
        }

    def create_pending(self, key="create-key-0001"):
        return self.store.create_approval(self.model, self.create_request(key))

    def approve(self, approval):
        record = approval["approval"]
        return self.store.decide(self.human, record["approval_id"], {
            "idempotency_key": "decision-key-0001",
            "expected_revision": record["revision"],
            "snapshot_hash": record["snapshot_hash"],
            "decision": "APPROVE",
            "reason": "synthetic test approval",
        })

    def operation_request(self, approval, operation_id="operation-key-0001"):
        record = approval["approval"]
        return {
            "operation_id": operation_id,
            "approval_id": record["approval_id"],
            "expected_approval_revision": record["revision"],
            "case": record["case"],
            "snapshot_hash": record["snapshot_hash"],
            "action": "simulate_classification_trial",
        }

    def test_weight_edit_invalidates_old_approval_and_requires_new_review(self):
        request = self.create_request()
        request["input"].update(weight_kg="400", volume_cbm="3")
        def edit(version, weight):
            return self.store.revise_case(self.human, SYNTHETIC_CASE, {
                "expected_version": version, "input": {**request["input"], "weight_kg": weight}})
        self.assertEqual(edit(1, "400")["version"], 2)
        request["case"]["version"] = 2
        approved = self.approve(self.store.create_approval(self.model, request))
        self.assertEqual(edit(2, "800")["version"], 3)
        self.assertFalse(edit(2, "800")["changed"])
        with self.assertRaises(ServiceError):
            self.store.execute(self.executor, self.operation_request(approved))
        self.assertEqual(self.store.get_approval(self.viewer, approved["approval"]["approval_id"])["state"], "INVALIDATED")
        request["case"]["version"] = 3
        request["idempotency_key"] = "updated-draft-001"
        with self.assertRaises(ServiceError) as mismatch:
            self.store.create_approval(self.model, request)
        self.assertEqual(mismatch.exception.code, "DRAFT_INPUT_MISMATCH")
        request["input"]["weight_kg"] = "800"
        pending = self.store.create_approval(self.model, request)
        self.assertEqual(pending["approval"]["state"], "PENDING")
        with self.assertRaises(ServiceError):
            self.store.execute(self.executor, self.operation_request(pending))

    def test_creation_is_idempotent_and_payload_conflict_is_rejected(self):
        first = self.create_pending()
        replay = self.store.create_approval(self.model, self.create_request())
        self.assertFalse(first["replayed"])
        self.assertTrue(replay["replayed"])
        self.assertEqual(first["approval"]["approval_id"], replay["approval"]["approval_id"])

        changed = self.create_request(product="另一个合成商品")
        with self.assertRaises(ServiceError) as caught:
            self.store.create_approval(self.model, changed)
        self.assertEqual(caught.exception.code, "IDEMPOTENCY_KEY_REUSED")

    def test_idempotent_create_retry_does_not_reread_changed_source(self):
        first = self.create_pending()
        connection = sqlite3.connect(self.database)
        raw = b'{"name":"second synthetic source","version":"v1","items":["another synthetic item"]}\n'
        connection.execute(
            "INSERT INTO source_versions(tenant_id,source_ref,source_version,raw_bytes,declared_sha256,created_at)"
            " VALUES(?,?,?,?,?,?)",
            (SYNTHETIC_TENANT, "synthetic-source-2", "v1", raw, None, "2026-11-17T00:00:00Z"),
        )
        connection.execute(
            "UPDATE cases SET version=2,source_ref=?,source_version=? WHERE tenant_id=? AND case_id=?",
            ("synthetic-source-2", "v1", SYNTHETIC_TENANT, SYNTHETIC_CASE),
        )
        connection.execute("DELETE FROM source_versions WHERE tenant_id=? AND source_ref=? AND source_version=?",
                           (SYNTHETIC_TENANT, SYNTHETIC_SOURCE, "v1"))
        connection.commit()
        connection.close()
        replay = self.store.create_approval(self.model, self.create_request())
        self.assertTrue(replay["replayed"])
        self.assertEqual(first["approval"]["approval_id"], replay["approval"]["approval_id"])

    def test_roles_and_forged_identity_fields_fail_closed(self):
        with self.assertRaises(ServiceError) as caught:
            self.store.create_approval(self.viewer, self.create_request())
        self.assertEqual(caught.exception.status, 403)
        with self.assertRaises(ServiceError) as caught:
            self.store.create_approval(self.model, {**self.create_request(), "actor_id": "human-a", "confirmed": True})
        self.assertEqual(caught.exception.code, "INVALID_REQUEST")

        pending = self.create_pending()
        with self.assertRaises(ServiceError) as caught:
            self.store.decide(self.model, pending["approval"]["approval_id"], {
                "idempotency_key": "decision-key-0001", "expected_revision": 1,
                "snapshot_hash": pending["approval"]["snapshot_hash"], "decision": "APPROVE",
            })
        self.assertEqual(caught.exception.status, 403)
        with self.assertRaises(ServiceError) as caught:
            self.store.get_approval(self.other_tenant, pending["approval"]["approval_id"])
        self.assertEqual(caught.exception.status, 404)

    def test_requester_cannot_self_approve_and_rejection_survives_restart(self):
        self_requested = self.store.create_approval(self.human, self.create_request(key="human-create-0001"))
        approval_id = self_requested["approval"]["approval_id"]
        with self.assertRaises(ServiceError) as caught:
            self.store.decide(self.human, approval_id, {
                "idempotency_key": "self-decision-0001", "expected_revision": 1,
                "snapshot_hash": self_requested["approval"]["snapshot_hash"], "decision": "APPROVE",
            })
        self.assertEqual(caught.exception.code, "SELF_APPROVAL_FORBIDDEN")

        pending = self.create_pending()
        approval_id = pending["approval"]["approval_id"]
        record = pending["approval"]
        rejected = self.store.decide(self.human, approval_id, {
            "idempotency_key": "decision-key-0001", "expected_revision": record["revision"],
            "snapshot_hash": record["snapshot_hash"], "decision": "REJECT", "reason": "not accepted",
        })
        self.assertEqual(rejected["approval"]["state"], "REJECTED")
        self.store = ApprovalStore(self.database)
        replay = self.store.create_approval(self.model, self.create_request())
        self.assertEqual(replay["approval"]["approval_id"], approval_id)
        self.assertEqual(replay["approval"]["state"], "REJECTED")
        with self.assertRaises(ServiceError) as caught:
            self.store.decide(self.human, approval_id, {
                "idempotency_key": "decision-key-0002", "expected_revision": 1,
                "snapshot_hash": replay["approval"]["snapshot_hash"], "decision": "APPROVE",
            })
        self.assertEqual(caught.exception.code, "TERMINAL_OR_NONPENDING")

    def test_decision_replay_is_idempotent_and_competing_humans_cannot_both_decide(self):
        pending = self.create_pending()
        record = pending["approval"]
        decision = {
            "idempotency_key": "decision-key-0001",
            "expected_revision": record["revision"],
            "snapshot_hash": record["snapshot_hash"],
            "decision": "APPROVE",
            "reason": "synthetic test approval",
        }
        first = self.store.decide(self.human, record["approval_id"], decision)
        replay = self.store.decide(self.human, record["approval_id"], decision)
        self.assertTrue(replay["replayed"])
        self.assertEqual(first["approval"]["decision_record"]["decision_id"], replay["approval"]["decision_record"]["decision_id"])

        other_human = Principal("human-b-token", "human-b", SYNTHETIC_TENANT, "human")
        competing = {**decision, "idempotency_key": "decision-key-0002", "decision": "REJECT"}
        with self.assertRaises(ServiceError) as caught:
            self.store.decide(other_human, record["approval_id"], competing)
        self.assertEqual(caught.exception.code, "TERMINAL_OR_NONPENDING")
        self.assertEqual(self.store.get_approval(self.human, record["approval_id"])["state"], "APPROVED")

        conflicting_replay = {**decision, "decision": "REJECT"}
        with self.assertRaises(ServiceError) as caught:
            self.store.decide(self.human, record["approval_id"], conflicting_replay)
        self.assertEqual(caught.exception.code, "IDEMPOTENCY_KEY_REUSED")

    def test_source_change_invalidates_old_approval_without_an_effect(self):
        pending = self.create_pending()
        approved = self.approve(pending)
        connection = sqlite3.connect(self.database)
        raw = b'{"name":"second synthetic source","version":"v2","items":["different synthetic item"]}\n'
        connection.execute(
            "INSERT INTO source_versions(tenant_id,source_ref,source_version,raw_bytes,declared_sha256,created_at)"
            " VALUES(?,?,?,?,?,?)",
            (SYNTHETIC_TENANT, "synthetic-source-2", "v1", raw, None, "2026-11-17T00:00:00Z"),
        )
        connection.execute(
            "UPDATE cases SET version=2,source_ref=?,source_version=? WHERE tenant_id=? AND case_id=?",
            ("synthetic-source-2", "v1", SYNTHETIC_TENANT, SYNTHETIC_CASE),
        )
        connection.commit()
        connection.close()

        request = self.operation_request(approved)
        with self.assertRaises(ServiceError) as caught:
            self.store.execute(self.executor, request)
        self.assertEqual(caught.exception.code, "APPROVAL_INVALIDATED")
        after = self.store.get_approval(self.human, approved["approval"]["approval_id"])
        self.assertEqual(after["state"], "INVALIDATED")
        self.assertEqual(self.store.get_audit(self.human, "operation", request["operation_id"]), [])
        connection = sqlite3.connect(self.database)
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM effects").fetchone()[0], 0)
        connection.close()

    def test_simulation_is_atomic_persistent_and_replay_does_not_duplicate_effect(self):
        with patch("prototype.approval_service.now_utc", return_value="2026-11-17T00:00:00.000Z"):
            pending = self.create_pending()
            approved = self.approve(pending)
            request = self.operation_request(approved)
            first = self.store.execute(self.executor, request)
        self.assertFalse(first["replayed"])
        self.assertEqual(first["operation"]["status"], "SUCCEEDED")
        self.assertFalse(first["result"]["usable_for_quote"])
        self.assertIsNone(first["result"]["total_payable"])
        self.assertFalse(first["effect"]["external_side_effect"])

        # Reopen the same database to model a process restart after commit but before a response arrived.
        self.store = ApprovalStore(self.database)
        replay = self.store.execute(self.executor, request)
        self.assertTrue(replay["replayed"])
        self.assertEqual(first["result"]["result_id"], replay["result"]["result_id"])
        self.assertEqual(self.store.get_operation(self.human, request["operation_id"])["result"]["result_hash"], first["result"]["result_hash"])
        self.assertEqual(self.store.get_result(self.human, first["result"]["result_id"])["result_hash"], first["result"]["result_hash"])
        self.assertEqual(self.store.get_effect(self.human, first["effect"]["effect_id"])["operation_id"], request["operation_id"])
        self.assertGreaterEqual(len(self.store.get_audit(self.human, "operation", request["operation_id"])), 1)
        approval_events = self.store.get_audit(self.human, "approval", request["approval_id"])
        self.assertEqual(
            [event["event_type"] for event in approval_events],
            ["APPROVAL_CREATED", "HUMAN_DECISION_RECORDED", "APPROVAL_CONSUMED"],
        )

        changed = {**request, "snapshot_hash": "f" * 64}
        with self.assertRaises(ServiceError) as caught:
            self.store.execute(self.executor, changed)
        self.assertEqual(caught.exception.code, "OPERATION_ID_REUSED")
        with self.assertRaises(ServiceError) as caught:
            self.store.execute(self.executor, {**request, "action": "real_quote"})
        self.assertEqual(caught.exception.code, "OPERATION_ID_REUSED")
        with self.assertRaises(ServiceError):
            self.store.execute(self.executor, {**request, "operation_id": "operation-key-0002"})

        connection = sqlite3.connect(self.database)
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM effects").fetchone()[0], 1)
        self.assertEqual(connection.execute("SELECT state FROM approvals").fetchone()[0], "CONSUMED")
        connection.close()

    def test_concurrent_same_create_key_yields_one_approval(self):
        request = self.create_request()
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda _: self.store.create_approval(self.model, request), range(6)))
        ids = {result["approval"]["approval_id"] for result in results}
        self.assertEqual(len(ids), 1)
        connection = sqlite3.connect(self.database)
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM approvals").fetchone()[0], 1)
        connection.close()

    def test_concurrent_same_operation_has_one_effect(self):
        pending = self.create_pending()
        approved = self.approve(pending)
        request = self.operation_request(approved)
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda _: self.store.execute(self.executor, request), range(6)))
        self.assertEqual({result["operation"]["operation_id"] for result in results}, {request["operation_id"]})
        connection = sqlite3.connect(self.database)
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM operations").fetchone()[0], 1)
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM effects").fetchone()[0], 1)
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM results").fetchone()[0], 1)
        connection.close()

    def test_loopback_http_routes_enforce_roles_and_return_authoritative_records(self):
        identities = {principal.token: principal for principal in (self.model, self.human, self.executor, self.viewer, self.other_tenant)}
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.store, identities))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = "http://127.0.0.1:{}/".format(server.server_address[1])

        def request(method, path, principal=None, body=None):
            data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers = {}
            if principal:
                headers["Authorization"] = "Bearer " + principal.token
            if data is not None:
                headers["Content-Type"] = "application/json"
            req = Request(base + path.lstrip("/"), data=data, headers=headers, method=method)
            try:
                with urlopen(req, timeout=3) as response:
                    return response.status, json.loads(response.read().decode("utf-8"))
            except HTTPError as error:
                return error.code, json.loads(error.read().decode("utf-8"))

        try:
            status, health = request("GET", "/health")
            self.assertEqual(status, 200)
            self.assertFalse(health["production_authority"])

            status, _ = request("POST", "/approvals", body=self.create_request())
            self.assertEqual(status, 401)
            status, _ = request("POST", "/approvals", self.executor, self.create_request())
            self.assertEqual(status, 403)
            status, _ = request("POST", "/approvals", self.model, {**self.create_request(), "actor_id": "human-a"})
            self.assertEqual(status, 400)

            status, created = request("POST", "/approvals", self.model, self.create_request())
            self.assertEqual(status, 201)
            approval = created["approval"]
            status, _ = request("GET", "/approvals/{}".format(approval["approval_id"]), self.other_tenant)
            self.assertEqual(status, 404)
            status, decided = request("POST", "/approvals/{}/decisions".format(approval["approval_id"]), self.human, {
                "idempotency_key": "http-decision-0001", "expected_revision": approval["revision"],
                "snapshot_hash": approval["snapshot_hash"], "decision": "APPROVE",
            })
            self.assertEqual(status, 200)
            operation = self.operation_request(decided, "http-operation-0001")
            status, executed = request("POST", "/operations", self.executor, operation)
            self.assertEqual(status, 200)
            self.assertEqual(executed["result"]["status"], "SIMULATED")

            status, fetched = request("GET", "/operations/http-operation-0001", self.viewer)
            self.assertEqual(status, 200)
            self.assertEqual(fetched["result"]["result_id"], executed["result"]["result_id"])
            status, result = request("GET", "/results/{}".format(executed["result"]["result_id"]), self.viewer)
            self.assertEqual(status, 200)
            self.assertIsNone(result["total_payable"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
