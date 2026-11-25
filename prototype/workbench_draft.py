"""Local operator adapter for synthetic workbench cases. Reads one request on stdin."""
import json
import os
import sys
import uuid
from contextlib import closing
from pathlib import Path

from approval_service import (ApprovalStore, Principal, ServiceError, SYNTHETIC_SOURCE,
                              SYNTHETIC_SOURCE_VERSION, SYNTHETIC_TENANT, _connect, now_utc)


def quote_history(db, key):
    """Read completed calculations and their separate review records for one case."""
    rows = db.execute("""
        SELECT trial.case_version, trial.approval_id AS trial_id, trial.state AS trial_state,
               review.approval_id AS review_id, review.state AS review_state, decision.decision,
               result.result_json
        FROM approvals trial
        JOIN operations op ON op.approval_id=trial.approval_id AND op.tenant_id=trial.tenant_id
        JOIN results result ON result.operation_id=op.operation_id AND result.tenant_id=op.tenant_id
        LEFT JOIN approvals review ON review.tenant_id=trial.tenant_id AND review.case_id=trial.case_id
            AND review.case_version=trial.case_version AND review.scope='internal_quote_review'
        LEFT JOIN decisions decision ON decision.approval_id=review.approval_id
        WHERE trial.tenant_id=? AND trial.case_id=? AND trial.scope='classification_trial'
        ORDER BY trial.case_version, trial.rowid
    """, key)
    history = []
    for row in rows:
        quote = json.loads(row["result_json"]).get("quote")
        if quote:
            history.append({**{name: row[name] for name in row.keys() if name != "result_json"},
                            "calculation": quote["calculation"], "source": quote["source"]})
    return history


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
        db.execute("BEGIN")
        case = db.execute("SELECT source_ref,source_version,version FROM cases WHERE tenant_id=? AND case_id=?", key).fetchone()
        if not case:
            if action == "read":
                return {"version": 1, "input": None, "approval": None}
            raise ServiceError(404, "NO_CASE", "请先保存草稿")
        version = case["version"]
        draft = db.execute("SELECT input_json FROM case_drafts WHERE tenant_id=? AND case_id=? AND version=?",
                           (*key, version)).fetchone()
        latest = db.execute("SELECT approval_id FROM approvals WHERE tenant_id=? AND case_id=? ORDER BY rowid DESC LIMIT 1", key).fetchone()
        source = case["source_ref"]
        result = db.execute("SELECT r.result_json FROM results r JOIN operations o ON r.operation_id=o.operation_id AND r.tenant_id=o.tenant_id "
                            "JOIN approvals a ON a.approval_id=o.approval_id WHERE a.tenant_id=? AND a.case_id=? AND a.case_version=? ORDER BY a.rowid DESC LIMIT 1",
                            (*key, version)).fetchone()
        approval = store._approval_view(db, store._scoped_approval(db, human, latest[0])) if latest else None
        history = quote_history(db, key) if action == "read" else []
    values = json.loads(draft[0]) if draft else None
    if action == "read":
        return {"version": version, "input": values, "approval": approval, "rate_source": source,
                "quote_history": history, "quote": json.loads(result[0]).get("quote") if result else None}
    if action == "save":
        store.revise_case(human, case_id, body)
    elif action in ("request", "review"):
        scope = "classification_trial" if action == "request" else "internal_quote_review"
        store.create_approval(requester, {"case": {"id": case_id, "version": body["version"]},
            "input": body["input"], "scope": scope, "source_ref": source,
            "source_version": case["source_version"], "idempotency_key": f"{action}-{case_id}-{body['version']}"})
    elif action == "calculate":
        if not approval or approval["approval_id"] != body["id"] or approval["case"]["version"] != version:
            raise ServiceError(409, "STALE", "草稿已更新")
        store.execute(Principal("", "workbench-calculator", key[0], "executor"), {
            "operation_id": "calc-"+body["id"], "approval_id": body["id"], "expected_approval_revision": body["revision"],
            "case": {"id": case_id, "version": body["version"]}, "snapshot_hash": body["hash"],
            "action": "simulate_classification_trial"})
    elif action in ("approve", "reject"):
        if not approval or approval["approval_id"] != body["id"] or approval["case"]["version"] != version or version != body["version"]:
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
