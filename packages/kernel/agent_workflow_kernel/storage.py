"""SQLite ledger for durable workflow kernel state."""

from __future__ import annotations

import json
import hashlib
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

from .contracts import (
    AdapterInvocation,
    ArtifactRef,
    FailureClass,
    Receipt,
    ResolvedLeasePolicy,
    StageRun,
    StageRunStatus,
    WorkflowInstance,
    WorkflowStatus,
    to_plain_data,
)
from .policy import HumanApprovalReceipt


UTC = timezone.utc


class LedgerConflict(RuntimeError):
    """Raised when a leased run cannot be mutated by the caller."""


class LedgerSchemaError(RuntimeError):
    """Raised before touching a database that is not a supported ledger shape."""


LEDGER_SCHEMA_VERSION = 1

# The v0 family was deliberately frozen at the last pre-versioned layout.
# A v0 database must have every durable domain table; optional columns added by
# old rolling upgrades are normalized by the atomic v0 -> v1 migration.
_V0_REQUIRED_TABLES = frozenset(
    {
        "workflow_instances",
        "stage_runs",
        "receipts",
        "artifact_refs",
        "adapter_invocations",
        "events",
        "child_sessions",
        "human_decisions",
    }
)

_V0_REQUIRED_COLUMNS = {
    "workflow_instances": {"instance_id", "workflow_def_id", "workflow_version", "status", "input_hash", "created_at", "updated_at"},
    "stage_runs": {"stage_run_id", "instance_id", "stage_id", "attempt", "status", "input_hash", "created_at", "updated_at"},
    "receipts": {"receipt_id", "instance_id", "receipt_json", "created_at"},
    "artifact_refs": {"artifact_id", "instance_id", "content_hash", "created_at"},
    "adapter_invocations": {"invocation_id", "instance_id", "stage_run_id", "status", "invocation_json", "started_at"},
    "events": {"event_sequence", "event_id", "instance_id", "event_type", "payload_json", "created_at"},
    "child_sessions": {"child_session_id", "parent_stage_run_id", "status", "created_at", "updated_at"},
    "human_decisions": {"decision_id", "instance_id", "stage_run_id", "action_fingerprint", "receipt_json", "created_at"},
}


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


_ACTIVE_LEASE_STATUSES: frozenset[StageRunStatus] = frozenset(
    {
        StageRunStatus.CLAIMED,
        StageRunStatus.STARTED,
        StageRunStatus.WAITING,
        StageRunStatus.WAITING_ON_CHILD,
        StageRunStatus.VALIDATING,
    }
)


