"""Run synthetic HTTP checks against a fixed candidate in a separate output directory."""
import argparse
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("candidate", type=Path, help="Extracted FP-02 commit 3e5b341 candidate")
parser.add_argument("output", type=Path, help="Separate evidence directory without an existing database")
args = parser.parse_args()
CANDIDATE = args.candidate.resolve()
ROOT = args.output.resolve()
DATABASE = ROOT / "independent.sqlite3"
EXPECTED_SERVICE_SHA = "ba47305156ffdcac79713709249c8d8bad70d8b067b4e0b29cb7f54f577d04b6"
if not __debug__:
    parser.error("Assertions must be enabled")
if ROOT == CANDIDATE or CANDIDATE in ROOT.parents:
    parser.error("Output must be outside the candidate directory")
if DATABASE.exists():
    parser.error("Output already contains independent.sqlite3; use a new directory")
service_file = CANDIDATE / "prototype" / "approval_service.py"
if not service_file.is_file() or hashlib.sha256(service_file.read_bytes()).hexdigest() != EXPECTED_SERVICE_SHA:
    parser.error("Candidate service does not match the fixed implementation SHA-256")
(ROOT / "temp").mkdir(parents=True, exist_ok=True)
EVIDENCE = {"commit": "3e5b3416d8576dd30bcaba557c1a95cd2432ece6", "service_sha256": EXPECTED_SERVICE_SHA, "requests": [], "groups": [], "service_starts": 0}
TOKENS = {role: secrets.token_urlsafe(32) for role in ("MODEL", "HUMAN", "EXECUTOR", "VIEWER")}
LOCK = threading.Lock()
PROCESS = None
LOG = None
with socket.socket() as available:
    available.bind(("127.0.0.1", 0))
    PORT = available.getsockname()[1]
BASE = f"http://127.0.0.1:{PORT}"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def compact_response(response):
    """Keep business identifiers and outcomes, excluding verbose snapshots and diagnostics."""
    fields = ("replayed", "environment", "production_authority", "approval_id", "revision", "state", "case", "source", "snapshot_hash", "decision_record", "operation_id", "request_digest", "status", "effect_id", "result_id", "result_hash", "usable_for_quote", "total_payable", "external_side_effect")
    summary = {key: response[key] for key in fields if key in response}
    for key in ("approval", "operation", "effect", "result"):
        if key in response:
            summary[key] = compact_response(response[key])
    if "error" in response:
        summary["error_code"] = response["error"]["code"]
    if "events" in response:
        summary["events"] = [{key: event[key] for key in ("event_type", "actor_principal", "payload")} for event in response["events"]]
    return summary


def request(method, path, role="VIEWER", body=None):
    headers = {}
    if role:
        headers["Authorization"] = "Bearer " + TOKENS[role]
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json; charset=utf-8"
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    opener = build_opener(ProxyHandler({}), NoRedirect())
    req = Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with opener.open(req, timeout=10) as response:
            status, result = response.status, json.load(response)
    except HTTPError as error:
        with error:
            status, result = error.code, json.load(error)
    with LOCK:
        EVIDENCE["requests"].append({"method": method, "path": path, "role": role, "body": body, "status": status, "response": compact_response(result)})
    return status, result


