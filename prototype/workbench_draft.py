"""Local operator adapter for synthetic workbench cases. Reads one request on stdin."""
import json
import os
import sys
import uuid
from contextlib import closing
from pathlib import Path

from approval_service import (ApprovalStore, Principal, ServiceError, SYNTHETIC_SOURCE,
                              SYNTHETIC_SOURCE_VERSION, SYNTHETIC_TENANT, _connect, now_utc)


def dispatch(store, action, case_id, body):
    case_id = str(uuid.UUID(case_id))
    key = (SYNTHETIC_TENANT, case_id)
    human = Principal("", "workbench-operator", key[0], "human")
    requester = Principal("", "workbench-requester", key[0], "model")
    with closing(_connect(store.database)) as db:
        if action == "save":
            store._draft_input(body.get("input"), require_dimensions=True)
            db.execute("INSERT OR IGNORE INTO cases VALUES(?,?,?,?,?,?)",
                       (*key, 1, SYNTHETIC_SOURCE, SYNTHETIC_SOURCE_VERSION, now_utc()))
        case = db.execute("SELECT version FROM cases WHERE tenant_id=? AND case_id=?", key).fetchone()
        if not case:
            if action == "read":
                return {"version": 1, "input": None, "approval": None}
            raise ServiceError(404, "NO_CASE", "请先保存草稿")
        version = case["version"]
        draft = db.execute("SELECT input_json FROM case_drafts WHERE tenant_id=? AND case_id=? AND version=?",
                           (*key, version)).fetchone()
        latest = db.execute("SELECT approval_id FROM approvals WHERE tenant_id=? AND case_id=? ORDER BY rowid DESC LIMIT 1", key).fetchone()
    values = json.loads(draft[0]) if draft else None
    approval = store.get_approval(human, latest[0]) if latest else None
    if action == "read":
        return {"version": version, "input": values, "approval": approval}
    if action == "save":
        store.revise_case(human, case_id, body)
    elif action == "request":
        store.create_approval(requester, {"case": {"id": case_id, "version": body["version"]},
            "input": body["input"], "scope": "classification_trial", "source_ref": SYNTHETIC_SOURCE,
            "source_version": SYNTHETIC_SOURCE_VERSION, "idempotency_key": f"request-{case_id}-{body['version']}"})
    elif action in ("approve", "reject"):
        if not approval or approval["approval_id"] != body["id"] or approval["case"]["version"] != body["version"]:
            raise ServiceError(409, "STALE", "审批已变化，请重新读取")
        store.decide(human, body["id"], {"decision": "APPROVE" if action == "approve" else "REJECT",
            "expected_revision": body["revision"], "snapshot_hash": body["hash"],
            "idempotency_key": f"{action}-{body['id']}", "reason": "工作台操作员决定（本机模拟）"})
    else:
        raise ValueError("未知操作")
    return dispatch(store, "read", case_id, {})


if __name__ == "__main__":
    try:
        store = ApprovalStore(os.getenv("FP_WORKBENCH_DB") or Path(__file__).parent / "output/workbench-approvals.db")
        store.seed_demo()
        request = json.load(sys.stdin)
        result = dispatch(store, request["action"], request["case"], request["body"])
        print(json.dumps(result))
    except (ValueError, KeyError, TypeError, ServiceError) as error:
        print(json.dumps({"error": str(error), "status": getattr(error, "status", 400)}))
