import tempfile
import unittest
import uuid
from pathlib import Path

from approval_service import ApprovalStore, ServiceError
from workbench_draft import dispatch


class SampleQuoteReviewTest(unittest.TestCase):
    def test_switch_back_and_reject_retries(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ApprovalStore(Path(tmp) / "cases.db")
            store.seed_demo()
            case = str(uuid.uuid4())
            values = {"product_name":"塑料收纳盒", "weight_kg":"400", "volume_cbm":"3"}
            def call(action, **payload):
                return dispatch(store, action, case, payload)
            def decision_args(draft):
                a = draft["approval"]
                return dict(id=a["approval_id"], version=draft["version"], hash=a["snapshot_hash"], revision=a["revision"])
            version = 1
            previous = None
            for source, expected in [("A","2460.00"), ("B","2280.00"), ("A","2460.00")]:
                draft = call("save", input=values, expected_version=version, rate_source=source)
                version = draft["version"]
                self.assertIsNone(draft["quote"])
                if previous:
                    self.assertEqual(draft["approval"]["state"], "INVALIDATED")
                    with self.assertRaises(ServiceError):
                        call("approve", **previous)
                request = dict(input=values, version=version)
                with self.assertRaises(ServiceError):
                    call("review", **request)
                trial = call("request", **request)
                trial = call("approve", **decision_args(trial))
                calculated = call("calculate", **decision_args(trial))
                self.assertEqual(calculated["quote"]["base_freight_usd"], expected)
                review = call("review", **request)
                self.assertEqual(review["approval"]["snapshot"]["quote"], calculated["quote"])
                previous = decision_args(review)
                if version < 4:
                    call("approve", **previous)
                else:
                    call("reject", **previous)
                    self.assertEqual(call("review", **request)["approval"]["state"], "REJECTED")
                    with self.assertRaises(ServiceError):
                        call("approve", **previous)
                    with self.assertRaises(ServiceError):
                        call("calculate", **previous)
            history = call("read")["quote_history"]
            self.assertEqual([row["source"]["file"] for row in history],
                             ["rate-A-v1.xlsx", "rate-B-v1.xlsx", "rate-A-v1.xlsx"])
            self.assertEqual([row["review_state"] for row in history], ["INVALIDATED","INVALIDATED","REJECTED"])
            self.assertEqual([row["decision"] for row in history], ["APPROVE","APPROVE","REJECT"])
            self.assertEqual({row["trial_state"] for row in history}, {"CONSUMED"})
            self.assertEqual(len({row[key] for row in history for key in ("trial_id","review_id")}), 6)

    def test_destination_confirmation_revises_approved_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ApprovalStore(Path(tmp) / "cases.db")
            store.seed_demo()
            case = str(uuid.uuid4())
            values = {"product_name": "塑料收纳盒", "volume_cbm": "3", "weight_kg": "400"}

            def call(action, **body):
                return dispatch(store, action, case, body)

            def decision(draft):
                approval = draft["approval"]
                return dict(id=approval["approval_id"], version=draft["version"],
                            hash=approval["snapshot_hash"], revision=approval["revision"])

            def reviewed(version):
                request = dict(version=version, input=values)
                trial = call("request", **request)
                approved = call("approve", **decision(trial))
                quote = call("calculate", **decision(approved))
                self.assertEqual(quote["quote"]["base_freight_usd"], "2460.00")
                return call("review", **request)

            original = call("save", input=values, expected_version=1, rate_source="A")
            old = reviewed(original["version"])
            call("approve", **decision(old))
            values["destination_country"] = "巴西"
            updated = call("save", input=values, expected_version=old["version"], rate_source="A")
            self.assertEqual(updated["version"], old["version"] + 1)
            self.assertIsNone(updated["quote"])
            self.assertEqual(updated["approval"]["state"], "INVALIDATED")
            with self.assertRaises(ServiceError):
                call("approve", **decision(old))
            new = reviewed(updated["version"])
            self.assertEqual(new["approval"]["snapshot"]["input"]["destination_country"], "巴西")
            self.assertNotEqual(decision(old)["id"], decision(new)["id"])
            self.assertEqual(new["approval"]["state"], "PENDING")
