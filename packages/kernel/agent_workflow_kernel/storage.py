"""SQLite ledger for durable workflow kernel state."""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

from .contracts import (
    AdapterInvocation,
    ArtifactRef,
    FailureClass,
    Receipt,
    StageRun,
    StageRunStatus,
    WorkflowInstance,
    WorkflowStatus,
    to_plain_data,
)


UTC = timezone.utc


class LedgerConflict(RuntimeError):
    """Raised when a leased run cannot be mutated by the caller."""


@dataclass(slots=True, frozen=True)
class RecoveryAction:
    stage_run_id: str
    previous_status: StageRunStatus
    action: str
    failure_class: FailureClass


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso_timestamp(value: datetime | str | None = None) -> str:
    if value is None:
        value = utc_now()
    if isinstance(value, str):
        return value
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def _json(value: Any) -> str:
    return json.dumps(to_plain_data(value), sort_keys=True, separators=(",", ":"))


def _status_value(value: StageRunStatus | WorkflowStatus | str) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _failure_value(value: FailureClass | str | None) -> str | None:
    if value is None:
        return None
    return value.value if hasattr(value, "value") else str(value)


class WorkflowLedger:
    """Repository API for the SQLite workflow ledger.

    The repository owns transactions, lease mutation, and append-only events.
    Adapter execution stays outside this class.
    """

    def __init__(self, database: str | Path):
        self.database = str(database)
        self.connection = sqlite3.connect(self.database)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")

    def close(self) -> None:
        self.connection.close()

    def initialize(self) -> None:
        self.connection.executescript(
            """
            PRAGMA foreign_keys = ON;

            CREATE TABLE IF NOT EXISTS workflow_instances (
              instance_id TEXT PRIMARY KEY,
              workflow_def_id TEXT NOT NULL,
              workflow_version TEXT NOT NULL,
              status TEXT NOT NULL,
              current_stage_id TEXT,
              idempotency_key TEXT,
              input_hash TEXT NOT NULL,
              recovery_epoch INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS stage_runs (
              stage_run_id TEXT PRIMARY KEY,
              instance_id TEXT NOT NULL REFERENCES workflow_instances(instance_id),
              stage_id TEXT NOT NULL,
              attempt INTEGER NOT NULL,
              status TEXT NOT NULL,
              adapter_id TEXT,
              actor_ref TEXT,
              failure_class TEXT,
              failure_summary TEXT,
              approval_required INTEGER NOT NULL DEFAULT 0,
              idempotency_key TEXT,
              input_hash TEXT NOT NULL,
              output_hash TEXT,
              retry_count INTEGER NOT NULL DEFAULT 0,
              retry_after_at TEXT,
              lease_owner TEXT,
              lease_token TEXT,
              lease_expires_at TEXT,
              parent_stage_run_id TEXT REFERENCES stage_runs(stage_run_id),
              receipt_id TEXT,
              created_at TEXT NOT NULL,
              started_at TEXT,
              completed_at TEXT,
              updated_at TEXT NOT NULL,
              UNIQUE(instance_id, stage_id, attempt)
            );

            CREATE TABLE IF NOT EXISTS receipts (
              receipt_id TEXT PRIMARY KEY,
              instance_id TEXT NOT NULL REFERENCES workflow_instances(instance_id),
              stage_run_id TEXT REFERENCES stage_runs(stage_run_id),
              receipt_kind TEXT NOT NULL,
              actor TEXT NOT NULL,
              status TEXT NOT NULL,
              failure_class TEXT,
              summary TEXT NOT NULL,
              receipt_json TEXT NOT NULL,
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS artifact_refs (
              artifact_id TEXT PRIMARY KEY,
              instance_id TEXT NOT NULL REFERENCES workflow_instances(instance_id),
              stage_run_id TEXT REFERENCES stage_runs(stage_run_id),
              receipt_id TEXT REFERENCES receipts(receipt_id),
              role TEXT NOT NULL,
              uri TEXT NOT NULL,
              content_hash TEXT NOT NULL,
              mime_type TEXT NOT NULL,
              size_bytes INTEGER,
              created_by TEXT,
              visibility TEXT NOT NULL,
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS adapter_invocations (
              invocation_id TEXT PRIMARY KEY,
              workflow_id TEXT NOT NULL,
              instance_id TEXT NOT NULL REFERENCES workflow_instances(instance_id),
              stage_run_id TEXT NOT NULL REFERENCES stage_runs(stage_run_id),
              adapter_family TEXT NOT NULL,
              adapter_id TEXT NOT NULL,
              operation TEXT NOT NULL,
              input_ref TEXT,
              context_packet_ref TEXT,
              idempotency_key TEXT,
              status TEXT NOT NULL,
              request_hash TEXT,
              response_hash TEXT,
              external_ref TEXT,
              error_class TEXT,
              error_summary TEXT,
              started_at TEXT NOT NULL,
              completed_at TEXT,
              invocation_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS events (
              event_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
              event_id TEXT NOT NULL UNIQUE,
              instance_id TEXT NOT NULL REFERENCES workflow_instances(instance_id),
              stage_run_id TEXT REFERENCES stage_runs(stage_run_id),
              event_type TEXT NOT NULL,
              actor TEXT NOT NULL,
              payload_json TEXT NOT NULL,
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS child_sessions (
              child_session_id TEXT PRIMARY KEY,
              parent_stage_run_id TEXT NOT NULL REFERENCES stage_runs(stage_run_id),
              invocation_id TEXT REFERENCES adapter_invocations(invocation_id),
              source_thread_id TEXT,
              external_session_id TEXT,
              delegate_kind TEXT NOT NULL,
              delegate_owner TEXT NOT NULL,
              goal_hash TEXT NOT NULL,
              context_packet_hash TEXT NOT NULL,
              allowed_scope_json TEXT NOT NULL,
              expected_receipts_json TEXT NOT NULL,
              status TEXT NOT NULL,
              audit_status TEXT NOT NULL,
              transcript_ref TEXT,
              last_seen_at TEXT,
              deadline_at TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            """
        )

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield self.connection
        except Exception:
            self.connection.rollback()
            raise
        else:
            self.connection.commit()

    def insert_workflow_instance(
        self, instance: WorkflowInstance, *, created_at: datetime | str | None = None
    ) -> None:
        now = iso_timestamp(created_at)
        with self._transaction() as conn:
            conn.execute(
                """
                INSERT INTO workflow_instances (
                  instance_id, workflow_def_id, workflow_version, status,
                  current_stage_id, idempotency_key, input_hash, recovery_epoch,
                  created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    instance.instance_id,
                    instance.workflow_def_id,
                    instance.workflow_version,
                    _status_value(instance.status),
                    instance.current_stage_id,
                    instance.idempotency_key,
                    instance.input_hash,
                    instance.recovery_epoch,
                    now,
                    now,
                ),
            )

    def get_workflow_instance(self, instance_id: str) -> WorkflowInstance | None:
        row = self.connection.execute(
            "SELECT * FROM workflow_instances WHERE instance_id = ?", (instance_id,)
        ).fetchone()
        if row is None:
            return None
        return WorkflowInstance(
            instance_id=row["instance_id"],
            workflow_def_id=row["workflow_def_id"],
            workflow_version=row["workflow_version"],
            status=WorkflowStatus(row["status"]),
            current_stage_id=row["current_stage_id"],
            idempotency_key=row["idempotency_key"],
            input_hash=row["input_hash"],
            recovery_epoch=row["recovery_epoch"],
        )

    def update_workflow_instance(
        self,
        *,
        instance_id: str,
        status: WorkflowStatus | str,
        current_stage_id: str | None,
        updated_at: datetime | str | None = None,
        actor: str = "kernel",
        event_type: str = "workflow_updated",
        payload: dict[str, Any] | None = None,
    ) -> None:
        now = iso_timestamp(updated_at)
        with self._transaction() as conn:
            conn.execute(
                """
                UPDATE workflow_instances
                SET status = ?, current_stage_id = ?, updated_at = ?
                WHERE instance_id = ?
                """,
                (_status_value(status), current_stage_id, now, instance_id),
            )
            self._append_event(
                conn,
                instance_id=instance_id,
                stage_run_id=None,
                event_type=event_type,
                actor=actor,
                payload=payload or {},
                created_at=now,
            )

    def append_event(
        self,
        *,
        instance_id: str,
        stage_run_id: str | None,
        event_type: str,
        actor: str,
        payload: dict[str, Any],
        created_at: datetime | str | None = None,
    ) -> None:
        now = iso_timestamp(created_at)
        with self._transaction() as conn:
            self._append_event(
                conn,
                instance_id=instance_id,
                stage_run_id=stage_run_id,
                event_type=event_type,
                actor=actor,
                payload=payload,
                created_at=now,
            )

    def insert_stage_run(
        self,
        run: StageRun,
        *,
        input_hash: str = "unrecorded",
        idempotency_key: str | None = None,
        parent_stage_run_id: str | None = None,
        created_at: datetime | str | None = None,
    ) -> None:
        now = iso_timestamp(created_at)
        with self._transaction() as conn:
            conn.execute(
                """
                INSERT INTO stage_runs (
                  stage_run_id, instance_id, stage_id, attempt, status,
                  adapter_id, actor_ref, failure_class, idempotency_key,
                  input_hash, retry_after_at, lease_token, parent_stage_run_id,
                  receipt_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.stage_run_id,
                    run.instance_id,
                    run.stage_id,
                    run.attempt,
                    _status_value(run.status),
                    run.adapter_id,
                    run.actor_ref,
                    _failure_value(run.failure_class),
                    idempotency_key,
                    input_hash,
                    run.retry_after_at,
                    run.lease_token,
                    parent_stage_run_id,
                    run.receipt_id,
                    now,
                    now,
                ),
            )

    def get_stage_run(self, stage_run_id: str) -> StageRun | None:
        row = self.connection.execute(
            "SELECT * FROM stage_runs WHERE stage_run_id = ?", (stage_run_id,)
        ).fetchone()
        return self._stage_run_from_row(row) if row else None

    def claim_next_queued_run(
        self,
        *,
        owner_id: str,
        lease_seconds: int = 300,
        now: datetime | str | None = None,
    ) -> StageRun | None:
        claimed_at = iso_timestamp(now)
        expires_at = iso_timestamp(_coerce_datetime(claimed_at) + timedelta(seconds=lease_seconds))
        lease_token = uuid.uuid4().hex
        with self._transaction() as conn:
            row = conn.execute(
                """
                SELECT * FROM stage_runs
                WHERE status = ?
                  AND (retry_after_at IS NULL OR retry_after_at <= ?)
                ORDER BY created_at, stage_run_id
                LIMIT 1
                """,
                (StageRunStatus.QUEUED.value, claimed_at),
            ).fetchone()
            if row is None:
                return None
            cursor = conn.execute(
                """
                UPDATE stage_runs
                SET status = ?, lease_owner = ?, lease_token = ?,
                    lease_expires_at = ?, started_at = COALESCE(started_at, ?),
                    updated_at = ?
                WHERE stage_run_id = ? AND status = ?
                """,
                (
                    StageRunStatus.CLAIMED.value,
                    owner_id,
                    lease_token,
                    expires_at,
                    claimed_at,
                    claimed_at,
                    row["stage_run_id"],
                    StageRunStatus.QUEUED.value,
                ),
            )
            if cursor.rowcount == 0:
                return None
            self._append_event(
                conn,
                instance_id=row["instance_id"],
                stage_run_id=row["stage_run_id"],
                event_type="stage_claimed",
                actor=owner_id,
                payload={
                    "lease_token": lease_token,
                    "lease_expires_at": expires_at,
                    "previous_status": row["status"],
                },
                created_at=claimed_at,
            )
            updated = conn.execute(
                "SELECT * FROM stage_runs WHERE stage_run_id = ?", (row["stage_run_id"],)
            ).fetchone()
        return self._stage_run_from_row(updated)

    def renew_lease(
        self,
        *,
        stage_run_id: str,
        owner_id: str,
        lease_token: str,
        lease_seconds: int = 300,
        now: datetime | str | None = None,
    ) -> bool:
        renewed_at = iso_timestamp(now)
        expires_at = iso_timestamp(_coerce_datetime(renewed_at) + timedelta(seconds=lease_seconds))
        with self._transaction() as conn:
            row = conn.execute(
                """
                SELECT * FROM stage_runs
                WHERE stage_run_id = ? AND lease_owner = ? AND lease_token = ?
                """,
                (stage_run_id, owner_id, lease_token),
            ).fetchone()
            if row is None or (row["lease_expires_at"] and row["lease_expires_at"] <= renewed_at):
                return False
            conn.execute(
                """
                UPDATE stage_runs
                SET lease_expires_at = ?, updated_at = ?
                WHERE stage_run_id = ? AND lease_owner = ? AND lease_token = ?
                """,
                (expires_at, renewed_at, stage_run_id, owner_id, lease_token),
            )
            self._append_event(
                conn,
                instance_id=row["instance_id"],
                stage_run_id=stage_run_id,
                event_type="lease_renewed",
                actor=owner_id,
                payload={"lease_expires_at": expires_at},
                created_at=renewed_at,
            )
        return True

    def record_adapter_invocation(
        self,
        invocation: AdapterInvocation,
        *,
        status: str = "started",
        request_hash: str | None = None,
        response_hash: str | None = None,
        external_ref: str | None = None,
        error_class: str | None = None,
        error_summary: str | None = None,
        started_at: datetime | str | None = None,
        completed_at: datetime | str | None = None,
    ) -> None:
        started = iso_timestamp(started_at)
        completed = iso_timestamp(completed_at) if completed_at is not None else None
        with self._transaction() as conn:
            conn.execute(
                """
                INSERT INTO adapter_invocations (
                  invocation_id, workflow_id, instance_id, stage_run_id,
                  adapter_family, adapter_id, operation, input_ref,
                  context_packet_ref, idempotency_key, status, request_hash,
                  response_hash, external_ref, error_class, error_summary,
                  started_at, completed_at, invocation_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    invocation.invocation_id,
                    invocation.workflow_id,
                    invocation.instance_id,
                    invocation.stage_run_id,
                    invocation.adapter_family.value,
                    invocation.adapter_id,
                    invocation.operation,
                    invocation.input_ref,
                    invocation.context_packet_ref,
                    invocation.idempotency_key,
                    status,
                    request_hash,
                    response_hash,
                    external_ref,
                    error_class,
                    error_summary,
                    started,
                    completed,
                    _json(invocation),
                ),
            )

    def record_receipt(self, receipt: Receipt) -> None:
        with self._transaction() as conn:
            self._record_receipt(conn, receipt)

    def complete_stage_run(
        self,
        *,
        stage_run_id: str,
        lease_token: str,
        receipt_id: str | None = None,
        output_hash: str | None = None,
        now: datetime | str | None = None,
        actor: str = "runner",
    ) -> None:
        completed_at = iso_timestamp(now)
        with self._transaction() as conn:
            row = self._require_leased_run(conn, stage_run_id, lease_token)
            conn.execute(
                """
                UPDATE stage_runs
                SET status = ?, receipt_id = COALESCE(?, receipt_id),
                    output_hash = COALESCE(?, output_hash),
                    lease_owner = NULL, lease_token = NULL, lease_expires_at = NULL,
                    completed_at = ?, updated_at = ?
                WHERE stage_run_id = ? AND lease_token = ?
                """,
                (
                    StageRunStatus.SUCCEEDED.value,
                    receipt_id,
                    output_hash,
                    completed_at,
                    completed_at,
                    stage_run_id,
                    lease_token,
                ),
            )
            self._append_event(
                conn,
                instance_id=row["instance_id"],
                stage_run_id=stage_run_id,
                event_type="stage_completed",
                actor=actor,
                payload={"receipt_id": receipt_id, "output_hash": output_hash},
                created_at=completed_at,
            )

    def fail_stage_run(
        self,
        *,
        stage_run_id: str,
        lease_token: str,
        failure_class: FailureClass | str,
        failure_summary: str,
        status: StageRunStatus = StageRunStatus.FAILED,
        retry_after_at: datetime | str | None = None,
        now: datetime | str | None = None,
        actor: str = "runner",
    ) -> None:
        failed_at = iso_timestamp(now)
        retry_at = iso_timestamp(retry_after_at) if retry_after_at is not None else None
        with self._transaction() as conn:
            row = self._require_leased_run(conn, stage_run_id, lease_token)
            conn.execute(
                """
                UPDATE stage_runs
                SET status = ?, failure_class = ?, failure_summary = ?,
                    retry_after_at = ?, lease_owner = NULL, lease_token = NULL,
                    lease_expires_at = NULL, completed_at = ?, updated_at = ?
                WHERE stage_run_id = ? AND lease_token = ?
                """,
                (
                    status.value,
                    _failure_value(failure_class),
                    failure_summary,
                    retry_at,
                    failed_at,
                    failed_at,
                    stage_run_id,
                    lease_token,
                ),
            )
            self._append_event(
                conn,
                instance_id=row["instance_id"],
                stage_run_id=stage_run_id,
                event_type="stage_failed",
                actor=actor,
                payload={
                    "failure_class": _failure_value(failure_class),
                    "failure_summary": failure_summary,
                    "retry_after_at": retry_at,
                    "status": status.value,
                },
                created_at=failed_at,
            )

    def schedule_retry(
        self,
        *,
        stage_run_id: str,
        lease_token: str,
        failure_class: FailureClass | str,
        failure_summary: str,
        retry_after_at: datetime | str,
        now: datetime | str | None = None,
        actor: str = "runner",
    ) -> None:
        scheduled_at = iso_timestamp(now)
        retry_at = iso_timestamp(retry_after_at)
        with self._transaction() as conn:
            row = self._require_leased_run(conn, stage_run_id, lease_token)
            conn.execute(
                """
                UPDATE stage_runs
                SET status = ?, failure_class = ?, failure_summary = ?,
                    retry_count = retry_count + 1, retry_after_at = ?,
                    lease_owner = NULL, lease_token = NULL, lease_expires_at = NULL,
                    updated_at = ?
                WHERE stage_run_id = ? AND lease_token = ?
                """,
                (
                    StageRunStatus.QUEUED.value,
                    _failure_value(failure_class),
                    failure_summary,
                    retry_at,
                    scheduled_at,
                    stage_run_id,
                    lease_token,
                ),
            )
            self._append_event(
                conn,
                instance_id=row["instance_id"],
                stage_run_id=stage_run_id,
                event_type="stage_retry_scheduled",
                actor=actor,
                payload={
                    "failure_class": _failure_value(failure_class),
                    "failure_summary": failure_summary,
                    "retry_after_at": retry_at,
                },
                created_at=scheduled_at,
            )

    def block_stage_run(
        self,
        *,
        stage_run_id: str,
        lease_token: str,
        failure_class: FailureClass | str = FailureClass.DOMAIN_BLOCKED,
        failure_summary: str,
        approval_required: bool = False,
        now: datetime | str | None = None,
        actor: str = "runner",
    ) -> None:
        blocked_at = iso_timestamp(now)
        with self._transaction() as conn:
            row = self._require_leased_run(conn, stage_run_id, lease_token)
            conn.execute(
                """
                UPDATE stage_runs
                SET status = ?, failure_class = ?, failure_summary = ?,
                    approval_required = ?, lease_owner = NULL, lease_token = NULL,
                    lease_expires_at = NULL, completed_at = ?, updated_at = ?
                WHERE stage_run_id = ? AND lease_token = ?
                """,
                (
                    StageRunStatus.BLOCKED.value,
                    _failure_value(failure_class),
                    failure_summary,
                    1 if approval_required else 0,
                    blocked_at,
                    blocked_at,
                    stage_run_id,
                    lease_token,
                ),
            )
            self._append_event(
                conn,
                instance_id=row["instance_id"],
                stage_run_id=stage_run_id,
                event_type="stage_blocked",
                actor=actor,
                payload={
                    "failure_class": _failure_value(failure_class),
                    "failure_summary": failure_summary,
                    "approval_required": approval_required,
                },
                created_at=blocked_at,
            )

    def sweep_stale_leases(
        self, *, now: datetime | str | None = None, actor: str = "recovery"
    ) -> list[RecoveryAction]:
        swept_at = iso_timestamp(now)
        actions: list[RecoveryAction] = []
        requeue_statuses = {StageRunStatus.CLAIMED.value}
        block_statuses = {
            StageRunStatus.STARTED.value,
            StageRunStatus.WAITING.value,
            StageRunStatus.WAITING_ON_CHILD.value,
            StageRunStatus.VALIDATING.value,
        }
        with self._transaction() as conn:
            rows = conn.execute(
                """
                SELECT * FROM stage_runs
                WHERE lease_token IS NOT NULL
                  AND lease_expires_at IS NOT NULL
                  AND lease_expires_at <= ?
                ORDER BY lease_expires_at, stage_run_id
                """,
                (swept_at,),
            ).fetchall()
            for row in rows:
                previous_status = StageRunStatus(row["status"])
                if row["status"] in requeue_statuses:
                    action = "requeued"
                    new_status = StageRunStatus.QUEUED.value
                    approval_required = 0
                    failure_class = FailureClass.STALE_LEASE
                    failure_summary = "Lease expired before adapter work started."
                elif row["status"] in block_statuses:
                    action = "blocked"
                    new_status = StageRunStatus.BLOCKED.value
                    approval_required = 1
                    failure_class = FailureClass.UNKNOWN_SIDE_EFFECT_STATE
                    failure_summary = "Lease expired after work may have started."
                else:
                    continue
                conn.execute(
                    """
                    UPDATE stage_runs
                    SET status = ?, failure_class = ?, failure_summary = ?,
                        approval_required = ?, lease_owner = NULL,
                        lease_token = NULL, lease_expires_at = NULL,
                        updated_at = ?
                    WHERE stage_run_id = ?
                    """,
                    (
                        new_status,
                        failure_class.value,
                        failure_summary,
                        approval_required,
                        swept_at,
                        row["stage_run_id"],
                    ),
                )
                conn.execute(
                    """
                    UPDATE workflow_instances
                    SET recovery_epoch = recovery_epoch + 1, updated_at = ?
                    WHERE instance_id = ?
                    """,
                    (swept_at, row["instance_id"]),
                )
                self._append_event(
                    conn,
                    instance_id=row["instance_id"],
                    stage_run_id=row["stage_run_id"],
                    event_type="recovery",
                    actor=actor,
                    payload={
                        "action": action,
                        "previous_status": row["status"],
                        "failure_class": failure_class.value,
                        "failure_summary": failure_summary,
                    },
                    created_at=swept_at,
                )
                actions.append(
                    RecoveryAction(
                        stage_run_id=row["stage_run_id"],
                        previous_status=previous_status,
                        action=action,
                        failure_class=failure_class,
                    )
                )
        return actions

    def list_events(self, *, stage_run_id: str | None = None) -> list[dict[str, Any]]:
        if stage_run_id is None:
            rows = self.connection.execute(
                "SELECT * FROM events ORDER BY event_sequence"
            ).fetchall()
        else:
            rows = self.connection.execute(
                "SELECT * FROM events WHERE stage_run_id = ? ORDER BY event_sequence",
                (stage_run_id,),
            ).fetchall()
        return [self._event_from_row(row) for row in rows]

    def _record_receipt(self, conn: sqlite3.Connection, receipt: Receipt) -> None:
        conn.execute(
            """
            INSERT INTO receipts (
              receipt_id, instance_id, stage_run_id, receipt_kind, actor,
              status, failure_class, summary, receipt_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                receipt.receipt_id,
                receipt.instance_id,
                receipt.stage_run_id,
                receipt.kind,
                receipt.runtime_provenance.get("actor", "unknown"),
                receipt.status,
                None,
                receipt.summary,
                _json(receipt),
                receipt.created_at,
            ),
        )
        for artifact in receipt.artifact_refs:
            self._record_artifact(conn, receipt, artifact)
        self._append_event(
            conn,
            instance_id=receipt.instance_id,
            stage_run_id=receipt.stage_run_id,
            event_type="receipt_recorded",
            actor=receipt.runtime_provenance.get("actor", "unknown"),
            payload={"receipt_id": receipt.receipt_id, "kind": receipt.kind},
            created_at=receipt.created_at,
        )

    def _record_artifact(
        self, conn: sqlite3.Connection, receipt: Receipt, artifact: ArtifactRef
    ) -> None:
        conn.execute(
            """
            INSERT INTO artifact_refs (
              artifact_id, instance_id, stage_run_id, receipt_id, role, uri,
              content_hash, mime_type, size_bytes, created_by, visibility,
              created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                artifact.artifact_id,
                receipt.instance_id,
                receipt.stage_run_id,
                receipt.receipt_id,
                artifact.role,
                artifact.uri,
                artifact.content_hash,
                artifact.mime_type,
                artifact.size_bytes,
                artifact.created_by,
                artifact.visibility,
                receipt.created_at,
            ),
        )

    def _require_leased_run(
        self, conn: sqlite3.Connection, stage_run_id: str, lease_token: str
    ) -> sqlite3.Row:
        row = conn.execute(
            "SELECT * FROM stage_runs WHERE stage_run_id = ? AND lease_token = ?",
            (stage_run_id, lease_token),
        ).fetchone()
        if row is None:
            raise LedgerConflict(f"stage run {stage_run_id!r} is not leased by this token")
        return row

    def _append_event(
        self,
        conn: sqlite3.Connection,
        *,
        instance_id: str,
        stage_run_id: str | None,
        event_type: str,
        actor: str,
        payload: dict[str, Any],
        created_at: str,
    ) -> None:
        conn.execute(
            """
            INSERT INTO events (
              event_id, instance_id, stage_run_id, event_type,
              actor, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                uuid.uuid4().hex,
                instance_id,
                stage_run_id,
                event_type,
                actor,
                _json(payload),
                created_at,
            ),
        )

    def _stage_run_from_row(self, row: sqlite3.Row) -> StageRun:
        return StageRun(
            stage_run_id=row["stage_run_id"],
            instance_id=row["instance_id"],
            stage_id=row["stage_id"],
            status=StageRunStatus(row["status"]),
            attempt=row["attempt"],
            adapter_id=row["adapter_id"],
            actor_ref=row["actor_ref"],
            lease_token=row["lease_token"],
            receipt_id=row["receipt_id"],
            failure_class=FailureClass(row["failure_class"]) if row["failure_class"] else None,
            retry_after_at=row["retry_after_at"],
        )

    def _event_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "event_id": row["event_id"],
            "instance_id": row["instance_id"],
            "stage_run_id": row["stage_run_id"],
            "event_type": row["event_type"],
            "actor": row["actor"],
            "payload": json.loads(row["payload_json"]),
            "created_at": row["created_at"],
        }


def _coerce_datetime(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    return datetime.fromisoformat(value).astimezone(UTC)


__all__ = [
    "LedgerConflict",
    "RecoveryAction",
    "WorkflowLedger",
    "iso_timestamp",
    "utc_now",
]