def _decision_value(value: Any) -> str:
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

    def close(self) -> None:
        self.connection.close()

    def initialize(self) -> None:
        """Open only a known ledger shape and advance it to schema v1.

        Version validation happens before any schema DDL, `ALTER`, index work,
        or journal setting.  This protects a future/corrupt database from the
        old `CREATE IF NOT EXISTS` behaviour which could mutate it before
        refusing to operate.
        """
        version = int(self.connection.execute("PRAGMA user_version").fetchone()[0])
        tables = self._user_tables()
        if version > LEDGER_SCHEMA_VERSION or version < 0:
            raise LedgerSchemaError(
                f"unsupported ledger schema version {version}; supported versions are 0 and {LEDGER_SCHEMA_VERSION}"
            )
        if not tables:
            if version != 0:
                raise LedgerSchemaError(
                    f"empty ledger has schema version {version}, expected 0"
                )
            try:
                self._initialize_schema_unversioned()
                self.connection.execute(f"PRAGMA user_version = {LEDGER_SCHEMA_VERSION}")
                self.connection.commit()
            except Exception:
                self.connection.rollback()
                raise
            self.connection.execute("PRAGMA journal_mode = WAL")
            return
        if version == 0:
            self._validate_v0_shape(tables)
            self._migrate_v0_to_v1()
        else:
            self._validate_v1_shape(tables)
        self.connection.execute("PRAGMA journal_mode = WAL")

    def _initialize_schema_unversioned(self) -> None:
        self.connection.executescript(
            """
            BEGIN IMMEDIATE;
            PRAGMA foreign_keys = ON;

            CREATE TABLE IF NOT EXISTS workflow_instances (
              instance_id TEXT PRIMARY KEY,
              workflow_def_id TEXT NOT NULL,
              workflow_version TEXT NOT NULL,
              status TEXT NOT NULL,
              current_stage_id TEXT,
              idempotency_key TEXT,
              input_hash TEXT NOT NULL,
              input_snapshot_json TEXT,
              workflow_definition_json TEXT,
              workflow_definition_hash TEXT,
              workflow_source_uri TEXT,
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
              prompt_hash TEXT,
              context_packet_ref TEXT,
              context_packet_hash TEXT,
              rendered_context_hash TEXT,
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

            CREATE TABLE IF NOT EXISTS human_decisions (
              decision_id TEXT PRIMARY KEY,
              instance_id TEXT NOT NULL REFERENCES workflow_instances(instance_id),
              stage_run_id TEXT NOT NULL REFERENCES stage_runs(stage_run_id),
              gate_id TEXT NOT NULL,
              decision TEXT NOT NULL,
              human_ref TEXT NOT NULL,
              canonical_surface TEXT NOT NULL,
              exact_action_approved TEXT NOT NULL,
              action_fingerprint TEXT NOT NULL,
              receipt_json TEXT NOT NULL,
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS late_results (
              late_result_id TEXT PRIMARY KEY,
              instance_id TEXT NOT NULL REFERENCES workflow_instances(instance_id),
              stage_run_id TEXT NOT NULL REFERENCES stage_runs(stage_run_id),
              reported_lease_token TEXT,
              reported_owner TEXT,
              result_kind TEXT NOT NULL,
              result_hash TEXT,
              external_ref TEXT,
              evidence_json TEXT NOT NULL,
              observed_at TEXT NOT NULL,
              recorded_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_workflow_instances_created
              ON workflow_instances (created_at DESC, instance_id);
            CREATE INDEX IF NOT EXISTS idx_workflow_instances_updated
              ON workflow_instances (updated_at DESC, instance_id);
            CREATE INDEX IF NOT EXISTS idx_workflow_instances_status_updated
              ON workflow_instances (status, updated_at DESC, instance_id);
            CREATE INDEX IF NOT EXISTS idx_stage_runs_waiting_gate
              ON stage_runs (status, created_at, stage_run_id);
            CREATE INDEX IF NOT EXISTS idx_stage_runs_waiting_gate_latest
              ON stage_runs (instance_id, status, updated_at, stage_run_id);
            CREATE INDEX IF NOT EXISTS idx_stage_runs_instance_stage_latest
              ON stage_runs (
                instance_id, stage_id, updated_at DESC, attempt DESC, stage_run_id DESC
              );
            CREATE INDEX IF NOT EXISTS idx_stage_runs_status_lease
              ON stage_runs (status, lease_expires_at, stage_run_id);
            -- Migration: the lane-agnostic kernel must not name a host lane. The
            -- old `idx_stage_runs_trade_lab_feed` is renamed to a name describing
            -- its COLUMNS (the index is lane-generic); drop the old name so an
            -- existing ledger DB upgrades cleanly on next open.
            DROP INDEX IF EXISTS idx_stage_runs_trade_lab_feed;
            CREATE INDEX IF NOT EXISTS idx_stage_runs_stage_status_completed
              ON stage_runs (
                stage_id, status, completed_at DESC, stage_run_id DESC, instance_id
              );
            CREATE INDEX IF NOT EXISTS idx_events_stage_run_sequence
              ON events (stage_run_id, event_sequence);
            CREATE INDEX IF NOT EXISTS idx_receipts_instance_latest
              ON receipts (instance_id, status, created_at DESC, receipt_id DESC);
            CREATE INDEX IF NOT EXISTS idx_receipts_stage_run
              ON receipts (stage_run_id, created_at DESC, receipt_id DESC);
            CREATE INDEX IF NOT EXISTS idx_late_results_stage_run
              ON late_results (stage_run_id, recorded_at DESC, late_result_id DESC);
            """
        )
        for column_name, column_sql in (
            ("prompt_hash", "prompt_hash TEXT"),
            ("context_packet_ref", "context_packet_ref TEXT"),
            ("context_packet_hash", "context_packet_hash TEXT"),
            ("rendered_context_hash", "rendered_context_hash TEXT"),
            ("lease_seconds", "lease_seconds INTEGER"),
            ("lease_source", "lease_source TEXT"),
            ("lease_source_ref", "lease_source_ref TEXT"),
        ):
            self._ensure_stage_run_column(column_name, column_sql)
        for column_name, column_sql in (
            ("input_snapshot_json", "input_snapshot_json TEXT"),
            ("workflow_definition_json", "workflow_definition_json TEXT"),
            ("workflow_definition_hash", "workflow_definition_hash TEXT"),
            ("workflow_source_uri", "workflow_source_uri TEXT"),
        ):
            self._ensure_workflow_instance_column(column_name, column_sql)

    def _user_tables(self) -> set[str]:
        return {
            str(row["name"])
            for row in self.connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        }

    def _table_columns(self, table_name: str) -> set[str]:
        return {
            str(row["name"])
            for row in self.connection.execute(f"PRAGMA table_info({table_name})").fetchall()
        }

    def _validate_v0_shape(self, tables: set[str]) -> None:
        missing_tables = _V0_REQUIRED_TABLES - tables
        if missing_tables:
            raise LedgerSchemaError(
                "malformed v0 ledger; missing required tables: " + ", ".join(sorted(missing_tables))
            )
        for table_name, required_columns in _V0_REQUIRED_COLUMNS.items():
            missing_columns = required_columns - self._table_columns(table_name)
            if missing_columns:
                raise LedgerSchemaError(
                    f"malformed v0 ledger; {table_name} is missing required columns: "
                    + ", ".join(sorted(missing_columns))
                )

    def _validate_v1_shape(self, tables: set[str]) -> None:
        required_tables = _V0_REQUIRED_TABLES | {"late_results"}
        missing_tables = required_tables - tables
        if missing_tables:
            raise LedgerSchemaError(
                "malformed v1 ledger; missing required tables: " + ", ".join(sorted(missing_tables))
            )
        self._validate_v0_shape(tables)
        required_stage_columns = {
            "prompt_hash",
            "context_packet_ref",
            "context_packet_hash",
            "rendered_context_hash",
            "lease_seconds",
            "lease_source",
            "lease_source_ref",
        }
        missing_stage_columns = required_stage_columns - self._table_columns("stage_runs")
        if missing_stage_columns:
            raise LedgerSchemaError(
                "malformed v1 ledger; stage_runs is missing required columns: "
                + ", ".join(sorted(missing_stage_columns))
            )
        required_instance_columns = {
            "input_snapshot_json",
            "workflow_definition_json",
            "workflow_definition_hash",
            "workflow_source_uri",
        }
        missing_instance_columns = required_instance_columns - self._table_columns("workflow_instances")
        if missing_instance_columns:
            raise LedgerSchemaError(
                "malformed v1 ledger; workflow_instances is missing required columns: "
                + ", ".join(sorted(missing_instance_columns))
            )

    def _migrate_v0_to_v1(self) -> None:
        """Advance the frozen v0 layout atomically, preserving every row.

        The only legal migration is v0 -> v1.  Any exception (including an
        injected SQLite failure) rolls back both DDL and `user_version`, so a
        caller can safely restore its pre-migration copy and retry.
        """
        with self._transaction() as conn:
            for column_name, column_sql in (
                ("prompt_hash", "prompt_hash TEXT"),
                ("context_packet_ref", "context_packet_ref TEXT"),
                ("context_packet_hash", "context_packet_hash TEXT"),
                ("rendered_context_hash", "rendered_context_hash TEXT"),
                ("lease_seconds", "lease_seconds INTEGER"),
                ("lease_source", "lease_source TEXT"),
                ("lease_source_ref", "lease_source_ref TEXT"),
            ):
                self._ensure_stage_run_column(column_name, column_sql)
            for column_name, column_sql in (
                ("input_snapshot_json", "input_snapshot_json TEXT"),
                ("workflow_definition_json", "workflow_definition_json TEXT"),
                ("workflow_definition_hash", "workflow_definition_hash TEXT"),
                ("workflow_source_uri", "workflow_source_uri TEXT"),
            ):
                self._ensure_workflow_instance_column(column_name, column_sql)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS late_results (
                  late_result_id TEXT PRIMARY KEY,
                  instance_id TEXT NOT NULL REFERENCES workflow_instances(instance_id),
                  stage_run_id TEXT NOT NULL REFERENCES stage_runs(stage_run_id),
                  reported_lease_token TEXT,
                  reported_owner TEXT,
                  result_kind TEXT NOT NULL,
                  result_hash TEXT,
                  external_ref TEXT,
                  evidence_json TEXT NOT NULL,
                  observed_at TEXT NOT NULL,
                  recorded_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_late_results_stage_run "
                "ON late_results (stage_run_id, recorded_at DESC, late_result_id DESC)"
            )
            conn.execute(f"PRAGMA user_version = {LEDGER_SCHEMA_VERSION}")
        self._validate_v1_shape(self._user_tables())

    def backup_to(self, destination: str | Path) -> str:
        """Create a verified SQLite backup and return its SHA-256 digest."""
        destination_path = Path(destination)
        if destination_path.resolve() == Path(self.database).resolve():
            raise ValueError("backup destination must differ from the active ledger")
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(destination_path) as backup:
            self.connection.backup(backup)
            integrity = backup.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise LedgerSchemaError(f"backup integrity check failed: {integrity}")
        return _sha256_file(destination_path)

    def restore_from_backup(self, source: str | Path) -> str:
        """Restore a verified SQLite backup into this open ledger connection."""
        source_path = Path(source)
        if not source_path.is_file():
            raise FileNotFoundError(source_path)
        with sqlite3.connect(source_path) as backup:
            integrity = backup.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise LedgerSchemaError(f"backup integrity check failed: {integrity}")
            backup.backup(self.connection)
        self.initialize()
        return _sha256_file(source_path)

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
        self,
        instance: WorkflowInstance,
        *,
        created_at: datetime | str | None = None,
        input_snapshot: Any | None = None,
        workflow_definition_json: str | None = None,
        workflow_definition_hash: str | None = None,
        workflow_source_uri: str | None = None,
    ) -> None:
        now = iso_timestamp(created_at)
        with self._transaction() as conn:
            conn.execute(
                """
                INSERT INTO workflow_instances (
                  instance_id, workflow_def_id, workflow_version, status,
                  current_stage_id, idempotency_key, input_hash,
                  input_snapshot_json, workflow_definition_json,
                  workflow_definition_hash, workflow_source_uri, recovery_epoch,
                  created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    instance.instance_id,
                    instance.workflow_def_id,
                    instance.workflow_version,
                    _status_value(instance.status),
                    instance.current_stage_id,
                    instance.idempotency_key,
                    instance.input_hash,
                    _json(input_snapshot) if input_snapshot is not None else None,
                    workflow_definition_json,
                    workflow_definition_hash,
                    workflow_source_uri,
                    instance.recovery_epoch,
                    now,
                    now,
                ),
            )

    def get_workflow_instance(self, instance_id: str) -> WorkflowInstance | None:
        row = self.connection.execute(
            "SELECT * FROM workflow_instances WHERE instance_id = ?", (instance_id,)
        ).fetchone()
        return self._workflow_instance_from_row(row) if row else None

    def get_workflow_instance_provenance(self, instance_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            """
            SELECT input_hash, input_snapshot_json, workflow_definition_json,
                   workflow_definition_hash, workflow_source_uri
            FROM workflow_instances
            WHERE instance_id = ?
            """,
            (instance_id,),
        ).fetchone()
        if row is None:
            return None
        return {
            "input_hash": row["input_hash"],
            "input_snapshot": _loads_json(row["input_snapshot_json"])
            if row["input_snapshot_json"]
            else None,
            "workflow_definition": _loads_json(row["workflow_definition_json"])
            if row["workflow_definition_json"]
            else None,
            "workflow_definition_hash": row["workflow_definition_hash"],
            "workflow_source_uri": row["workflow_source_uri"],
        }

    def get_workflow_input_snapshot(self, instance_id: str) -> dict[str, Any] | None:
        provenance = self.get_workflow_instance_provenance(instance_id)
        if provenance is None:
            return None
        snapshot = provenance.get("input_snapshot")
        return dict(snapshot) if isinstance(snapshot, dict) else None

    def find_next_workflow_instance_for_work(
        self,
        *,
        workflow_def_id: str | None = None,
        workflow_version: str | None = None,
        include_waiting_human: bool = False,
        prefer_waiting_human: bool = False,
        now: datetime | str | None = None,
    ) -> WorkflowInstance | None:
        """Return the next workflow instance with queued or waiting work.

        Queued stage runs are preferred because they can be claimed atomically by
        the runner. Surface lifecycle callers can prefer waiting human gates so
        an acknowledged gate remains discoverable even while unrelated queued
        work exists in the same ledger.
        """

        timestamp = iso_timestamp(now)
        filters: list[str] = []
        if workflow_def_id is not None:
            filters.append("wi.workflow_def_id = ?")
        if workflow_version is not None:
            filters.append("wi.workflow_version = ?")
        filter_sql = f" AND {' AND '.join(filters)}" if filters else ""

        def filter_params() -> list[Any]:
            params: list[Any] = []
            if workflow_def_id is not None:
                params.append(workflow_def_id)
            if workflow_version is not None:
                params.append(workflow_version)
            return params

        def waiting_row() -> sqlite3.Row | None:
            params: list[Any] = [StageRunStatus.WAITING_ON_HUMAN.value, *filter_params()]
            return self.connection.execute(
                f"""
                SELECT wi.*
                FROM stage_runs sr
                JOIN workflow_instances wi ON wi.instance_id = sr.instance_id
                WHERE sr.status = ?
                  {filter_sql}
                ORDER BY sr.updated_at, sr.stage_run_id
                LIMIT 1
                """,
                tuple(params),
            ).fetchone()

        if include_waiting_human and prefer_waiting_human:
            row = waiting_row()
            if row is not None:
                return self._workflow_instance_from_row(row)

        params = [StageRunStatus.QUEUED.value, timestamp, *filter_params()]
        row = self.connection.execute(
            f"""
            SELECT wi.*
            FROM stage_runs sr
            JOIN workflow_instances wi ON wi.instance_id = sr.instance_id
            WHERE sr.status = ?
              AND (sr.retry_after_at IS NULL OR sr.retry_after_at <= ?)
              {filter_sql}
            ORDER BY sr.created_at, sr.stage_run_id
            LIMIT 1
            """,
            tuple(params),
        ).fetchone()
        if row is not None or not include_waiting_human:
            return self._workflow_instance_from_row(row) if row else None

        row = waiting_row()
        return self._workflow_instance_from_row(row) if row else None

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

    def cancel_workflow_instance(
        self,
        *,
        instance_id: str,
        actor: str = "operator",
        reason: str | None = None,
        updated_at: datetime | str | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        """Terminalize an instance as cancelled and retire active stage runs.

        This is an operator repair primitive, not a workflow transition. It is
        intentionally conservative: terminal instances are left unchanged unless
        ``force`` is set, and only active/not-yet-terminal stage runs are marked
        superseded.
        """

        now = iso_timestamp(updated_at)
        terminal = {
            WorkflowStatus.DONE.value,
            WorkflowStatus.CANCELLED.value,
            WorkflowStatus.POLICY_DENIED.value,
        }
        active_stage_statuses = {
            StageRunStatus.QUEUED.value,
            StageRunStatus.CLAIMED.value,
            StageRunStatus.STARTED.value,
            StageRunStatus.WAITING.value,
            StageRunStatus.WAITING_ON_CHILD.value,
            StageRunStatus.WAITING_ON_HUMAN.value,
            StageRunStatus.VALIDATING.value,
            StageRunStatus.APPROVAL_REQUIRED.value,
        }
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT * FROM workflow_instances WHERE instance_id = ?",
                (instance_id,),
            ).fetchone()
            if row is None:
                raise KeyError(instance_id)
            previous_status = row["status"]
            if previous_status in terminal and not force:
                return {
                    "changed": False,
                    "instance_id": instance_id,
                    "previous_status": previous_status,
                    "status": previous_status,
                    "superseded_stage_runs": [],
                    "reason": "already_terminal",
                }
            stage_rows = conn.execute(
                f"""
                SELECT stage_run_id, status
                FROM stage_runs
                WHERE instance_id = ?
                  AND status IN ({",".join("?" for _ in active_stage_statuses)})
                ORDER BY created_at, stage_run_id
                """,
                (instance_id, *sorted(active_stage_statuses)),
            ).fetchall()
            superseded = [r["stage_run_id"] for r in stage_rows]
            if superseded:
                conn.executemany(
                    """
                    UPDATE stage_runs
                    SET status = ?, lease_owner = NULL, lease_token = NULL,
                        lease_expires_at = NULL, updated_at = ?
                    WHERE stage_run_id = ?
                    """,
                    [
                        (StageRunStatus.SUPERSEDED.value, now, stage_run_id)
                        for stage_run_id in superseded
                    ],
                )
            conn.execute(
                """
                UPDATE workflow_instances
                SET status = ?, current_stage_id = NULL, updated_at = ?
                WHERE instance_id = ?
                """,
                (WorkflowStatus.CANCELLED.value, now, instance_id),
            )
            payload = {
                "previous_status": previous_status,
                "previous_stage_id": row["current_stage_id"],
                "reason": reason,
                "force": force,
                "superseded_stage_runs": superseded,
            }
            self._append_event(
                conn,
                instance_id=instance_id,
                stage_run_id=None,
                event_type="workflow_cancelled",
                actor=actor,
                payload=payload,
                created_at=now,
            )
        return {
            "changed": True,
            "instance_id": instance_id,
            "previous_status": previous_status,
            "status": WorkflowStatus.CANCELLED.value,
            "superseded_stage_runs": superseded,
            "reason": reason,
        }

    def park_stale_workflow_instance(
        self,
        *,
        instance_id: str,
        actor: str = "supervisor",
        reason: str | None = None,
        updated_at: datetime | str | None = None,
    ) -> dict[str, Any]:
        """Recoverably PARK (not cancel) an in-flight instance stranded by a
        workflow-definition change.

        This is the storage half of the auto-park-stale sweep. Unlike
        :meth:`cancel_workflow_instance` it does NOT terminalize the instance as
        ``cancelled`` — the row + all receipts are preserved for inspection. It
        moves the instance to ``blocked`` (a non-advancing, non-cancelled state
        the active-instance sweep already excludes) and supersedes any active
        stage runs so the stranded gate stops surfacing as a waiting ACT/NEEDS-
        ATTENTION row. A distinct ``workflow_auto_parked_stale_definition`` event
        records the park for the audit trail.

        Idempotent by construction: a caller that filters to in-flight (non-
        terminal, non-blocked) instances never re-parks an already-parked run,
        and this method also no-ops on an instance already in a blocked/terminal
        state (``changed: False``) so a double-call is safe.
        """
        now = iso_timestamp(updated_at)
        # An already-parked/terminal instance is left untouched (idempotent).
        leave_unchanged = {
            WorkflowStatus.DONE.value,
            WorkflowStatus.CANCELLED.value,
            WorkflowStatus.POLICY_DENIED.value,
            WorkflowStatus.BLOCKED.value,
        }
        active_stage_statuses = {
            StageRunStatus.QUEUED.value,
            StageRunStatus.CLAIMED.value,
            StageRunStatus.STARTED.value,
            StageRunStatus.WAITING.value,
            StageRunStatus.WAITING_ON_CHILD.value,
            StageRunStatus.WAITING_ON_HUMAN.value,
            StageRunStatus.VALIDATING.value,
            StageRunStatus.APPROVAL_REQUIRED.value,
        }
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT * FROM workflow_instances WHERE instance_id = ?",
                (instance_id,),
            ).fetchone()
            if row is None:
                raise KeyError(instance_id)
            previous_status = row["status"]
            if previous_status in leave_unchanged:
                return {
                    "changed": False,
                    "instance_id": instance_id,
                    "previous_status": previous_status,
                    "status": previous_status,
                    "superseded_stage_runs": [],
                    "reason": "already_parked_or_terminal",
                }
            stage_rows = conn.execute(
                f"""
                SELECT stage_run_id, status
                FROM stage_runs
                WHERE instance_id = ?
                  AND status IN ({",".join("?" for _ in active_stage_statuses)})
                ORDER BY created_at, stage_run_id
                """,
                (instance_id, *sorted(active_stage_statuses)),
            ).fetchall()
            superseded = [r["stage_run_id"] for r in stage_rows]
            if superseded:
                conn.executemany(
                    """
                    UPDATE stage_runs
                    SET status = ?, lease_owner = NULL, lease_token = NULL,
                        lease_expires_at = NULL, updated_at = ?
                    WHERE stage_run_id = ?
                    """,
                    [
                        (StageRunStatus.SUPERSEDED.value, now, stage_run_id)
                        for stage_run_id in superseded
                    ],
                )
            conn.execute(
                """
                UPDATE workflow_instances
                SET status = ?, current_stage_id = NULL, updated_at = ?
                WHERE instance_id = ?
                """,
                (WorkflowStatus.BLOCKED.value, now, instance_id),
            )
            payload = {
                "previous_status": previous_status,
                "previous_stage_id": row["current_stage_id"],
                "reason": reason,
                "superseded_stage_runs": superseded,
            }
            self._append_event(
                conn,
                instance_id=instance_id,
                stage_run_id=None,
                event_type="workflow_auto_parked_stale_definition",
                actor=actor,
                payload=payload,
                created_at=now,
            )
        return {
            "changed": True,
            "instance_id": instance_id,
            "previous_status": previous_status,
            "status": WorkflowStatus.BLOCKED.value,
            "superseded_stage_runs": superseded,
            "reason": reason,
        }

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
        instance_id: str | None = None,
        lease_seconds: int | None = 300,
        lease_resolver: Callable[[StageRun], ResolvedLeasePolicy] | None = None,
        now: datetime | str | None = None,
    ) -> StageRun | None:
        claimed_at = iso_timestamp(now)
        lease_token = uuid.uuid4().hex
        instance_filter = "AND instance_id = ?" if instance_id is not None else ""
        params: tuple[Any, ...] = (
            (StageRunStatus.QUEUED.value, claimed_at, instance_id)
            if instance_id is not None
            else (StageRunStatus.QUEUED.value, claimed_at)
        )
        with self._transaction() as conn:
            row = conn.execute(
                f"""
                SELECT * FROM stage_runs
                WHERE status = ?
                  AND (retry_after_at IS NULL OR retry_after_at <= ?)
                  {instance_filter}
                ORDER BY created_at, stage_run_id
                LIMIT 1
                """,
                params,
            ).fetchone()
            if row is None:
                return None
            lease = (
                lease_resolver(self._stage_run_from_row(row))
                if lease_resolver is not None
                else ResolvedLeasePolicy(
                    lease_seconds=_positive_int(lease_seconds, "lease_seconds"),
                    source="runner_default",
                    source_ref="WorkflowRunner.run_once.lease_seconds",
                    actor_ref=row["actor_ref"],
                )
            )
            expires_at = iso_timestamp(
                _coerce_datetime(claimed_at) + timedelta(seconds=lease.lease_seconds)
            )
            cursor = conn.execute(
                """
                UPDATE stage_runs
                SET status = ?, lease_owner = ?, lease_token = ?,
                    lease_expires_at = ?, lease_seconds = ?, lease_source = ?,
                    lease_source_ref = ?, updated_at = ?
                WHERE stage_run_id = ? AND status = ?
                """,
                (
                    StageRunStatus.CLAIMED.value,
                    owner_id,
                    lease_token,
                    expires_at,
                    lease.lease_seconds,
                    lease.source,
                    lease.source_ref,
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
                    "lease_seconds": lease.lease_seconds,
                    "lease_source": lease.source,
                    "lease_source_ref": lease.source_ref,
                    "lease_expires_at": expires_at,
                    "lease": to_plain_data(lease),
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
            if row is None or (
                row["lease_expires_at"]
                and _coerce_datetime(row["lease_expires_at"]) <= _coerce_datetime(renewed_at)
            ):
                return False
            if StageRunStatus(row["status"]) not in _ACTIVE_LEASE_STATUSES:
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

    def mark_stage_run_started(
        self,
        *,
        stage_run_id: str,
        lease_token: str,
        actor: str,
        owner_id: str | None = None,
        idempotency_key: str | None = None,
        side_effect_scope: dict[str, Any] | None = None,
        adapter_family: str | None = None,
        adapter_id: str | None = None,
        operation: str | None = None,
        request_hash: str | None = None,
        now: datetime | str | None = None,
    ) -> None:
        """Mark a claimed run as past the safe replay point.

        Once this is recorded, stale-lease recovery must assume adapter or
        handler work may have started and must not blindly requeue the attempt.
        """

        started_at = iso_timestamp(now)
        with self._transaction() as conn:
            row = self._require_leased_run(
                conn, stage_run_id, lease_token, at=started_at, owner_id=owner_id or actor,
                permitted_statuses={StageRunStatus.CLAIMED},
            )
            conn.execute(
                """
                UPDATE stage_runs
                SET status = ?, started_at = COALESCE(started_at, ?),
                    idempotency_key = COALESCE(idempotency_key, ?),
                    updated_at = ?
                WHERE stage_run_id = ? AND lease_token = ?
                """,
                (
                    StageRunStatus.STARTED.value,
                    started_at,
                    idempotency_key,
                    started_at,
                    stage_run_id,
                    lease_token,
                ),
            )
            self._append_event(
                conn,
                instance_id=row["instance_id"],
                stage_run_id=stage_run_id,
                event_type="stage_started",
                actor=actor,
                payload={
                    "previous_status": row["status"],
                    "idempotency_key": idempotency_key or row["idempotency_key"],
                    "adapter_family": adapter_family,
                    "adapter_id": adapter_id,
                    "operation": operation,
                    "request_hash": request_hash,
                    "side_effect_scope": side_effect_scope or {},
                },
                created_at=started_at,
            )

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

    def record_adapter_invocation_started(
        self,
        invocation: AdapterInvocation,
        *,
        request_hash: str | None,
        actor: str,
        side_effect_scope: dict[str, Any] | None = None,
        started_at: datetime | str | None = None,
    ) -> None:
        started = iso_timestamp(started_at)
        with self._transaction() as conn:
            conn.execute(
                """
                INSERT INTO adapter_invocations (
                  invocation_id, workflow_id, instance_id, stage_run_id,
                  adapter_family, adapter_id, operation, input_ref,
                  context_packet_ref, idempotency_key, status, request_hash,
                  response_hash, external_ref, error_class, error_summary,
                  started_at, completed_at, invocation_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, ?, NULL, ?)
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
                    "started",
                    request_hash,
                    started,
                    _json(invocation),
                ),
            )
            self._append_event(
                conn,
                instance_id=invocation.instance_id,
                stage_run_id=invocation.stage_run_id,
                event_type="adapter_invocation_preflight",
                actor=actor,
                payload={
                    "invocation_id": invocation.invocation_id,
                    "adapter_family": invocation.adapter_family.value,
                    "adapter_id": invocation.adapter_id,
                    "operation": invocation.operation,
                    "idempotency_key": invocation.idempotency_key,
                    "request_hash": request_hash,
                    "side_effect_scope": side_effect_scope or {},
                },
                created_at=started,
            )

    def complete_adapter_invocation(
        self,
        *,
        invocation_id: str,
        status: str,
        actor: str,
        response_hash: str | None = None,
        external_ref: str | None = None,
        error_class: str | None = None,
        error_summary: str | None = None,
        completed_at: datetime | str | None = None,
    ) -> None:
        completed = iso_timestamp(completed_at)
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT * FROM adapter_invocations WHERE invocation_id = ?",
                (invocation_id,),
            ).fetchone()
            if row is None:
                raise LedgerConflict(f"adapter invocation {invocation_id!r} is not started")
            conn.execute(
                """
                UPDATE adapter_invocations
                SET status = ?, response_hash = ?, external_ref = ?,
                    error_class = ?, error_summary = ?, completed_at = ?
                WHERE invocation_id = ?
                """,
                (
                    status,
                    response_hash,
                    external_ref,
                    error_class,
                    error_summary,
                    completed,
                    invocation_id,
                ),
            )
            self._append_event(
                conn,
                instance_id=row["instance_id"],
                stage_run_id=row["stage_run_id"],
                event_type="adapter_invocation_completed",
                actor=actor,
                payload={
                    "invocation_id": invocation_id,
                    "status": status,
                    "response_hash": response_hash,
                    "external_ref": external_ref,
                    "error_class": error_class,
                    "error_summary": error_summary,
                },
                created_at=completed,
            )

    def record_stage_run_prompt_context(
        self,
        *,
        stage_run_id: str,
        prompt_hash: str,
        context_packet_ref: str,
        context_packet_hash: str,
        rendered_context_hash: str,
    ) -> None:
        """Pin resolved prompt and rendered-context hashes onto a stage run."""

        with self._transaction() as conn:
            conn.execute(
                """
                UPDATE stage_runs
                SET prompt_hash = ?, context_packet_ref = ?,
                    context_packet_hash = ?, rendered_context_hash = ?
                WHERE stage_run_id = ?
                """,
                (
                    prompt_hash,
                    context_packet_ref,
                    context_packet_hash,
                    rendered_context_hash,
                    stage_run_id,
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
        owner_id: str | None = None,
    ) -> None:
        completed_at = iso_timestamp(now)
        with self._transaction() as conn:
            row = self._require_leased_run(
                conn, stage_run_id, lease_token, at=completed_at, owner_id=owner_id or actor,
                permitted_statuses=_ACTIVE_LEASE_STATUSES,
            )
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
        owner_id: str | None = None,
    ) -> None:
        failed_at = iso_timestamp(now)
        retry_at = iso_timestamp(retry_after_at) if retry_after_at is not None else None
        with self._transaction() as conn:
            row = self._require_leased_run(
                conn, stage_run_id, lease_token, at=failed_at, owner_id=owner_id or actor,
                permitted_statuses=_ACTIVE_LEASE_STATUSES,
            )
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
        owner_id: str | None = None,
    ) -> None:
        scheduled_at = iso_timestamp(now)
        retry_at = iso_timestamp(retry_after_at)
        with self._transaction() as conn:
            row = self._require_leased_run(
                conn, stage_run_id, lease_token, at=scheduled_at, owner_id=owner_id or actor,
                permitted_statuses=_ACTIVE_LEASE_STATUSES,
            )
            next_attempt_row = conn.execute(
                """
                SELECT COALESCE(MAX(attempt), 0) + 1 AS next_attempt
                FROM stage_runs
                WHERE instance_id = ? AND stage_id = ?
                """,
                (row["instance_id"], row["stage_id"]),
            ).fetchone()
            next_attempt = int(next_attempt_row["next_attempt"])
            next_stage_run_id = f"{row['instance_id']}:{row['stage_id']}:{next_attempt}"
            next_retry_count = int(row["retry_count"] or 0) + 1
            conn.execute(
                """
                UPDATE stage_runs
                SET status = ?, failure_class = ?, failure_summary = ?,
                    retry_count = ?, retry_after_at = ?,
                    lease_owner = NULL, lease_token = NULL, lease_expires_at = NULL,
                    completed_at = ?, updated_at = ?
                WHERE stage_run_id = ? AND lease_token = ?
                """,
                (
                    _stage_status_for_failure(failure_class).value,
                    _failure_value(failure_class),
                    failure_summary,
                    next_retry_count,
                    retry_at,
                    scheduled_at,
                    scheduled_at,
                    stage_run_id,
                    lease_token,
                ),
            )
            conn.execute(
                """
                INSERT INTO stage_runs (
                  stage_run_id, instance_id, stage_id, attempt, status,
                  adapter_id, actor_ref, idempotency_key, input_hash,
                  retry_count, retry_after_at, parent_stage_run_id,
                  created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    next_stage_run_id,
                    row["instance_id"],
                    row["stage_id"],
                    next_attempt,
                    StageRunStatus.QUEUED.value,
                    row["adapter_id"],
                    row["actor_ref"],
                    row["idempotency_key"],
                    row["input_hash"],
                    next_retry_count,
                    retry_at,
                    row["stage_run_id"],
                    scheduled_at,
                    scheduled_at,
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
                    "new_stage_run_id": next_stage_run_id,
                    "new_attempt": next_attempt,
                },
                created_at=scheduled_at,
            )
            self._append_event(
                conn,
                instance_id=row["instance_id"],
                stage_run_id=next_stage_run_id,
                event_type="stage_retry_queued",
                actor=actor,
                payload={
                    "parent_stage_run_id": row["stage_run_id"],
                    "failure_class": _failure_value(failure_class),
                    "retry_after_at": retry_at,
                    "attempt": next_attempt,
                    "idempotency_key": row["idempotency_key"],
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
        owner_id: str | None = None,
    ) -> None:
        blocked_at = iso_timestamp(now)
        with self._transaction() as conn:
            row = self._require_leased_run(
                conn, stage_run_id, lease_token, at=blocked_at, owner_id=owner_id or actor,
                permitted_statuses=_ACTIVE_LEASE_STATUSES,
            )
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

    def wait_stage_run_for_human_decision(
        self,
        *,
        stage_run_id: str,
        lease_token: str,
        failure_class: FailureClass | str = FailureClass.DOMAIN_BLOCKED,
        failure_summary: str,
        now: datetime | str | None = None,
        actor: str = "runner",
        owner_id: str | None = None,
    ) -> None:
        waiting_at = iso_timestamp(now)
        with self._transaction() as conn:
            row = self._require_leased_run(
                conn, stage_run_id, lease_token, at=waiting_at, owner_id=owner_id or actor,
                permitted_statuses=_ACTIVE_LEASE_STATUSES,
            )
            conn.execute(
                """
                UPDATE stage_runs
                SET status = ?, failure_class = ?, failure_summary = ?,
                    approval_required = 1, lease_owner = NULL, lease_token = NULL,
                    lease_expires_at = NULL, updated_at = ?
                WHERE stage_run_id = ? AND lease_token = ?
                """,
                (
                    StageRunStatus.WAITING_ON_HUMAN.value,
                    _failure_value(failure_class),
                    failure_summary,
                    waiting_at,
                    stage_run_id,
                    lease_token,
                ),
            )
            self._append_event(
                conn,
                instance_id=row["instance_id"],
                stage_run_id=stage_run_id,
                event_type="stage_waiting_on_human",
                actor=actor,
                payload={
                    "failure_class": _failure_value(failure_class),
                    "failure_summary": failure_summary,
                    "approval_required": True,
                },
                created_at=waiting_at,
            )

    def find_waiting_human_stage_run(self, *, instance_id: str) -> StageRun | None:
        row = self.connection.execute(
            """
            SELECT * FROM stage_runs
            WHERE instance_id = ? AND status = ?
            ORDER BY updated_at DESC, stage_run_id DESC
            LIMIT 1
            """,
            (instance_id, StageRunStatus.WAITING_ON_HUMAN.value),
        ).fetchone()
        return self._stage_run_from_row(row) if row else None

    def record_human_decision(
        self,
        decision: HumanApprovalReceipt,
        *,
        instance_id: str,
        stage_run_id: str,
        created_at: datetime | str | None = None,
        actor: str = "kernel",
    ) -> None:
        decided_at = iso_timestamp(created_at if created_at is not None else decision.created_at)
        decision_text = _decision_value(decision.decision)
        with self._transaction() as conn:
            conn.execute(
                """
                INSERT INTO human_decisions (
                  decision_id, instance_id, stage_run_id, gate_id, decision,
                  human_ref, canonical_surface, exact_action_approved,
                  action_fingerprint, receipt_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision.approval_id,
                    instance_id,
                    stage_run_id,
                    decision.gate_id,
                    decision_text,
                    decision.human_ref,
                    decision.canonical_surface,
                    decision.exact_action_approved,
                    decision.action_fingerprint,
                    _json(decision),
                    decided_at,
                ),
            )
            self._append_event(
                conn,
                instance_id=instance_id,
                stage_run_id=stage_run_id,
                event_type="human_decision_recorded",
                actor=actor,
                payload={
                    "approval_id": decision.approval_id,
                    "gate_id": decision.gate_id,
                    "decision": decision_text,
                    "canonical_surface": decision.canonical_surface,
                },
                created_at=decided_at,
            )

    def complete_waiting_human_stage_run(
        self,
        *,
        stage_run_id: str,
        status: StageRunStatus,
        receipt_id: str | None = None,
        output_hash: str | None = None,
        failure_class: FailureClass | str | None = None,
        failure_summary: str | None = None,
        now: datetime | str | None = None,
        actor: str = "kernel",
    ) -> None:
        completed_at = iso_timestamp(now)
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT * FROM stage_runs WHERE stage_run_id = ? AND status = ?",
                (stage_run_id, StageRunStatus.WAITING_ON_HUMAN.value),
            ).fetchone()
            if row is None:
                raise LedgerConflict(
                    f"stage run {stage_run_id!r} is not waiting on a human decision"
                )
            conn.execute(
                """
                UPDATE stage_runs
                SET status = ?, receipt_id = COALESCE(?, receipt_id),
                    output_hash = COALESCE(?, output_hash),
                    failure_class = ?, failure_summary = ?,
                    completed_at = ?, updated_at = ?
                WHERE stage_run_id = ? AND status = ?
                """,
                (
                    status.value,
                    receipt_id,
                    output_hash,
                    _failure_value(failure_class),
                    failure_summary,
                    completed_at,
                    completed_at,
                    stage_run_id,
                    StageRunStatus.WAITING_ON_HUMAN.value,
                ),
            )
            self._append_event(
                conn,
                instance_id=row["instance_id"],
                stage_run_id=stage_run_id,
                event_type="human_stage_decided",
                actor=actor,
                payload={
                    "status": status.value,
                    "receipt_id": receipt_id,
                    "output_hash": output_hash,
                    "failure_class": _failure_value(failure_class),
                    "failure_summary": failure_summary,
                },
                created_at=completed_at,
            )

    # -- child sessions: a bounded, scoped, audited delegate session --------- #
    #
    # The child_sessions table already models exactly a bounded, scoped,
    # transcript-bearing, audited delegate session (delegate_kind, goal_hash,
    # allowed_scope_json, status, audit_status, transcript_ref, deadline_at).
    # A COMPANION session is a new `delegate_kind` row over this table — NOT a
    # new table. These three methods (insert / update / get) are the durable
    # spine the companion-session runner records its lifecycle onto: one row
    # per session, status transitions (open -> capped|closed), the transcript
    # as an attached ref, and the deadline the wall-clock cap derives from.

    def insert_child_session(
        self,
        *,
        child_session_id: str,
        parent_stage_run_id: str,
        delegate_kind: str,
        delegate_owner: str,
        goal_hash: str,
        context_packet_hash: str,
        allowed_scope: Any,
        expected_receipts: Any = (),
        status: str = "open",
        audit_status: str = "pending",
        invocation_id: str | None = None,
        source_thread_id: str | None = None,
        external_session_id: str | None = None,
        transcript_ref: str | None = None,
        deadline_at: datetime | str | None = None,
        created_at: datetime | str | None = None,
    ) -> None:
        """Open a child-session row under an existing parent stage run.

        Append-only at creation; lifecycle is then driven by
        ``update_child_session``. ``allowed_scope`` / ``expected_receipts`` are
        stored as canonical JSON (the scope the session may touch and the
        receipts it is expected to produce — for a companion: its recall
        namespace and its gated-candidate receipts)."""
        now = iso_timestamp(created_at)
        deadline = iso_timestamp(deadline_at) if deadline_at is not None else None
        with self._transaction() as conn:
            conn.execute(
                """
                INSERT INTO child_sessions (
                  child_session_id, parent_stage_run_id, invocation_id,
                  source_thread_id, external_session_id, delegate_kind,
                  delegate_owner, goal_hash, context_packet_hash,
                  allowed_scope_json, expected_receipts_json, status,
                  audit_status, transcript_ref, last_seen_at, deadline_at,
                  created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    child_session_id,
                    parent_stage_run_id,
                    invocation_id,
                    source_thread_id,
                    external_session_id,
                    delegate_kind,
                    delegate_owner,
                    goal_hash,
                    context_packet_hash,
                    _json(allowed_scope),
                    _json(expected_receipts),
                    status,
                    audit_status,
                    transcript_ref,
                    now,
                    deadline,
                    now,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT instance_id FROM stage_runs WHERE stage_run_id = ?",
                (parent_stage_run_id,),
            ).fetchone()
            if row is not None:
                self._append_event(
                    conn,
                    instance_id=row["instance_id"],
                    stage_run_id=parent_stage_run_id,
                    event_type="child_session_opened",
                    actor=delegate_owner,
                    payload={
                        "child_session_id": child_session_id,
                        "delegate_kind": delegate_kind,
                        "goal_hash": goal_hash,
                        "status": status,
                        "deadline_at": deadline,
                    },
                    created_at=now,
                )

    def update_child_session(
        self,
        *,
        child_session_id: str,
        status: str | None = None,
        audit_status: str | None = None,
        transcript_ref: str | None = None,
        last_seen_at: datetime | str | None = None,
        external_session_id: str | None = None,
        actor: str = "kernel",
        now: datetime | str | None = None,
    ) -> None:
        """Advance a child session's lifecycle (status / audit_status /
        transcript_ref / last_seen). Only provided fields change; the rest are
        left as-is. Raises if the session does not exist."""
        updated_at = iso_timestamp(now)
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT * FROM child_sessions WHERE child_session_id = ?",
                (child_session_id,),
            ).fetchone()
            if row is None:
                raise LedgerConflict(f"child session {child_session_id!r} does not exist")
            new_status = status if status is not None else row["status"]
            new_audit = audit_status if audit_status is not None else row["audit_status"]
            new_transcript = (
                transcript_ref if transcript_ref is not None else row["transcript_ref"]
            )
            new_last_seen = (
                iso_timestamp(last_seen_at) if last_seen_at is not None else row["last_seen_at"]
            )
            new_external = (
                external_session_id
                if external_session_id is not None
                else row["external_session_id"]
            )
            conn.execute(
                """
                UPDATE child_sessions
                SET status = ?, audit_status = ?, transcript_ref = ?,
                    last_seen_at = ?, external_session_id = ?, updated_at = ?
                WHERE child_session_id = ?
                """,
                (
                    new_status,
                    new_audit,
                    new_transcript,
                    new_last_seen,
                    new_external,
                    updated_at,
                    child_session_id,
                ),
            )
            parent_row = conn.execute(
                "SELECT instance_id FROM stage_runs WHERE stage_run_id = ?",
                (row["parent_stage_run_id"],),
            ).fetchone()
            if parent_row is not None:
                self._append_event(
                    conn,
                    instance_id=parent_row["instance_id"],
                    stage_run_id=row["parent_stage_run_id"],
                    event_type="child_session_updated",
                    actor=actor,
                    payload={
                        "child_session_id": child_session_id,
                        "status": new_status,
                        "audit_status": new_audit,
                        "transcript_ref": new_transcript,
                    },
                    created_at=updated_at,
                )

    def get_child_session(self, child_session_id: str) -> dict[str, Any] | None:
        """The full child-session row as a plain dict, or None if absent.
        ``allowed_scope_json`` / ``expected_receipts_json`` are decoded."""
        row = self.connection.execute(
            "SELECT * FROM child_sessions WHERE child_session_id = ?",
            (child_session_id,),
        ).fetchone()
        if row is None:
            return None
        return {
            "child_session_id": row["child_session_id"],
            "parent_stage_run_id": row["parent_stage_run_id"],
            "invocation_id": row["invocation_id"],
            "source_thread_id": row["source_thread_id"],
            "external_session_id": row["external_session_id"],
            "delegate_kind": row["delegate_kind"],
            "delegate_owner": row["delegate_owner"],
            "goal_hash": row["goal_hash"],
            "context_packet_hash": row["context_packet_hash"],
            "allowed_scope": _loads_json(row["allowed_scope_json"])
            if row["allowed_scope_json"]
            else None,
            "expected_receipts": _loads_json(row["expected_receipts_json"])
            if row["expected_receipts_json"]
            else None,
            "status": row["status"],
            "audit_status": row["audit_status"],
            "transcript_ref": row["transcript_ref"],
            "last_seen_at": row["last_seen_at"],
            "deadline_at": row["deadline_at"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def next_stage_attempt(self, *, instance_id: str, stage_id: str) -> int:
        row = self.connection.execute(
            """
            SELECT COALESCE(MAX(attempt), 0) + 1 AS next_attempt
            FROM stage_runs
            WHERE instance_id = ? AND stage_id = ?
            """,
            (instance_id, stage_id),
        ).fetchone()
        return int(row["next_attempt"])

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
                ORDER BY lease_expires_at, stage_run_id
                """,
            ).fetchall()
            for row in rows:
                if _coerce_datetime(row["lease_expires_at"]) > _coerce_datetime(swept_at):
                    continue
                previous_status = StageRunStatus(row["status"])
                if row["status"] in requeue_statuses:
                    if _stage_run_has_start_evidence(conn, row["stage_run_id"]):
                        action = "blocked"
                        new_status = StageRunStatus.BLOCKED.value
                        approval_required = 1
                        failure_class = FailureClass.UNKNOWN_SIDE_EFFECT_STATE
                        failure_summary = "Lease expired after adapter invocation may have started."
                    else:
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
                if action == "blocked":
                    inst = conn.execute(
                        """
                        SELECT status, current_stage_id
                        FROM workflow_instances
                        WHERE instance_id = ?
                        """,
                        (row["instance_id"],),
                    ).fetchone()
                    if (
                        inst is not None
                        and inst["current_stage_id"] == row["stage_id"]
                        and inst["status"] not in (
                            WorkflowStatus.DONE.value,
                            WorkflowStatus.CANCELLED.value,
                            WorkflowStatus.BLOCKED.value,
                            WorkflowStatus.POLICY_DENIED.value,
                        )
                    ):
                        conn.execute(
                            """
                            UPDATE workflow_instances
                            SET status = ?, updated_at = ?
                            WHERE instance_id = ?
                            """,
                            (WorkflowStatus.BLOCKED.value, swept_at, row["instance_id"]),
                        )
                        self._append_event(
                            conn,
                            instance_id=row["instance_id"],
                            stage_run_id=row["stage_run_id"],
                            event_type="workflow_blocked",
                            actor=actor,
                            payload={
                                "stage_id": row["stage_id"],
                                "stage_run_id": row["stage_run_id"],
                                "reason": failure_summary,
                                "failure_class": failure_class.value,
                                "source": "stale_lease_recovery",
                            },
                            created_at=swept_at,
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

    def terminalize_blocked_current_stages(
        self, *, now: datetime | str | None = None, actor: str = "recovery"
    ) -> list[str]:
        """Repair legacy limbo rows where the current stage is already blocked
        but the workflow instance still claims to be running."""

        repaired_at = iso_timestamp(now)
        terminal = (
            WorkflowStatus.DONE.value,
            WorkflowStatus.CANCELLED.value,
            WorkflowStatus.BLOCKED.value,
            WorkflowStatus.POLICY_DENIED.value,
        )
        blocked_stage_statuses = (
            StageRunStatus.BLOCKED.value,
            StageRunStatus.FAILED.value,
            StageRunStatus.INVALID_OUTPUT.value,
            StageRunStatus.TIMED_OUT.value,
            StageRunStatus.APPROVAL_DENIED.value,
        )
        repaired: list[str] = []
        with self._transaction() as conn:
            rows = conn.execute(
                f"""
                SELECT wi.instance_id, wi.status AS instance_status,
                       wi.current_stage_id, sr.stage_run_id,
                       sr.status AS stage_status, sr.failure_class,
                       sr.failure_summary
                FROM workflow_instances wi
                JOIN stage_runs sr
                  ON sr.instance_id = wi.instance_id
                 AND sr.stage_id = wi.current_stage_id
                WHERE wi.status NOT IN ({",".join("?" for _ in terminal)})
                  AND sr.status IN ({",".join("?" for _ in blocked_stage_statuses)})
                ORDER BY sr.updated_at, sr.stage_run_id
                """,
                (*terminal, *blocked_stage_statuses),
            ).fetchall()
            for row in rows:
                conn.execute(
                    """
                    UPDATE workflow_instances
                    SET status = ?, updated_at = ?
                    WHERE instance_id = ?
                    """,
                    (WorkflowStatus.BLOCKED.value, repaired_at, row["instance_id"]),
                )
                self._append_event(
                    conn,
                    instance_id=row["instance_id"],
                    stage_run_id=row["stage_run_id"],
                    event_type="workflow_blocked",
                    actor=actor,
                    payload={
                        "stage_id": row["current_stage_id"],
                        "stage_run_id": row["stage_run_id"],
                        "previous_status": row["instance_status"],
                        "stage_status": row["stage_status"],
                        "reason": row["failure_summary"],
                        "failure_class": row["failure_class"],
                        "source": "blocked_current_stage_reconcile",
                    },
                    created_at=repaired_at,
                )
                repaired.append(row["instance_id"])
        return repaired

    def record_late_result(
        self,
        *,
        stage_run_id: str,
        result_kind: str,
        evidence: Any,
        reported_lease_token: str | None = None,
        reported_owner: str | None = None,
        result_hash: str | None = None,
        external_ref: str | None = None,
        observed_at: datetime | str | None = None,
        actor: str = "reconciliation",
    ) -> str:
        """Store a late external result as evidence without changing stage state.

        This is intentionally not a recovery shortcut.  A completion/failure
        received after a lease was swept, stolen, or expired is useful for
        reconciliation, but it cannot authoritatively complete the old run.
        An operator or a domain-specific reconciliation workflow must decide
        what to do next from this append-only record.
        """
        recorded_at = iso_timestamp()
        observed = iso_timestamp(observed_at)
        late_result_id = f"late:{stage_run_id}:{uuid.uuid4().hex}"
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT instance_id, status FROM stage_runs WHERE stage_run_id = ?",
                (stage_run_id,),
            ).fetchone()
            if row is None:
                raise LedgerConflict(f"stage run {stage_run_id!r} does not exist")
            conn.execute(
                """
                INSERT INTO late_results (
                  late_result_id, instance_id, stage_run_id, reported_lease_token,
                  reported_owner, result_kind, result_hash, external_ref,
                  evidence_json, observed_at, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    late_result_id,
                    row["instance_id"],
                    stage_run_id,
                    reported_lease_token,
                    reported_owner,
                    result_kind,
                    result_hash,
                    external_ref,
                    _json(evidence),
                    observed,
                    recorded_at,
                ),
            )
            self._append_event(
                conn,
                instance_id=row["instance_id"],
                stage_run_id=stage_run_id,
                event_type="late_result_recorded",
                actor=actor,
                payload={
                    "late_result_id": late_result_id,
                    "result_kind": result_kind,
                    "result_hash": result_hash,
                    "external_ref": external_ref,
                    "reported_owner": reported_owner,
                    "reported_lease_token": reported_lease_token,
                    "non_authoritative": True,
                    "stage_status_at_recording": row["status"],
                },
                created_at=recorded_at,
            )
        return late_result_id

    def list_late_results(self, *, stage_run_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM late_results WHERE stage_run_id = ? ORDER BY recorded_at, late_result_id",
            (stage_run_id,),
        ).fetchall()
        return [
            {
                "late_result_id": row["late_result_id"],
                "stage_run_id": row["stage_run_id"],
                "result_kind": row["result_kind"],
                "result_hash": row["result_hash"],
                "external_ref": row["external_ref"],
                "evidence": _loads_json(row["evidence_json"]),
                "observed_at": row["observed_at"],
                "recorded_at": row["recorded_at"],
                "non_authoritative": True,
            }
            for row in rows
        ]

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

    def export_stage_run_audit(self, *, stage_run_id: str) -> dict[str, Any] | None:
        """Export enough ledger state to audit or resume one stage run."""

        row = self.connection.execute(
            """
            SELECT
              sr.*,
              wi.workflow_def_id,
              wi.workflow_version,
              wi.status AS instance_status,
              wi.current_stage_id,
              wi.input_hash AS instance_input_hash,
              wi.input_snapshot_json,
              wi.workflow_definition_json,
              wi.workflow_definition_hash,
              wi.workflow_source_uri,
              wi.recovery_epoch
            FROM stage_runs sr
            JOIN workflow_instances wi ON wi.instance_id = sr.instance_id
            WHERE sr.stage_run_id = ?
            """,
            (stage_run_id,),
        ).fetchone()
        if row is None:
            return None

        receipt_rows = self.connection.execute(
            """
            SELECT * FROM receipts
            WHERE stage_run_id = ?
            ORDER BY created_at, receipt_id
            """,
            (stage_run_id,),
        ).fetchall()
        invocation_rows = self.connection.execute(
            """
            SELECT * FROM adapter_invocations
            WHERE stage_run_id = ?
            ORDER BY started_at, invocation_id
            """,
            (stage_run_id,),
        ).fetchall()
        artifact_rows = self.connection.execute(
            """
            SELECT * FROM artifact_refs
            WHERE stage_run_id = ?
            ORDER BY created_at, artifact_id
            """,
            (stage_run_id,),
        ).fetchall()

        receipts = [_receipt_export(receipt_row) for receipt_row in receipt_rows]
        prompt_provenance = _latest_prompt_provenance(receipts)
        return {
            "schema_version": "stage-run-audit.v1",
            "workflow": {
                "id": row["workflow_def_id"],
                "version": row["workflow_version"],
            },
            "instance": {
                "instance_id": row["instance_id"],
                "status": row["instance_status"],
                "current_stage_id": row["current_stage_id"],
                "input_hash": row["instance_input_hash"],
                "input_snapshot": _loads_json(row["input_snapshot_json"])
                if row["input_snapshot_json"]
                else None,
                "recovery_epoch": row["recovery_epoch"],
            },
            "workflow_provenance": {
                "definition_hash": row["workflow_definition_hash"],
                "source_uri": row["workflow_source_uri"],
                "definition": _loads_json(row["workflow_definition_json"])
                if row["workflow_definition_json"]
                else None,
            },
            "stage_run": {
                "stage_run_id": row["stage_run_id"],
                "stage_id": row["stage_id"],
                "attempt": row["attempt"],
                "status": row["status"],
                "adapter_id": row["adapter_id"],
                "actor_ref": row["actor_ref"],
                "failure_class": row["failure_class"],
                "failure_summary": row["failure_summary"],
                "approval_required": bool(row["approval_required"]),
                "idempotency_key": row["idempotency_key"],
                "input_hash": row["input_hash"],
                "output_hash": row["output_hash"],
                "prompt_hash": row["prompt_hash"],
                "context_packet_ref": row["context_packet_ref"],
                "context_packet_hash": row["context_packet_hash"],
                "rendered_context_hash": row["rendered_context_hash"],
                "lease_seconds": row["lease_seconds"],
                "lease_source": row["lease_source"],
                "lease_source_ref": row["lease_source_ref"],
                "receipt_id": row["receipt_id"],
                "created_at": row["created_at"],
                "started_at": row["started_at"],
                "completed_at": row["completed_at"],
                "updated_at": row["updated_at"],
            },
            "provenance": {
                "prompt_hash": row["prompt_hash"]
                or (prompt_provenance or {}).get("prompt_bundle_digest"),
                "context_packet_ref": row["context_packet_ref"],
                "context_packet_hash": row["context_packet_hash"],
                "rendered_context_hash": row["rendered_context_hash"],
                "prompt_provenance": prompt_provenance,
            },
            "adapter_invocations": [_invocation_export(invocation_row) for invocation_row in invocation_rows],
            "receipts": receipts,
            "artifacts": [_artifact_export(artifact_row) for artifact_row in artifact_rows],
            "events": self.list_events(stage_run_id=stage_run_id),
        }

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

    def _ensure_stage_run_column(self, column_name: str, column_sql: str) -> None:
        self._ensure_table_column("stage_runs", column_name, column_sql)

    def _ensure_workflow_instance_column(self, column_name: str, column_sql: str) -> None:
        self._ensure_table_column("workflow_instances", column_name, column_sql)

    def _ensure_table_column(
        self,
        table_name: str,
        column_name: str,
        column_sql: str,
    ) -> None:
        columns = {
            row["name"]
            for row in self.connection.execute(f"PRAGMA table_info({table_name})").fetchall()
        }
        if column_name not in columns:
            self.connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_sql}")

    def _require_leased_run(
        self,
        conn: sqlite3.Connection,
        stage_run_id: str,
        lease_token: str,
        *,
        at: datetime | str,
        owner_id: str,
        permitted_statuses: set[StageRunStatus] | frozenset[StageRunStatus],
    ) -> sqlite3.Row:
        """Validate all authority predicates at the exact mutation time.

        This deliberately treats equality as expired: a lease authorizes times
        strictly before ``lease_expires_at``.  Callers that received an
        external result after this check must use ``record_late_result``;
        they cannot turn that evidence into a terminal state transition.
        """
        row = conn.execute(
            "SELECT * FROM stage_runs WHERE stage_run_id = ? AND lease_token = ?",
            (stage_run_id, lease_token),
        ).fetchone()
        if row is None:
            raise LedgerConflict(f"stage run {stage_run_id!r} is not leased by this token")
        if row["lease_owner"] != owner_id:
            raise LedgerConflict(f"stage run {stage_run_id!r} is not leased by owner {owner_id!r}")
        if StageRunStatus(row["status"]) not in permitted_statuses:
            allowed = ", ".join(sorted(status.value for status in permitted_statuses))
            raise LedgerConflict(
                f"stage run {stage_run_id!r} has status {row['status']!r}; expected one of {allowed}"
            )
        expires_at = row["lease_expires_at"]
        mutation_time = _coerce_datetime(iso_timestamp(at))
        if expires_at is None or _coerce_datetime(expires_at) <= mutation_time:
            raise LedgerConflict(f"stage run {stage_run_id!r} lease is expired at mutation time")
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
            lease_seconds=row["lease_seconds"],
            lease_source=row["lease_source"],
            lease_source_ref=row["lease_source_ref"],
            receipt_id=row["receipt_id"],
            failure_class=FailureClass(row["failure_class"]) if row["failure_class"] else None,
            retry_after_at=row["retry_after_at"],
            idempotency_key=row["idempotency_key"],
        )

    def _workflow_instance_from_row(self, row: sqlite3.Row) -> WorkflowInstance:
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


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _positive_int(value: Any, label: str) -> int:
    try:
        integer = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a positive integer") from exc
    if integer <= 0:
        raise ValueError(f"{label} must be a positive integer")
    return integer


def _stage_status_for_failure(failure_class: FailureClass | str) -> StageRunStatus:
    if _failure_value(failure_class) == FailureClass.INVALID_OUTPUT.value:
        return StageRunStatus.INVALID_OUTPUT
    return StageRunStatus.FAILED


def _stage_run_has_start_evidence(conn: sqlite3.Connection, stage_run_id: str) -> bool:
    invocation = conn.execute(
        """
        SELECT 1 FROM adapter_invocations
        WHERE stage_run_id = ?
        LIMIT 1
        """,
        (stage_run_id,),
    ).fetchone()
    if invocation is not None:
        return True
    event = conn.execute(
        """
        SELECT 1 FROM events
        WHERE stage_run_id = ?
          AND event_type IN ('stage_started', 'adapter_invocation_preflight')
        LIMIT 1
        """,
        (stage_run_id,),
    ).fetchone()
    return event is not None


def _receipt_export(row: sqlite3.Row) -> dict[str, Any]:
    receipt_json = _loads_json(row["receipt_json"])
    prompt_provenance = (
        receipt_json.get("prompt_provenance", {})
        if isinstance(receipt_json, dict)
        else {}
    )
    context = prompt_provenance.get("context", {}) if isinstance(prompt_provenance, dict) else {}
    return {
        "receipt_id": row["receipt_id"],
        "receipt_kind": row["receipt_kind"],
        "actor": row["actor"],
        "status": row["status"],
        "failure_class": row["failure_class"],
        "summary": row["summary"],
        "created_at": row["created_at"],
        "context_packet_ref": receipt_json.get("context_packet_ref") if isinstance(receipt_json, dict) else None,
        "prompt_hash": prompt_provenance.get("prompt_bundle_digest") if isinstance(prompt_provenance, dict) else None,
        "context_packet_hash": context.get("packet_digest") if isinstance(context, dict) else None,
        "rendered_context_hash": context.get("rendered_input_digest") if isinstance(context, dict) else None,
        "receipt": receipt_json,
    }


def _invocation_export(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "invocation_id": row["invocation_id"],
        "workflow_id": row["workflow_id"],
        "instance_id": row["instance_id"],
        "stage_run_id": row["stage_run_id"],
        "adapter_family": row["adapter_family"],
        "adapter_id": row["adapter_id"],
        "operation": row["operation"],
        "input_ref": row["input_ref"],
        "context_packet_ref": row["context_packet_ref"],
        "idempotency_key": row["idempotency_key"],
        "status": row["status"],
        "request_hash": row["request_hash"],
        "response_hash": row["response_hash"],
        "external_ref": row["external_ref"],
        "error_class": row["error_class"],
        "error_summary": row["error_summary"],
        "started_at": row["started_at"],
        "completed_at": row["completed_at"],
        "invocation": _loads_json(row["invocation_json"]),
    }


def _artifact_export(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "artifact_id": row["artifact_id"],
        "instance_id": row["instance_id"],
        "stage_run_id": row["stage_run_id"],
        "receipt_id": row["receipt_id"],
        "role": row["role"],
        "uri": row["uri"],
        "content_hash": row["content_hash"],
        "mime_type": row["mime_type"],
        "size_bytes": row["size_bytes"],
        "created_by": row["created_by"],
        "visibility": row["visibility"],
        "created_at": row["created_at"],
    }


def _latest_prompt_provenance(receipts: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    for receipt in reversed(list(receipts)):
        payload = receipt.get("receipt")
        if not isinstance(payload, dict):
            continue
        prompt_provenance = payload.get("prompt_provenance")
        if isinstance(prompt_provenance, dict) and prompt_provenance:
            return prompt_provenance
    return None


def _loads_json(value: str) -> Any:
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {"unparseable_json": True}


__all__ = [
    "LedgerConflict",
    "RecoveryAction",
    "WorkflowLedger",
    "iso_timestamp",
    "utc_now",
]
