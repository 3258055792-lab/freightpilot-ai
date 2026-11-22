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
