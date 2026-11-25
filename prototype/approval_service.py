#!/usr/bin/env python3
"""Local-only synthetic approval service; this is not a production authority."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import ipaddress
import json
import os
import re
import sqlite3
import sys
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional, Union
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "prototype" / "output" / "approval-demo.db"
SYNTHETIC_TENANT = "synthetic-demo"
SYNTHETIC_CASE = "case-synthetic-001"
SYNTHETIC_SOURCE = "synthetic-source-1"
SYNTHETIC_SOURCE_VERSION = "v1"
SNAPSHOT_PROTOCOL = "fp02-snapshot-v1"
SIMULATOR_VERSION = "classification-trial-simulator-v1"
MAX_BODY_BYTES = 64 * 1024


class ServiceError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Principal:
    token: str
    subject: str
    tenant_id: str
    role: str


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def require_string(value: Any, name: str, minimum: int = 1, maximum: int = 128) -> str:
    if not isinstance(value, str):
        raise ServiceError(400, "INVALID_REQUEST", f"{name} must be text")
    value = value.strip()
    if not minimum <= len(value) <= maximum or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ServiceError(400, "INVALID_REQUEST", f"{name} has an invalid length or control character")
    return value


def require_positive_int(value: Any, name: str) -> int:
    if type(value) is not int or value < 1:
        raise ServiceError(400, "INVALID_REQUEST", f"{name} must be a positive integer")
    return value


def _check_keys(payload: Any, required: set[str], optional: Optional[set[str]] = None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ServiceError(400, "INVALID_REQUEST", "request body must be a JSON object")
    optional = optional or set()
    keys = set(payload)
    if not required.issubset(keys) or keys - required - optional:
        raise ServiceError(400, "INVALID_REQUEST", "request fields do not match the supported schema")
    return payload


def _connect(path: Union[str, Path]) -> sqlite3.Connection:
    connection = sqlite3.connect(str(path), timeout=10, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 10000")
    connection.execute("PRAGMA synchronous = FULL")
    return connection


class ApprovalStore:
    """SQLite-backed approval authority for synthetic local simulations."""

    def __init__(self, database: Union[str, Path]):
        self.database = Path(database).expanduser().resolve()
        if str(database) == ":memory:":
            raise ValueError("use a temporary file database so restart persistence can be verified")
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def initialize(self) -> None:
        connection = _connect(self.database)
        try:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS source_versions (
                    tenant_id TEXT NOT NULL,
                    source_ref TEXT NOT NULL,
                    source_version TEXT NOT NULL,
                    raw_bytes BLOB NOT NULL,
                    declared_sha256 TEXT,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, source_ref, source_version)
                );
                CREATE TABLE IF NOT EXISTS cases (
                    tenant_id TEXT NOT NULL,
                    case_id TEXT NOT NULL,
                    version INTEGER NOT NULL CHECK(version > 0),
                    source_ref TEXT NOT NULL,
                    source_version TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, case_id),
                    FOREIGN KEY (tenant_id, source_ref, source_version)
                      REFERENCES source_versions(tenant_id, source_ref, source_version)
                );
                CREATE TABLE IF NOT EXISTS case_drafts (
                    tenant_id TEXT NOT NULL, case_id TEXT NOT NULL, version INTEGER NOT NULL,
                    input_json TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, case_id, version),
                    FOREIGN KEY (tenant_id, case_id) REFERENCES cases(tenant_id, case_id)
                );
                CREATE TABLE IF NOT EXISTS approvals (
                    approval_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    requester_principal TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    create_digest TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    case_id TEXT NOT NULL,
                    case_version INTEGER NOT NULL,
                    source_ref TEXT NOT NULL,
                    source_version TEXT NOT NULL,
                    source_bytes_sha256 TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    snapshot_hash TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('PENDING','APPROVED','REJECTED','CONSUMED','INVALIDATED')),
                    revision INTEGER NOT NULL CHECK(revision > 0),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE (tenant_id, requester_principal, idempotency_key)
                );
                CREATE TABLE IF NOT EXISTS decisions (
                    decision_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    approval_id TEXT NOT NULL REFERENCES approvals(approval_id),
                    actor_principal TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    request_digest TEXT NOT NULL,
                    decision TEXT NOT NULL CHECK(decision IN ('APPROVE','REJECT')),
                    expected_revision INTEGER NOT NULL,
                    snapshot_hash TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE (tenant_id, actor_principal, idempotency_key)
                );
                CREATE TABLE IF NOT EXISTS operations (
                    operation_id TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    request_digest TEXT NOT NULL,
                    approval_id TEXT NOT NULL REFERENCES approvals(approval_id),
                    status TEXT NOT NULL CHECK(status = 'SUCCEEDED'),
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, operation_id),
                    UNIQUE (tenant_id, approval_id)
                );
                CREATE TABLE IF NOT EXISTS effects (
                    effect_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    operation_id TEXT NOT NULL,
                    effect_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE (tenant_id, operation_id),
                    FOREIGN KEY (tenant_id, operation_id) REFERENCES operations(tenant_id, operation_id)
                );
                CREATE TABLE IF NOT EXISTS results (
                    result_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    operation_id TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    result_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE (tenant_id, operation_id),
                    FOREIGN KEY (tenant_id, operation_id) REFERENCES operations(tenant_id, operation_id)
                );
                CREATE TABLE IF NOT EXISTS audit_events (
                    event_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    actor_principal TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS audit_entity_idx
                    ON audit_events(tenant_id, entity_type, entity_id, created_at);
                """
            )
        finally:
            connection.close()

    def seed_demo(self) -> None:
        """Insert one immutable, synthetic source and case; never overwrites existing rows."""
        raw = (
            '{"name":"synthetic freight sample","version":"v1",'
            '"items":["合成塑料收纳盒"],"declared_sha256":null}\n'
        ).encode("utf-8")
        timestamp = now_utc()
        connection = _connect(self.database)
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT OR IGNORE INTO source_versions"
                "(tenant_id,source_ref,source_version,raw_bytes,declared_sha256,created_at)"
                " VALUES(?,?,?,?,?,?)",
                (SYNTHETIC_TENANT, SYNTHETIC_SOURCE, SYNTHETIC_SOURCE_VERSION, raw, None, timestamp),
            )
            connection.execute(
                "INSERT OR IGNORE INTO cases"
                "(tenant_id,case_id,version,source_ref,source_version,created_at)"
                " VALUES(?,?,?,?,?,?)",
                (SYNTHETIC_TENANT, SYNTHETIC_CASE, 1, SYNTHETIC_SOURCE, SYNTHETIC_SOURCE_VERSION, timestamp),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _draft_input(payload, *, require_dimensions=False):
        fields = {"weight_kg", "volume_cbm"}
        values = _check_keys(payload, {"product_name"}, fields | {"destination_country"})
        result = {"product_name": unicodedata.normalize("NFC", require_string(values["product_name"], "product_name", 2, 80))}
        if "destination_country" in values:
            result["destination_country"] = require_string(values["destination_country"], "destination_country", 1, 80)
        if require_dimensions or fields.intersection(values):
            for field in sorted(fields):
                value = values.get(field)
                if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,10}(?:[.][0-9]{1,6})?", value) or not value.strip("0."):
                    raise ServiceError(400, "INVALID_REQUEST", f"{field} must be a positive decimal string")
                result[field] = value
        return result

    def revise_case(self, principal, case_id, payload):
        self._require_role(principal, {"human"})
        body = _check_keys(payload, {"expected_version", "input"}, {"rate_source"})
        expected = require_positive_int(body["expected_version"], "expected_version")
        draft = canonical_json(self._draft_input(body["input"], require_dimensions=True))
        key = (principal.tenant_id, case_id)
        connection = _connect(self.database)
        try:
            connection.execute("BEGIN IMMEDIATE")
            case = connection.execute("SELECT version,source_ref,source_version FROM cases WHERE tenant_id=? AND case_id=?", key).fetchone()
            if not case:
                raise ServiceError(404, "CASE_NOT_FOUND", "case not found")
            version = case["version"]
            ref, source_version = case["source_ref"], case["source_version"]
            if "rate_source" in body:
                from sample_quote import FILES, sample_bytes
                ref = body["rate_source"]
                if not isinstance(ref, str) or ref not in FILES:
                    raise ServiceError(400, "INVALID_SOURCE", "选择合成来源 A 或 B")
                raw = sample_bytes(ref)
                source_version = hashlib.sha256(raw).hexdigest()
                connection.execute("INSERT OR IGNORE INTO source_versions VALUES(?,?,?,?,?,?)",
                                   (principal.tenant_id, ref, source_version, raw, source_version, now_utc()))
            if ref in ("A", "B") and body["input"].get("destination_country") not in (None, "巴西"):
                raise ServiceError(400, "INVALID_DESTINATION", "巴西海运样例不适用于所填目的国")
            current = connection.execute("SELECT input_json FROM case_drafts WHERE tenant_id=? AND case_id=? AND version=?", (*key, version)).fetchone()
            if (ref, source_version) == (case["source_ref"], case["source_version"]) and current and current[0] == draft and expected in (version, version - 1):
                connection.commit()
                return {"version": version, "changed": False}
            if version != expected:
                raise ServiceError(409, "CASE_VERSION_STALE", "reload the current case version")
            version += 1
            connection.execute("INSERT INTO case_drafts VALUES(?,?,?,?)", (*key, version, draft))
            connection.execute("UPDATE cases SET version=?,source_ref=?,source_version=? WHERE tenant_id=? AND case_id=?", (version, ref, source_version, *key))
            connection.execute("UPDATE approvals SET state='INVALIDATED',revision=revision+1,updated_at=? WHERE tenant_id=? AND case_id=? AND state IN ('PENDING','APPROVED')", (now_utc(), *key))
            self._audit(connection, principal, "case", case_id, "DRAFT_REVISED", {"version": version})
            connection.commit()
            return {"version": version, "changed": True}
        finally:
            connection.close()

    @staticmethod
    def _normalize_create(payload: Any) -> dict[str, Any]:
        payload = _check_keys(
            payload,
            {"idempotency_key", "case", "scope", "source_ref", "source_version", "input"},
        )
        key = require_string(payload["idempotency_key"], "idempotency_key", 8, 128)
        case = _check_keys(payload["case"], {"id", "version"})
        case_id = require_string(case["id"], "case.id", 1, 64)
        case_version = require_positive_int(case["version"], "case.version")
        scope = require_string(payload["scope"], "scope", 1, 64)
        if scope not in {"classification_trial", "internal_quote_review"}:
            raise ServiceError(400, "UNSUPPORTED_SCOPE", "unsupported approval scope")
        source_ref = require_string(payload["source_ref"], "source_ref", 1, 128)
        source_version = require_string(payload["source_version"], "source_version", 1, 64)
        if source_version.lower() == "latest":
            raise ServiceError(400, "IMMUTABLE_SOURCE_REQUIRED", "source_version must be immutable, not latest")
        return {
            "idempotency_key": key,
            "case": {"id": case_id, "version": case_version},
            "scope": scope,
            "source_ref": source_ref,
            "source_version": source_version,
            "input": ApprovalStore._draft_input(payload["input"]),
        }

    def create_approval(self, principal: Principal, payload: Any) -> dict[str, Any]:
        self._require_role(principal, {"model", "human"})
        request = self._normalize_create(payload)
        digest_body = {
            "protocol": "approval-create-digest-v1",
            "tenant_id": principal.tenant_id,
            "principal_id": principal.subject,
            "case": request["case"],
            "scope": request["scope"],
            "source_ref": request["source_ref"],
            "source_version": request["source_version"],
            "input": request["input"],
        }
        digest = sha256_text(canonical_json(digest_body))
        connection = _connect(self.database)
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM approvals WHERE tenant_id=? AND requester_principal=? AND idempotency_key=?",
                (principal.tenant_id, principal.subject, request["idempotency_key"]),
            ).fetchone()
            if existing:
                connection.commit()
                if existing["create_digest"] != digest:
                    raise ServiceError(409, "IDEMPOTENCY_KEY_REUSED", "idempotency key was already used for a different request")
                return {"replayed": True, "approval": self._approval_view(connection, existing)}

            case = connection.execute(
                "SELECT * FROM cases WHERE tenant_id=? AND case_id=?",
                (principal.tenant_id, request["case"]["id"]),
            ).fetchone()
            if not case:
                raise ServiceError(404, "CASE_NOT_FOUND", "case was not found in this tenant")
            if case["version"] != request["case"]["version"]:
                raise ServiceError(409, "CASE_VERSION_STALE", "case version is no longer current")
            current = connection.execute("SELECT input_json FROM case_drafts WHERE tenant_id=? AND case_id=? AND version=?",
                                         (principal.tenant_id, case["case_id"], case["version"])).fetchone()
            if current and current[0] != canonical_json(request["input"]):
                raise ServiceError(409, "DRAFT_INPUT_MISMATCH", "approval inputs differ from the saved draft")
            if not current and "weight_kg" in request["input"]:
                raise ServiceError(409, "DRAFT_REQUIRED", "save the case draft before requesting approval")
            if (case["source_ref"], case["source_version"]) != (request["source_ref"], request["source_version"]):
                raise ServiceError(409, "SOURCE_NOT_SELECTED", "source does not match the case's current pinned source")
            if case["source_ref"] in ("A", "B"):
                existing_scope = connection.execute(
                    "SELECT 1 FROM approvals WHERE tenant_id=? AND case_id=? AND case_version=? AND scope=?",
                    (principal.tenant_id, case["case_id"], case["version"], request["scope"])).fetchone()
                if existing_scope:
                    raise ServiceError(409, "APPROVAL_EXISTS", "本版已经申请该类审批")
            source = connection.execute(
                "SELECT * FROM source_versions WHERE tenant_id=? AND source_ref=? AND source_version=?",
                (principal.tenant_id, request["source_ref"], request["source_version"]),
            ).fetchone()
            if not source:
                raise ServiceError(404, "SOURCE_NOT_FOUND", "immutable source version was not found")

            source_hash = hashlib.sha256(source["raw_bytes"]).hexdigest()
            snapshot = {
                "protocol": SNAPSHOT_PROTOCOL,
                "scope": request["scope"],
                "case": request["case"],
                "source": {
                    "ref": source["source_ref"],
                    "version": source["source_version"],
                    "bytes_sha256": source_hash,
                    "declared_sha256": source["declared_sha256"],
                },
                "input": request["input"],
                "simulator_version": SIMULATOR_VERSION,
            }
            if request["scope"] == "internal_quote_review":
                prior = connection.execute(
                    "SELECT r.result_json,r.result_hash FROM approvals a JOIN operations o ON o.approval_id=a.approval_id "
                    "JOIN results r ON r.operation_id=o.operation_id AND r.tenant_id=o.tenant_id "
                    "WHERE a.tenant_id=? AND a.case_id=? AND a.case_version=? AND a.source_bytes_sha256=? "
                    "AND a.scope='classification_trial' AND a.state='CONSUMED' ORDER BY a.rowid DESC LIMIT 1",
                    (principal.tenant_id, case["case_id"], case["version"], source_hash)).fetchone()
                if not prior or "quote" not in json.loads(prior[0]):
                    raise ServiceError(409, "QUOTE_REQUIRED", "请先完成当前版本的试算")
                snapshot["quote"] = json.loads(prior[0])["quote"]
                snapshot["trial_result_hash"] = prior[1]
            snapshot_json = canonical_json(snapshot)
            snapshot_hash = sha256_text(snapshot_json)
            timestamp = now_utc()
            approval_id = "approval-" + uuid.uuid4().hex
            connection.execute(
                "INSERT INTO approvals(approval_id,tenant_id,requester_principal,idempotency_key,create_digest,scope,"
                "case_id,case_version,source_ref,source_version,source_bytes_sha256,snapshot_json,snapshot_hash,state,revision,created_at,updated_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'PENDING',1,?,?)",
                (
                    approval_id, principal.tenant_id, principal.subject, request["idempotency_key"], digest,
                    request["scope"], case["case_id"], case["version"], source["source_ref"], source["source_version"],
                    source_hash, snapshot_json, snapshot_hash, timestamp, timestamp,
                ),
            )
            self._audit(connection, principal, "approval", approval_id, "APPROVAL_CREATED", {"create_digest": digest, "snapshot_hash": snapshot_hash})
            row = connection.execute("SELECT * FROM approvals WHERE approval_id=?", (approval_id,)).fetchone()
            view = self._approval_view(connection, row)
            connection.commit()
            return {"replayed": False, "approval": view}
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def decide(self, principal: Principal, approval_id: str, payload: Any) -> dict[str, Any]:
        self._require_role(principal, {"human"})
        payload = _check_keys(payload, {"idempotency_key", "expected_revision", "snapshot_hash", "decision"}, {"reason"})
        key = require_string(payload["idempotency_key"], "idempotency_key", 8, 128)
        expected_revision = require_positive_int(payload["expected_revision"], "expected_revision")
        snapshot_hash = require_string(payload["snapshot_hash"], "snapshot_hash", 64, 64)
        decision = require_string(payload["decision"], "decision", 1, 16).upper()
        if decision not in {"APPROVE", "REJECT"}:
            raise ServiceError(400, "INVALID_DECISION", "decision must be APPROVE or REJECT")
        reason = payload.get("reason", "")
        if not isinstance(reason, str) or len(reason) > 500:
            raise ServiceError(400, "INVALID_REQUEST", "reason must be text of at most 500 characters")
        digest = sha256_text(canonical_json({
            "approval_id": approval_id,
            "expected_revision": expected_revision,
            "snapshot_hash": snapshot_hash,
            "decision": decision,
            "reason": reason,
        }))
        connection = _connect(self.database)
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM decisions WHERE tenant_id=? AND actor_principal=? AND idempotency_key=?",
                (principal.tenant_id, principal.subject, key),
            ).fetchone()
            if existing:
                connection.commit()
                if existing["request_digest"] != digest:
                    raise ServiceError(409, "IDEMPOTENCY_KEY_REUSED", "decision idempotency key was used for a different request")
                approval = connection.execute("SELECT * FROM approvals WHERE approval_id=?", (existing["approval_id"],)).fetchone()
                return {"replayed": True, "approval": self._approval_view(connection, approval)}

            approval = self._scoped_approval(connection, principal, approval_id)
            if approval["requester_principal"] == principal.subject:
                raise ServiceError(403, "SELF_APPROVAL_FORBIDDEN", "the requester cannot approve their own request")
            if approval["state"] != "PENDING":
                raise ServiceError(409, "TERMINAL_OR_NONPENDING", "only a pending approval can be decided")
            if approval["revision"] != expected_revision or approval["snapshot_hash"] != snapshot_hash:
                raise ServiceError(409, "REVISION_OR_SNAPSHOT_MISMATCH", "approval revision or snapshot changed")

            decision_id = "decision-" + uuid.uuid4().hex
            timestamp = now_utc()
            new_state = "APPROVED" if decision == "APPROVE" else "REJECTED"
            connection.execute(
                "INSERT INTO decisions(decision_id,tenant_id,approval_id,actor_principal,idempotency_key,request_digest,decision,"
                "expected_revision,snapshot_hash,reason,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (decision_id, principal.tenant_id, approval_id, principal.subject, key, digest, decision, expected_revision, snapshot_hash, reason, timestamp),
            )
            connection.execute(
                "UPDATE approvals SET state=?,revision=revision+1,updated_at=? WHERE approval_id=? AND state='PENDING' AND revision=?",
                (new_state, timestamp, approval_id, expected_revision),
            )
            self._audit(connection, principal, "approval", approval_id, "HUMAN_DECISION_RECORDED", {"decision_id": decision_id, "decision": decision, "snapshot_hash": snapshot_hash})
            updated = connection.execute("SELECT * FROM approvals WHERE approval_id=?", (approval_id,)).fetchone()
            view = self._approval_view(connection, updated)
            connection.commit()
            return {"replayed": False, "approval": view}
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def execute(self, principal: Principal, payload: Any) -> dict[str, Any]:
        self._require_role(principal, {"executor"})
        payload = _check_keys(payload, {"operation_id", "approval_id", "expected_approval_revision", "case", "snapshot_hash", "action"})
        operation_id = require_string(payload["operation_id"], "operation_id", 8, 128)
        approval_id = require_string(payload["approval_id"], "approval_id", 8, 128)
        expected_revision = require_positive_int(payload["expected_approval_revision"], "expected_approval_revision")
        case = _check_keys(payload["case"], {"id", "version"})
        case_id = require_string(case["id"], "case.id", 1, 64)
        case_version = require_positive_int(case["version"], "case.version")
        snapshot_hash = require_string(payload["snapshot_hash"], "snapshot_hash", 64, 64)
        action = require_string(payload["action"], "action", 1, 64)
        digest = sha256_text(canonical_json({
            "tenant_id": principal.tenant_id,
            "operation_id": operation_id,
            "approval_id": approval_id,
            "expected_approval_revision": expected_revision,
            "case": {"id": case_id, "version": case_version},
            "snapshot_hash": snapshot_hash,
            "action": action,
        }))
        connection = _connect(self.database)
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM operations WHERE tenant_id=? AND operation_id=?",
                (principal.tenant_id, operation_id),
            ).fetchone()
            if existing:
                connection.commit()
                if existing["request_digest"] != digest:
                    raise ServiceError(409, "OPERATION_ID_REUSED", "operation_id was already used for a different request")
                return {"replayed": True, **self._operation_view(connection, existing)}

            if action != "simulate_classification_trial":
                raise ServiceError(400, "UNSUPPORTED_ACTION", "only a synthetic classification-trial simulation is implemented")

            approval = self._scoped_approval(connection, principal, approval_id)
            if approval["scope"] != "classification_trial":
                raise ServiceError(409, "SCOPE_MISMATCH", "草稿审核不能授权试算")
            if approval["state"] != "APPROVED":
                raise ServiceError(409, "APPROVAL_NOT_APPROVED", "only an approved, unconsumed authorization can be executed")
            if approval["revision"] != expected_revision or approval["snapshot_hash"] != snapshot_hash:
                raise ServiceError(409, "REVISION_OR_SNAPSHOT_MISMATCH", "approval revision or snapshot changed")
            if (approval["case_id"], approval["case_version"]) != (case_id, case_version):
                raise ServiceError(409, "CASE_BINDING_MISMATCH", "operation case does not match the approved snapshot")
            current_case = connection.execute(
                "SELECT * FROM cases WHERE tenant_id=? AND case_id=?",
                (principal.tenant_id, case_id),
            ).fetchone()
            if not current_case or current_case["version"] != case_version or (current_case["source_ref"], current_case["source_version"]) != (approval["source_ref"], approval["source_version"]):
                timestamp = now_utc()
                connection.execute("UPDATE approvals SET state='INVALIDATED',revision=revision+1,updated_at=? WHERE approval_id=?", (timestamp, approval_id))
                self._audit(connection, principal, "approval", approval_id, "APPROVAL_INVALIDATED_STALE_CASE", {"operation_id": operation_id})
                connection.commit()
                raise ServiceError(409, "APPROVAL_INVALIDATED", "case or source changed after approval; request a new approval")
            if connection.execute("SELECT 1 FROM operations WHERE tenant_id=? AND approval_id=?", (principal.tenant_id, approval_id)).fetchone():
                raise ServiceError(409, "APPROVAL_ALREADY_CONSUMED", "this approval is already bound to another operation")

            timestamp = now_utc()
            effect_id = "effect-" + uuid.uuid4().hex
            result_id = "result-" + uuid.uuid4().hex
            operation_row = {
                "operation_id": operation_id,
                "request_digest": digest,
                "approval_id": approval_id,
                "status": "SUCCEEDED",
                "action": action,
            }
            connection.execute(
                "INSERT INTO operations(operation_id,tenant_id,request_digest,approval_id,status,created_at) VALUES(?,?,?,?,?,?)",
                (operation_id, principal.tenant_id, digest, approval_id, "SUCCEEDED", timestamp),
            )
            effect = {
                "effect_id": effect_id,
                "operation_id": operation_id,
                "effect_type": "synthetic_classification_trial_authorized",
                "case_id": case_id,
                "case_version": case_version,
                "snapshot_hash": snapshot_hash,
                "usable_for_quote": False,
                "external_side_effect": False,
            }
            effect_json = canonical_json(effect)
            result = {
                "result_id": result_id,
                "operation_id": operation_id,
                "status": "SIMULATED",
                "usable_for_quote": False,
                "total_payable": None,
                "message": "Synthetic local authorization record only; no quote, booking, payment, or external action was performed.",
            }
            if approval["source_ref"] in ("A", "B"):
                from sample_quote import calculate_sample
                raw = connection.execute("SELECT raw_bytes FROM source_versions WHERE tenant_id=? AND source_ref=? AND source_version=?",
                                         (principal.tenant_id, approval["source_ref"], approval["source_version"])).fetchone()[0]
                if hashlib.sha256(raw).hexdigest() != approval["source_bytes_sha256"]:
                    raise ServiceError(409, "SOURCE_CHANGED", "运价原件与审批快照不一致")
                result["quote"] = calculate_sample(approval["source_ref"], raw, json.loads(approval["snapshot_json"])["input"])
                result["message"] = "已生成合成内部报价草稿，等待另行审核。"
            result_json = canonical_json(result)
            result_hash = sha256_text(result_json)
            connection.execute(
                "INSERT INTO effects(effect_id,tenant_id,operation_id,effect_json,created_at) VALUES(?,?,?,?,?)",
                (effect_id, principal.tenant_id, operation_id, effect_json, timestamp),
            )
            connection.execute(
                "INSERT INTO results(result_id,tenant_id,operation_id,result_json,result_hash,created_at) VALUES(?,?,?,?,?,?)",
                (result_id, principal.tenant_id, operation_id, result_json, result_hash, timestamp),
            )
            connection.execute(
                "UPDATE approvals SET state='CONSUMED',revision=revision+1,updated_at=? WHERE approval_id=? AND state='APPROVED' AND revision=?",
                (timestamp, approval_id, expected_revision),
            )
            self._audit(connection, principal, "operation", operation_id, "SIMULATED_EFFECT_AND_RESULT_COMMITTED", {"approval_id": approval_id, "effect_id": effect_id, "result_id": result_id, "result_hash": result_hash})
            self._audit(connection, principal, "approval", approval_id, "APPROVAL_CONSUMED", {"operation_id": operation_id})
            view = {
                "operation": operation_row,
                "effect": effect,
                "result": {**result, "result_hash": result_hash},
            }
            connection.commit()
            return {"replayed": False, **view}
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def get_approval(self, principal: Principal, approval_id: str) -> dict[str, Any]:
        connection = _connect(self.database)
        try:
            row = self._scoped_approval(connection, principal, approval_id)
            if principal.role == "model" and row["requester_principal"] != principal.subject:
                raise ServiceError(404, "NOT_FOUND", "record was not found")
            return self._approval_view(connection, row)
        finally:
            connection.close()

    def get_operation(self, principal: Principal, operation_id: str) -> dict[str, Any]:
        connection = _connect(self.database)
        try:
            row = connection.execute(
                "SELECT * FROM operations WHERE tenant_id=? AND operation_id=?",
                (principal.tenant_id, operation_id),
            ).fetchone()
            if not row:
                raise ServiceError(404, "NOT_FOUND", "record was not found")
            if principal.role == "model":
                approval = connection.execute("SELECT requester_principal FROM approvals WHERE approval_id=?", (row["approval_id"],)).fetchone()
                if not approval or approval["requester_principal"] != principal.subject:
                    raise ServiceError(404, "NOT_FOUND", "record was not found")
            return self._operation_view(connection, row)
        finally:
            connection.close()

    def get_result(self, principal: Principal, result_id: str) -> dict[str, Any]:
        connection = _connect(self.database)
        try:
            row = connection.execute("SELECT * FROM results WHERE tenant_id=? AND result_id=?", (principal.tenant_id, result_id)).fetchone()
            if not row:
                raise ServiceError(404, "NOT_FOUND", "record was not found")
            return {**json.loads(row["result_json"]), "result_hash": row["result_hash"]}
        finally:
            connection.close()

    def get_effect(self, principal: Principal, effect_id: str) -> dict[str, Any]:
        connection = _connect(self.database)
        try:
            row = connection.execute("SELECT * FROM effects WHERE tenant_id=? AND effect_id=?", (principal.tenant_id, effect_id)).fetchone()
            if not row:
                raise ServiceError(404, "NOT_FOUND", "record was not found")
            return json.loads(row["effect_json"])
        finally:
            connection.close()

    def get_audit(self, principal: Principal, entity_type: str, entity_id: str) -> list[dict[str, Any]]:
        connection = _connect(self.database)
        try:
            rows = connection.execute(
                "SELECT * FROM audit_events WHERE tenant_id=? AND entity_type=? AND entity_id=? ORDER BY rowid",
                (principal.tenant_id, entity_type, entity_id),
            ).fetchall()
            return [{"event_id": row["event_id"], "entity_type": row["entity_type"], "entity_id": row["entity_id"],
                     "event_type": row["event_type"], "actor_principal": row["actor_principal"],
                     "payload": json.loads(row["payload_json"]), "created_at": row["created_at"]} for row in rows]
        finally:
            connection.close()

    def _approval_view(self, connection: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
        decision = connection.execute("SELECT * FROM decisions WHERE approval_id=? ORDER BY created_at DESC LIMIT 1", (row["approval_id"],)).fetchone()
        return {
            "approval_id": row["approval_id"],
            "tenant_id": row["tenant_id"],
            "requester_principal": row["requester_principal"],
            "create_digest": row["create_digest"],
            "scope": row["scope"],
            "case": {"id": row["case_id"], "version": row["case_version"]},
            "source": {"ref": row["source_ref"], "version": row["source_version"], "bytes_sha256": row["source_bytes_sha256"]},
            "snapshot": json.loads(row["snapshot_json"]),
            "snapshot_hash": row["snapshot_hash"],
            "state": row["state"],
            "revision": row["revision"],
            "decision_record": None if not decision else {
                "decision_id": decision["decision_id"], "actor_type": "human", "actor_id": decision["actor_principal"],
                "decision": decision["decision"], "snapshot_hash": decision["snapshot_hash"],
                "reason": decision["reason"], "created_at": decision["created_at"],
            },
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _operation_view(self, connection: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
        effect = connection.execute("SELECT effect_json FROM effects WHERE tenant_id=? AND operation_id=?", (row["tenant_id"], row["operation_id"])).fetchone()
        result = connection.execute("SELECT result_json,result_hash FROM results WHERE tenant_id=? AND operation_id=?", (row["tenant_id"], row["operation_id"])).fetchone()
        return {
            "operation": {"operation_id": row["operation_id"], "request_digest": row["request_digest"],
                          "approval_id": row["approval_id"], "status": row["status"], "created_at": row["created_at"]},
            "effect": None if not effect else json.loads(effect["effect_json"]),
            "result": None if not result else {**json.loads(result["result_json"]), "result_hash": result["result_hash"]},
        }

    @staticmethod
    def _scoped_approval(connection: sqlite3.Connection, principal: Principal, approval_id: str) -> sqlite3.Row:
        row = connection.execute("SELECT * FROM approvals WHERE tenant_id=? AND approval_id=?", (principal.tenant_id, approval_id)).fetchone()
        if not row:
            raise ServiceError(404, "NOT_FOUND", "record was not found")
        return row

    @staticmethod
    def _require_role(principal: Principal, roles: set[str]) -> None:
        if principal.role not in roles:
            raise ServiceError(403, "FORBIDDEN", "this identity is not permitted to perform this action")

    @staticmethod
    def _audit(connection: sqlite3.Connection, principal: Principal, entity_type: str, entity_id: str,
               event_type: str, payload: dict[str, Any]) -> None:
        connection.execute(
            "INSERT INTO audit_events(event_id,tenant_id,entity_type,entity_id,event_type,actor_principal,payload_json,created_at)"
            " VALUES(?,?,?,?,?,?,?,?)",
            ("event-" + uuid.uuid4().hex, principal.tenant_id, entity_type, entity_id, event_type,
             principal.subject, canonical_json(payload), now_utc()),
        )


def identities_from_environment() -> dict[str, Principal]:
    roles = {
        "FP_APPROVAL_MODEL_TOKEN": ("model-test", "model"),
        "FP_APPROVAL_HUMAN_TOKEN": ("human-test", "human"),
        "FP_APPROVAL_EXECUTOR_TOKEN": ("executor-test", "executor"),
        "FP_APPROVAL_VIEWER_TOKEN": ("viewer-test", "viewer"),
    }
    identities: dict[str, Principal] = {}
    for env_name, (subject, role) in roles.items():
        token = os.environ.get(env_name, "").strip()
        if not token:
            raise SystemExit(f"Set {env_name} to a random local test value before starting the service.")
        if token in identities:
            raise SystemExit("Each approval test identity must use a different token.")
        identities[token] = Principal(token, subject, SYNTHETIC_TENANT, role)
    return identities


def make_handler(store: ApprovalStore, identities: dict[str, Principal]):
    class Handler(BaseHTTPRequestHandler):
        server_version = "FreightPilotSyntheticApproval/0.1"

        def log_message(self, _format: str, *args: Any) -> None:
            # Avoid writing request bodies or auth headers to console/logs.
            return

        def _send(self, status: int, body: dict[str, Any]) -> None:
            raw = canonical_json(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(raw)

        def _principal(self) -> Principal:
            header = self.headers.get("Authorization", "")
            if not header.startswith("Bearer "):
                raise ServiceError(401, "UNAUTHENTICATED", "a bearer token is required")
            token = header[7:].strip()
            for candidate, principal in identities.items():
                if hmac.compare_digest(token, candidate):
                    return principal
            raise ServiceError(401, "UNAUTHENTICATED", "the bearer token is not recognized")

        def _body(self) -> Any:
            if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
                raise ServiceError(415, "UNSUPPORTED_MEDIA_TYPE", "use application/json")
            try:
                size = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                raise ServiceError(400, "INVALID_LENGTH", "invalid Content-Length")
            if size < 1 or size > MAX_BODY_BYTES:
                raise ServiceError(413, "BODY_TOO_LARGE_OR_EMPTY", "JSON body must be between 1 byte and 64 KiB")
            try:
                return json.loads(self.rfile.read(size).decode("utf-8"), parse_constant=lambda _: (_ for _ in ()).throw(ValueError("invalid number")))
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
                raise ServiceError(400, "INVALID_JSON", "body must be valid UTF-8 JSON")

        def _dispatch(self, method: str) -> None:
            try:
                parsed = urlsplit(self.path)
                if parsed.query or parsed.fragment:
                    raise ServiceError(400, "INVALID_PATH", "query strings are not supported")
                parts = [part for part in parsed.path.split("/") if part]
                if method == "GET" and parts == ["health"]:
                    self._send(200, {"status": "ok", "environment": "synthetic-local", "production_authority": False})
                    return
                principal = self._principal()
                if method == "POST" and len(parts) == 3 and parts[0] == "cases" and parts[2] == "draft":
                    self._send(200, store.revise_case(principal, parts[1], self._body()))
                    return
                if method == "POST" and parts == ["approvals"]:
                    result = store.create_approval(principal, self._body())
                    self._send(200 if result["replayed"] else 201, result)
                    return
                if method == "POST" and len(parts) == 3 and parts[0] == "approvals" and parts[2] == "decisions":
                    result = store.decide(principal, parts[1], self._body())
                    self._send(200, result)
                    return
                if method == "POST" and parts == ["operations"]:
                    result = store.execute(principal, self._body())
                    self._send(200, result)
                    return
                if method == "GET" and len(parts) == 2 and parts[0] == "approvals":
                    self._send(200, store.get_approval(principal, parts[1]))
                    return
                if method == "GET" and len(parts) == 2 and parts[0] == "operations":
                    self._send(200, store.get_operation(principal, parts[1]))
                    return
                if method == "GET" and len(parts) == 2 and parts[0] == "results":
                    self._send(200, store.get_result(principal, parts[1]))
                    return
                if method == "GET" and len(parts) == 2 and parts[0] == "effects":
                    self._send(200, store.get_effect(principal, parts[1]))
                    return
                if method == "GET" and len(parts) == 3 and parts[0] == "audit":
                    self._send(200, {"events": store.get_audit(principal, parts[1], parts[2])})
                    return
                raise ServiceError(404, "NOT_FOUND", "route was not found")
            except ServiceError as error:
                self._send(error.status, {"error": {"code": error.code, "message": error.message}})
            except sqlite3.IntegrityError:
                self._send(409, {"error": {"code": "CONFLICT", "message": "a conflicting record already exists"}})
            except Exception:
                self._send(500, {"error": {"code": "INTERNAL_ERROR", "message": "request failed; inspect local development diagnostics"}})

        def do_GET(self) -> None:
            self._dispatch("GET")

        def do_POST(self) -> None:
            self._dispatch("POST")

    return Handler


def serve(database: Path, host: str, port: int) -> None:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        raise SystemExit("host must be a loopback IP address")
    if not address.is_loopback:
        raise SystemExit("this synthetic prototype only binds to loopback; no LAN or public exposure")
    store = ApprovalStore(database)
    store.seed_demo()
    identities = identities_from_environment()
    server = ThreadingHTTPServer((host, port), make_handler(store, identities))
    server.daemon_threads = True
    print(f"Synthetic approval service listening at http://{host}:{port}; Ctrl+C stops it.", flush=True)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        print("Stopping synthetic approval service.", flush=True)
    finally:
        server.server_close()


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Loopback-only synthetic FP-02 approval service")
    subparsers = parser.add_subparsers(dest="command", required=True)
    init_parser = subparsers.add_parser("init-demo", help="create schema and one synthetic source/case")
    init_parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    serve_parser = subparsers.add_parser("serve", help="serve the local synthetic API")
    serve_parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    if args.command == "init-demo":
        store = ApprovalStore(args.db)
        store.seed_demo()
        print(f"Initialized synthetic database: {store.database}")
        return 0
    if not 1 <= args.port <= 65535:
        raise SystemExit("port must be 1–65535")
    serve(args.db, args.host, args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