def start():
    global PROCESS, LOG
    env = os.environ.copy()
    env.update({"FP_APPROVAL_" + role + "_TOKEN": token for role, token in TOKENS.items()})
    env.update({"TEMP": str(ROOT / "temp"), "TMP": str(ROOT / "temp"), "PYTHONDONTWRITEBYTECODE": "1"})
    command = [sys.executable, "-B", "prototype/approval_service.py", "serve", "--db", str(DATABASE), "--host", "127.0.0.1", "--port", str(PORT)]
    LOG = (ROOT / "service-output.txt").open("ab")
    PROCESS = subprocess.Popen(command, cwd=CANDIDATE, env=env, stdin=subprocess.DEVNULL, stdout=LOG, stderr=subprocess.STDOUT, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    EVIDENCE["service_starts"] += 1
    for _ in range(100):
        if PROCESS.poll() is not None:
            raise AssertionError("Service stopped before health check")
        try:
            status, health = request("GET", "/health", role=None)
            assert status == 200 and health["environment"] == "synthetic-local"
            return
        except URLError:
            time.sleep(0.1)
    raise AssertionError("Service readiness timed out")


def stop():
    global PROCESS, LOG
    if PROCESS is not None:
        PROCESS.terminate()
        PROCESS.wait(timeout=10)
        PROCESS = None
    if LOG is not None:
        LOG.close()
        LOG = None


def create(key):
    body = {"idempotency_key": key, "case": {"id": "case-synthetic-001", "version": 1}, "scope": "classification_trial", "source_ref": "synthetic-source-1", "source_version": "v1", "input": {"product_name": "合成塑料收纳盒"}}
    status, response = request("POST", "/approvals", "MODEL", body)
    assert status == 201, response
    return body, response["approval"]


def decision(approval, choice="APPROVE", role="HUMAN", extra=None):
    body = {"idempotency_key": "decision-" + approval["approval_id"], "expected_revision": approval["revision"], "snapshot_hash": approval["snapshot_hash"], "decision": choice, "reason": "独立合成验收"}
    body.update(extra or {})
    return request("POST", "/approvals/" + approval["approval_id"] + "/decisions", role, body)


def operation(approval, operation_id):
    return {"operation_id": operation_id, "approval_id": approval["approval_id"], "expected_approval_revision": approval["revision"], "case": approval["case"], "snapshot_hash": approval["snapshot_hash"], "action": "simulate_classification_trial"}


def counts():
    with sqlite3.connect(DATABASE.as_uri() + "?mode=ro", uri=True) as db:
        return {table: db.execute("SELECT count(*) FROM " + table).fetchone()[0] for table in ("approvals", "decisions", "operations", "effects", "results")}


def passed(name, details):
    EVIDENCE["groups"].append({"name": name, "status": "PASS", "details": details})
    print("PASS", name, flush=True)


def main():
    assert not DATABASE.exists(), "Use a new isolated database for this script"
    start()
    create_body, pending = create("independent-role-0001")
    assert request("GET", "/approvals/" + pending["approval_id"], role=None)[0] == 401
    assert decision(pending, role="MODEL")[0] == 403
    assert decision(pending, role="VIEWER")[0] == 403
    assert decision(pending, extra={"actor_id": "human-test", "confirmed": True})[0] == 400
    status, unchanged = request("GET", "/approvals/" + pending["approval_id"])
    assert status == 200 and unchanged["state"] == "PENDING"
    assert counts()["decisions"] == 0 and counts()["effects"] == 0
    passed("角色与伪造字段", {"state": unchanged["state"], "counts": counts()})

    status, replay = request("POST", "/approvals", "MODEL", create_body)
    assert status == 200 and replay["replayed"] and replay["approval"]["approval_id"] == pending["approval_id"]
    conflict_body = {**create_body, "input": {"product_name": "另一合成商品"}}
    status, conflict = request("POST", "/approvals", "MODEL", conflict_body)
    assert status == 409 and conflict["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    passed("创建重放与载荷冲突", {"approval_id": pending["approval_id"], "counts": counts()})

    status, approved = decision(pending)
    assert status == 200
    approved = approved["approval"]
    first_op = operation(approved, "independent-race-operation-0001")
    second_op = operation(approved, "independent-race-operation-0002")
    barrier = threading.Barrier(2)
    def compete(body):
        barrier.wait()
        return request("POST", "/operations", "EXECUTOR", body)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(compete, body) for body in (first_op, second_op)]
        results = [future.result() for future in futures]
    assert sorted(status for status, _ in results) == [200, 409], results
    winner_index = next(index for index, result in enumerate(results) if result[0] == 200)
    winning_body = (first_op, second_op)[winner_index]
    saved = results[winner_index][1]
    assert saved["result"]["usable_for_quote"] is False and saved["result"]["total_payable"] is None
    stop()
    start()
    status, replay = request("POST", "/operations", "EXECUTOR", winning_body)
    assert status == 200 and replay["replayed"]
    assert replay["result"]["result_id"] == saved["result"]["result_id"]
    assert replay["result"]["result_hash"] == saved["result"]["result_hash"]
    for resource, identifier in (("operations", winning_body["operation_id"]), ("effects", saved["effect"]["effect_id"]), ("results", saved["result"]["result_id"])):
        assert request("GET", "/" + resource + "/" + identifier)[0] == 200
    assert request("GET", "/audit/approval/" + pending["approval_id"])[0] == 200
    changed_op = {**winning_body, "snapshot_hash": "f" * 64}
    status, conflict = request("POST", "/operations", "EXECUTOR", changed_op)
    assert status == 409 and conflict["error"]["code"] == "OPERATION_ID_REUSED"
    tally = counts()
    assert tally["operations"] == tally["effects"] == tally["results"] == 1
    passed("不同操作并发与进程重启后的持久重放", {"winning_operation": winning_body["operation_id"], "result_hash": saved["result"]["result_hash"], "counts": tally})

    reject_body, pending_reject = create("independent-reject-0001")
    status, rejected = decision(pending_reject, choice="REJECT")
    assert status == 200 and rejected["approval"]["state"] == "REJECTED"
    stop()
    start()
    status, rejected_replay = request("POST", "/approvals", "MODEL", reject_body)
    assert status == 200 and rejected_replay["approval"]["state"] == "REJECTED"
    assert rejected_replay["approval"]["approval_id"] == pending_reject["approval_id"]
    for suffix in ("old", "new"):
        rejected_op = operation(rejected_replay["approval"], "independent-rejected-operation-" + suffix)
        status, denied = request("POST", "/operations", "EXECUTOR", rejected_op)
        assert status == 409 and denied["error"]["code"] == "APPROVAL_NOT_APPROVED"
    status, denied = decision(rejected_replay["approval"])
    assert status == 409
    assert counts()["effects"] == 1
    passed("拒绝后真实进程重启与重试", {"approval_id": pending_reject["approval_id"], "state": "REJECTED", "counts": counts()})

    _, stale_pending = create("independent-stale-0001")
    status, stale_approved = decision(stale_pending)
    assert status == 200
    source_bytes = b'{"source":"independent-synthetic-source-2","version":"v2","synthetic":true}\n'
    stop()
    with sqlite3.connect(DATABASE) as db:
        db.execute("INSERT INTO source_versions(tenant_id,source_ref,source_version,raw_bytes,declared_sha256,created_at) VALUES(?,?,?,?,?,?)", ("synthetic-demo", "independent-synthetic-source-2", "v2", source_bytes, None, "2026-11-20T00:00:00Z"))
        db.execute("UPDATE cases SET version=2,source_ref=?,source_version=? WHERE tenant_id=? AND case_id=?", ("independent-synthetic-source-2", "v2", "synthetic-demo", "case-synthetic-001"))
    start()
    for suffix in ("first", "retry"):
        stale_op = operation(stale_approved["approval"], "independent-stale-operation-" + suffix)
        status, denied = request("POST", "/operations", "EXECUTOR", stale_op)
        assert status == 409
        expected_code = "APPROVAL_INVALIDATED" if suffix == "first" else "APPROVAL_NOT_APPROVED"
        assert denied["error"]["code"] == expected_code
    status, invalidated = request("GET", "/approvals/" + stale_pending["approval_id"])
    assert status == 200 and invalidated["state"] == "INVALIDATED"
    assert counts()["effects"] == 1
    passed("来源和案例版本变更失效", {"approval_id": stale_pending["approval_id"], "state": "INVALIDATED", "fixture_source_sha256": hashlib.sha256(source_bytes).hexdigest(), "fixture_method": "service stopped; isolated seed DB updated using known schema", "counts": counts()})


try:
    main()
    EVIDENCE["status"] = "PASS"
except Exception as error:
    EVIDENCE["status"] = "FAIL"
    EVIDENCE["failure"] = {"type": type(error).__name__}
    raise
finally:
    stop()
    EVIDENCE["service_stopped"] = PROCESS is None
    EVIDENCE["http_observations"] = len(EVIDENCE["requests"])
    (ROOT / "backend-summary.json").write_text(json.dumps(EVIDENCE, ensure_ascii=False, indent=2), encoding="utf-8")
