"""Serial, approval-bound orchestration for local zero-cost MARB campaigns.

Nightwatch coordinates already planned H2a slots and already approved H2b
authorizations.  It does not create either approval, grade runs, mutate the
registry, publish results, or deploy anything.  Importing this module performs
no file, environment, subprocess, provider, model, Docker, or network action.
"""
from __future__ import annotations

import argparse
import contextlib
import dataclasses
import datetime as dt
import hashlib
import ipaddress
import json
import os
import re
import stat
import sys
import urllib.parse
import uuid
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterator, NoReturn, Sequence

try:  # package import in tests; script import for the CLI
    from . import cohort_executor, cohort_runner
except ImportError:  # pragma: no cover - exercised by CLI smoke tests
    import cohort_executor  # type: ignore[no-redef]
    import cohort_runner  # type: ignore[no-redef]


CAMPAIGN_SCHEMA = "marb_nightwatch_campaign.v1"
LEDGER_SCHEMA = "marb_nightwatch_ledger.v1"
EVENT_SCHEMA = "marb_nightwatch_event.v1"
EXECUTE_LITERAL_PREFIX = "EXECUTE_MARB_NIGHTWATCH"
CONTROLLER_PATH = "harness/nightwatch.py"
HEX64 = re.compile(r"[0-9a-f]{64}")
FULL_COMMIT = re.compile(r"[0-9a-f]{40}")
CONTROL = re.compile(r"[\x00-\x1f\x7f]")
SAFE_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,191}")
WINDOWS_RESERVED_NAMES = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}
SLOT_STATUSES = {
    "pending",
    "running",
    "completed_ungraded",
    "failed",
    "partial",
    "manual_review",
}
TERMINAL_SLOT_STATUSES = {
    "completed_ungraded",
    "failed",
    "partial",
    "manual_review",
}
CAMPAIGN_STATUSES = {
    "ready",
    "running",
    "stopped",
    "completed_ungraded",
    "completed_with_failures",
    "manual_review",
}


class NightwatchError(ValueError):
    """Fail-closed campaign, ledger, or reconciliation error."""


class CampaignLockError(RuntimeError):
    """Another Nightwatch writer already owns the campaign lock."""


def _fail(message: str) -> NoReturn:
    raise NightwatchError(message)


def _canonical_json(value: Any, *, newline: bool = False) -> bytes:
    raw = json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return raw + (b"\n" if newline else b"")


def _exact_keys(value: Any, expected: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        _fail(f"{label} has unknown or missing fields")
    return value


def _decode_canonical(raw: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        _fail(f"{label} is not canonical JSON")
    if not isinstance(value, dict) or raw != _canonical_json(value, newline=True):
        _fail(f"{label} must be a canonical JSON object with one LF terminator")
    return value


def _parse_uuid4(value: Any, label: str) -> str:
    try:
        parsed = uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        _fail(f"{label} must be a UUIDv4")
    if parsed.version != 4 or str(parsed) != value:
        _fail(f"{label} must be a canonical UUIDv4")
    return str(parsed)


def _parse_utc(value: Any, label: str) -> dt.datetime:
    if not isinstance(value, str) or not re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", value
    ):
        _fail(f"{label} must be UTC text with whole-second precision")
    try:
        parsed = dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=dt.timezone.utc
        )
    except ValueError:
        _fail(f"{label} is not a valid UTC timestamp")
    return parsed


def _utc_text(value: dt.datetime) -> str:
    if not isinstance(value, dt.datetime) or value.tzinfo is None:
        _fail("Nightwatch clock must return a timezone-aware datetime")
    return value.astimezone(dt.timezone.utc).replace(microsecond=0).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _safe_relative(value: Any, label: str) -> PurePosixPath:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 500
        or "\\" in value
        or CONTROL.search(value)
    ):
        _fail(f"{label} is not a safe repository-relative path")
    relative = PurePosixPath(value)
    if (
        relative.is_absolute()
        or value != relative.as_posix()
        or any(
            part in {"", ".", ".."}
            or ":" in part
            or part.endswith((".", " "))
            or part.split(".", 1)[0].casefold() in WINDOWS_RESERVED_NAMES
            for part in relative.parts
        )
    ):
        _fail(f"{label} is not a safe repository-relative path")
    return relative


def _is_link_like(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return path.is_symlink()
    if stat.S_ISLNK(info.st_mode):
        return True
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    attributes = getattr(info, "st_file_attributes", 0)
    if reparse_flag and attributes & reparse_flag:
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction and is_junction())


def _stable_read(path: Path, label: str, *, max_bytes: int = 32 * 1024 * 1024) -> bytes:
    try:
        before = path.lstat()
        if (
            not stat.S_ISREG(before.st_mode)
            or _is_link_like(path)
            or before.st_size > max_bytes
        ):
            raise OSError
        with path.open("rb") as handle:
            raw = handle.read(max_bytes + 1)
        after = path.lstat()
    except OSError:
        _fail(f"{label} cannot be read safely")
    if (
        len(raw) > max_bytes
        or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        or len(raw) != after.st_size
    ):
        _fail(f"{label} changed while it was being read")
    return raw


def _repo_file(repo_root: Path, relative: str, label: str) -> Path:
    parts = _safe_relative(relative, label).parts
    candidate = repo_root.joinpath(*parts)
    cursor = repo_root
    try:
        for part in parts:
            cursor = cursor / part
            if _is_link_like(cursor):
                raise OSError
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(repo_root)
    except (OSError, ValueError):
        _fail(f"{label} is unavailable or escapes the repository")
    return candidate


def controller_identity() -> dict[str, str]:
    """Return the normalized source identity a campaign must approve."""
    raw = _stable_read(Path(__file__).resolve(), "Nightwatch controller source")
    try:
        normalized = (
            raw.decode("utf-8")
            .replace("\r\n", "\n")
            .replace("\r", "\n")
            .encode("utf-8")
        )
    except UnicodeDecodeError:
        _fail("Nightwatch controller source is not valid UTF-8")
    return {"path": CONTROLLER_PATH, "sha256": hashlib.sha256(normalized).hexdigest()}


def make_campaign_envelope(payload: dict[str, Any]) -> dict[str, Any]:
    """Digest-wrap reviewed campaign bytes; this does not approve execution."""
    digest = hashlib.sha256(_canonical_json(payload)).hexdigest()
    return {
        "schema": CAMPAIGN_SCHEMA,
        "campaign_sha256": digest,
        "campaign": payload,
    }


def verify_campaign_envelope(
    raw: bytes, *, expected_sha256: str
) -> dict[str, Any]:
    """Verify canonical campaign approval bytes without touching runtime state."""
    value = _exact_keys(
        _decode_canonical(raw, "Nightwatch campaign"),
        {"schema", "campaign_sha256", "campaign"},
        "Nightwatch campaign envelope",
    )
    if value.get("schema") != CAMPAIGN_SCHEMA:
        _fail("Nightwatch campaign schema is unsupported")
    campaign = value.get("campaign")
    if not isinstance(campaign, dict):
        _fail("Nightwatch campaign payload is malformed")
    digest = hashlib.sha256(_canonical_json(campaign)).hexdigest()
    if (
        not isinstance(expected_sha256, str)
        or not HEX64.fullmatch(expected_sha256)
        or value.get("campaign_sha256") != digest
        or digest != expected_sha256
    ):
        _fail("Nightwatch campaign digest does not match independent approval")
    campaign = _exact_keys(
        campaign,
        {
            "campaign_id",
            "approved_by",
            "issued_utc",
            "window",
            "source_revision",
            "controller",
            "execution",
            "policy",
            "limits",
            "slots",
        },
        "Nightwatch campaign payload",
    )
    _parse_uuid4(campaign.get("campaign_id"), "campaign_id")
    approved_by = campaign.get("approved_by")
    if (
        not isinstance(approved_by, str)
        or not approved_by.strip()
        or len(approved_by) > 100
        or not approved_by.isascii()
        or CONTROL.search(approved_by)
    ):
        _fail("approved_by must be a short public ASCII label")
    issued = _parse_utc(campaign.get("issued_utc"), "issued_utc")
    window = _exact_keys(
        campaign.get("window"),
        {"not_before_utc", "expires_utc"},
        "Nightwatch campaign window",
    )
    not_before = _parse_utc(window.get("not_before_utc"), "window.not_before_utc")
    expires = _parse_utc(window.get("expires_utc"), "window.expires_utc")
    if not_before < issued or expires <= not_before:
        _fail("Nightwatch campaign window is inconsistent")
    if not isinstance(campaign.get("source_revision"), str) or not FULL_COMMIT.fullmatch(
        campaign["source_revision"]
    ):
        _fail("Nightwatch campaign source revision is malformed")
    controller = _exact_keys(
        campaign.get("controller"),
        {"path", "sha256"},
        "Nightwatch campaign controller",
    )
    if (
        controller.get("path") != CONTROLLER_PATH
        or not isinstance(controller.get("sha256"), str)
        or not HEX64.fullmatch(controller["sha256"])
    ):
        _fail("Nightwatch campaign controller identity is malformed")
    execution = _exact_keys(
        campaign.get("execution"),
        {"execute", "max_concurrency"},
        "Nightwatch campaign execution policy",
    )
    if execution != {"execute": True, "max_concurrency": 1}:
        _fail("Nightwatch campaigns must be explicitly approved and serial")
    policy = _exact_keys(
        campaign.get("policy"),
        {
            "billing_mode",
            "zero_cost_attested",
            "max_cost_usd",
            "loopback_only",
            "credentials_allowed",
            "external_providers_allowed",
            "grading_allowed",
            "registry_mutation_allowed",
            "publication_allowed",
            "deployment_allowed",
        },
        "Nightwatch campaign safety policy",
    )
    if policy != {
        "billing_mode": "local-no-charge",
        "zero_cost_attested": True,
        "max_cost_usd": None,
        "loopback_only": True,
        "credentials_allowed": False,
        "external_providers_allowed": False,
        "grading_allowed": False,
        "registry_mutation_allowed": False,
        "publication_allowed": False,
        "deployment_allowed": False,
    }:
        _fail("Nightwatch campaign safety policy is not automation-safe")
    limits = _exact_keys(
        campaign.get("limits"),
        {"max_slots", "failure_stop_threshold"},
        "Nightwatch campaign limits",
    )
    slots = campaign.get("slots")
    if not isinstance(slots, list) or not slots:
        _fail("Nightwatch campaign must contain at least one ordered slot")
    if (
        not isinstance(limits.get("max_slots"), int)
        or isinstance(limits.get("max_slots"), bool)
        or limits["max_slots"] != len(slots)
        or not 1 <= limits["max_slots"] <= 256
        or not isinstance(limits.get("failure_stop_threshold"), int)
        or isinstance(limits.get("failure_stop_threshold"), bool)
        or not 1 <= limits["failure_stop_threshold"] <= len(slots)
    ):
        _fail("Nightwatch campaign aggregate limits are inconsistent")
    seen_run_ids: set[str] = set()
    seen_plan_paths: dict[str, str] = {}
    seen_authorization_paths: set[str] = set()
    for ordinal, slot_value in enumerate(slots, start=1):
        slot = _exact_keys(
            slot_value,
            {
                "ordinal",
                "plan_path",
                "plan_sha256",
                "planned_run_id",
                "authorization_path",
                "authorization_sha256",
                "authorization_literal",
            },
            "Nightwatch campaign slot",
        )
        if slot.get("ordinal") != ordinal:
            _fail("Nightwatch campaign slot order is inconsistent")
        plan_path = _safe_relative(slot.get("plan_path"), "campaign plan path").as_posix()
        authorization_path = _safe_relative(
            slot.get("authorization_path"), "campaign authorization path"
        ).as_posix()
        if (
            not isinstance(slot.get("plan_sha256"), str)
            or not HEX64.fullmatch(slot["plan_sha256"])
            or not isinstance(slot.get("authorization_sha256"), str)
            or not HEX64.fullmatch(slot["authorization_sha256"])
        ):
            _fail("Nightwatch campaign slot digest is malformed")
        plan_identity = plan_path.casefold()
        authorization_identity = authorization_path.casefold()
        if (
            authorization_identity in seen_authorization_paths
            or authorization_identity in seen_plan_paths
            or plan_identity in seen_authorization_paths
            or (
                plan_identity in seen_plan_paths
                and seen_plan_paths[plan_identity] != slot["plan_sha256"]
            )
        ):
            _fail("Nightwatch campaign input path identities are inconsistent")
        seen_plan_paths[plan_identity] = slot["plan_sha256"]
        seen_authorization_paths.add(authorization_identity)
        run_id = slot.get("planned_run_id")
        if (
            not isinstance(run_id, str)
            or not SAFE_RUN_ID.fullmatch(run_id)
            or run_id.casefold() in seen_run_ids
        ):
            _fail("Nightwatch campaign run identity is unsafe or duplicated")
        seen_run_ids.add(run_id.casefold())
        expected_literal = (
            f"{cohort_executor.EXECUTE_LITERAL_PREFIX}:"
            f"{slot['plan_sha256']}:{run_id}"
        )
        if slot.get("authorization_literal") != expected_literal:
            _fail("Nightwatch campaign slot confirmation literal is mismatched")
    return value


@dataclasses.dataclass(frozen=True)
class _ValidatedSlot:
    campaign_slot: dict[str, Any]
    plan_raw: bytes
    authorization_raw: bytes
    plan: dict[str, Any]
    authorization: dict[str, Any]
    seed: str
    model_id: str


def _loopback_endpoint(endpoint: str) -> bool:
    try:
        parsed = urllib.parse.urlsplit(endpoint)
        host = parsed.hostname
    except ValueError:
        return False
    if host is None:
        return False
    if host.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _authorization_midpoint(raw: bytes) -> dt.datetime:
    value = _decode_canonical(raw, "execution authorization")
    try:
        payload = value["authorization"]
        issued = _parse_utc(payload["issued_utc"], "authorization issued_utc")
        expires = _parse_utc(payload["expires_utc"], "authorization expires_utc")
    except (KeyError, TypeError):
        _fail("execution authorization payload is malformed")
    if expires <= issued:
        _fail("execution authorization window is inconsistent")
    return issued + (expires - issued) / 2


def _validate_slot_inputs(
    repo_root: Path,
    campaign: dict[str, Any],
    slot: dict[str, Any],
    *,
    now: dt.datetime,
    require_current: bool,
) -> _ValidatedSlot:
    plan_path = _repo_file(repo_root, slot["plan_path"], "campaign plan")
    authorization_path = _repo_file(
        repo_root, slot["authorization_path"], "campaign authorization"
    )
    plan_raw = _stable_read(plan_path, "campaign plan")
    authorization_raw = _stable_read(
        authorization_path, "campaign authorization", max_bytes=4 * 1024 * 1024
    )
    try:
        plan = cohort_runner.verify_plan_envelope(
            plan_raw, expected_sha256=slot["plan_sha256"]
        )
    except cohort_runner.PlanError as exc:
        _fail(str(exc))
    payload = plan["plan"]
    if payload["source"]["revision"] != campaign["source_revision"]:
        _fail("campaign plan source revision does not match the campaign")
    matching = [
        item for item in payload["runs"] if item.get("run_id") == slot["planned_run_id"]
    ]
    if len(matching) != 1:
        _fail("campaign slot is absent or ambiguous in its cohort plan")
    verification_time = now if require_current else _authorization_midpoint(authorization_raw)
    try:
        authorization = cohort_executor.verify_authorization(
            authorization_raw,
            expected_sha256=slot["authorization_sha256"],
            expected_plan_sha256=slot["plan_sha256"],
            expected_run_id=slot["planned_run_id"],
            expected_model_id=payload["cohort"]["model"]["id"],
            now=verification_time,
        )
    except cohort_executor.ExecutorError as exc:
        _fail(str(exc))
    provider = authorization["provider"]
    spend = authorization["spend"]
    if (
        provider["billing_mode"] != "local-no-charge"
        or provider["credential_env"] is not None
        or not _loopback_endpoint(provider["endpoint"])
        or spend != {
            "currency": "USD",
            "max_cost_usd": None,
            "zero_cost_attested": True,
        }
    ):
        _fail(
            "Nightwatch automation accepts only credential-free loopback local-no-charge providers"
        )
    return _ValidatedSlot(
        campaign_slot=slot,
        plan_raw=plan_raw,
        authorization_raw=authorization_raw,
        plan=plan,
        authorization=authorization,
        seed=matching[0]["seed"],
        model_id=payload["cohort"]["model"]["id"],
    )


def _resolve_repo_root(repo_root: Path) -> Path:
    try:
        resolved = repo_root.resolve(strict=True)
    except OSError:
        _fail("repository root is unavailable")
    if not resolved.is_dir() or _is_link_like(resolved):
        _fail("repository root must be a real directory")
    return resolved


def _runs_root(repo_root: Path) -> Path:
    root = repo_root / "runs"
    if root.exists() or root.is_symlink():
        if not root.is_dir() or _is_link_like(root):
            _fail("runs root must be a real directory")
    return root


def _campaign_paths(repo_root: Path, campaign_id: str) -> dict[str, Path]:
    runs_root = _runs_root(repo_root)
    nightwatch_root = runs_root / ".nightwatch"
    root = nightwatch_root / campaign_id
    for directory in (nightwatch_root, root):
        if directory.exists() or directory.is_symlink():
            if not directory.is_dir() or _is_link_like(directory):
                _fail("Nightwatch state directory is unsafe")
    return {
        "root": root,
        "ledger": root / "ledger.json",
        "events": root / "events.jsonl",
        "lock": root / "writer.lock",
    }


def _slot_ledger_entry(campaign: dict[str, Any], validated: _ValidatedSlot) -> dict[str, Any]:
    slot = validated.campaign_slot
    return {
        "ordinal": slot["ordinal"],
        "planned_run_id": slot["planned_run_id"],
        "plan_path": slot["plan_path"],
        "plan_sha256": slot["plan_sha256"],
        "authorization_path": slot["authorization_path"],
        "authorization_sha256": slot["authorization_sha256"],
        "source_revision": campaign["source_revision"],
        "seed": validated.seed,
        "model_id": validated.model_id,
        "status": "pending",
        "attempt_id": None,
        "run_dir": None,
        "run_log_sha256": None,
        "failure_category": None,
    }


def _new_ledger(
    campaign_envelope: dict[str, Any],
    validated_slots: list[_ValidatedSlot],
    now: dt.datetime,
) -> dict[str, Any]:
    campaign = campaign_envelope["campaign"]
    timestamp = _utc_text(now)
    return {
        "schema": LEDGER_SCHEMA,
        "campaign_id": campaign["campaign_id"],
        "campaign_sha256": campaign_envelope["campaign_sha256"],
        "source_revision": campaign["source_revision"],
        "status": "ready",
        "event_sequence": 0,
        "created_utc": timestamp,
        "updated_utc": timestamp,
        "limits": dict(campaign["limits"]),
        "slots": [
            _slot_ledger_entry(campaign, validated) for validated in validated_slots
        ],
    }


def _validate_ledger(
    raw: bytes,
    campaign_envelope: dict[str, Any],
    validated_slots: list[_ValidatedSlot],
) -> dict[str, Any]:
    ledger = _exact_keys(
        _decode_canonical(raw, "Nightwatch ledger"),
        {
            "schema",
            "campaign_id",
            "campaign_sha256",
            "source_revision",
            "status",
            "event_sequence",
            "created_utc",
            "updated_utc",
            "limits",
            "slots",
        },
        "Nightwatch ledger",
    )
    campaign = campaign_envelope["campaign"]
    if (
        ledger.get("schema") != LEDGER_SCHEMA
        or ledger.get("campaign_id") != campaign["campaign_id"]
        or ledger.get("campaign_sha256") != campaign_envelope["campaign_sha256"]
        or ledger.get("source_revision") != campaign["source_revision"]
        or ledger.get("limits") != campaign["limits"]
        or ledger.get("status") not in CAMPAIGN_STATUSES
        or not isinstance(ledger.get("event_sequence"), int)
        or isinstance(ledger.get("event_sequence"), bool)
        or ledger["event_sequence"] < 0
    ):
        _fail("Nightwatch ledger does not match the approved campaign")
    _parse_utc(ledger.get("created_utc"), "ledger created_utc")
    _parse_utc(ledger.get("updated_utc"), "ledger updated_utc")
    slots = ledger.get("slots")
    if not isinstance(slots, list) or len(slots) != len(validated_slots):
        _fail("Nightwatch ledger slot set does not match the campaign")
    for current, validated in zip(slots, validated_slots, strict=True):
        current = _exact_keys(
            current,
            {
                "ordinal",
                "planned_run_id",
                "plan_path",
                "plan_sha256",
                "authorization_path",
                "authorization_sha256",
                "source_revision",
                "seed",
                "model_id",
                "status",
                "attempt_id",
                "run_dir",
                "run_log_sha256",
                "failure_category",
            },
            "Nightwatch ledger slot",
        )
        expected = _slot_ledger_entry(campaign, validated)
        for key in (
            "ordinal",
            "planned_run_id",
            "plan_path",
            "plan_sha256",
            "authorization_path",
            "authorization_sha256",
            "source_revision",
            "seed",
            "model_id",
        ):
            if current.get(key) != expected[key]:
                _fail("Nightwatch ledger immutable slot identity drifted")
        if current.get("status") not in SLOT_STATUSES:
            _fail("Nightwatch ledger slot status is unsupported")
        if current.get("attempt_id") is not None:
            _parse_uuid4(current["attempt_id"], "ledger attempt_id")
        if current.get("run_dir") is not None:
            relative = _safe_relative(current["run_dir"], "ledger run directory")
            if relative.parts[0] != "runs" or len(relative.parts) != 2:
                _fail("Nightwatch ledger run directory is outside runs")
        digest = current.get("run_log_sha256")
        if digest is not None and (not isinstance(digest, str) or not HEX64.fullmatch(digest)):
            _fail("Nightwatch ledger run-log digest is malformed")
        failure = current.get("failure_category")
        if failure is not None and (
            not isinstance(failure, str)
            or not failure
            or len(failure) > 100
            or not failure.isascii()
            or CONTROL.search(failure)
        ):
            _fail("Nightwatch ledger failure category is malformed")
        empty_evidence = (
            current.get("attempt_id") is None
            and current.get("run_dir") is None
            and current.get("run_log_sha256") is None
            and current.get("failure_category") is None
        )
        if current["status"] in {"pending", "running"} and not empty_evidence:
            _fail("Nightwatch nonterminal ledger slot contains fabricated evidence")
        if current["status"] == "completed_ungraded" and (
            current.get("attempt_id") is None
            or current.get("run_dir") is None
            or current.get("run_log_sha256") is None
            or current.get("failure_category") is not None
        ):
            _fail("Nightwatch completed ledger slot is missing sealed evidence")
        if current["status"] in {"failed", "partial"} and (
            current.get("attempt_id") is None
            or current.get("run_dir") is None
            or current.get("run_log_sha256") is None
            or current.get("failure_category") is None
        ):
            _fail("Nightwatch failed ledger slot is missing retained evidence")
        if current["status"] == "manual_review" and current.get(
            "failure_category"
        ) is None:
            _fail("Nightwatch manual-review slot is missing its safe category")
    return ledger


def _write_canonical_atomic(path: Path, value: Any) -> None:
    payload = _canonical_json(value, newline=True)
    temporary = path.with_name(f"{path.name}.{uuid.uuid4()}.tmp")
    created = False
    try:
        with temporary.open("xb") as handle:
            created = True
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        created = False
    except BaseException:
        if created:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                raise NightwatchError(
                    "Nightwatch ledger temporary cleanup could not be verified"
                ) from None
        raise


@contextlib.contextmanager
def _campaign_writer_lock(path: Path) -> Iterator[None]:
    """Hold one nonblocking OS file lock for all campaign ledger writes."""
    handle = None
    try:
        campaign_root = path.parent
        nightwatch_root = campaign_root.parent
        runs_root = nightwatch_root.parent
        runs_root.mkdir(mode=0o700, exist_ok=True)
        if not runs_root.is_dir() or _is_link_like(runs_root):
            raise OSError
        nightwatch_root.mkdir(mode=0o700, exist_ok=True)
        if not nightwatch_root.is_dir() or _is_link_like(nightwatch_root):
            raise OSError
        campaign_root.mkdir(mode=0o700, exist_ok=True)
        if not campaign_root.is_dir() or _is_link_like(campaign_root):
            raise OSError
        if path.exists() or path.is_symlink():
            before = path.lstat()
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_nlink != 1
                or _is_link_like(path)
            ):
                raise OSError
        handle = path.open("a+b")
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
            os.fsync(handle.fileno())
        handle.seek(0)
        opened = os.fstat(handle.fileno())
        after = path.lstat()
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or after.st_nlink != 1
            or (opened.st_dev, opened.st_ino) != (after.st_dev, after.st_ino)
        ):
            raise OSError
    except OSError:
        if handle is not None:
            handle.close()
        raise CampaignLockError("Nightwatch campaign writer lock is unavailable") from None
    locked = False
    try:
        if os.name == "nt":
            import msvcrt

            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                raise CampaignLockError(
                    "another Nightwatch controller owns the campaign writer lock"
                ) from None
        else:  # pragma: no cover - exercised in Linux CI
            import fcntl

            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise CampaignLockError(
                    "another Nightwatch controller owns the campaign writer lock"
                ) from None
        locked = True
        yield
    finally:
        if locked:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:  # pragma: no cover - exercised in Linux CI
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        handle.close()


def _read_events(path: Path, campaign_envelope: dict[str, Any]) -> list[dict[str, Any]]:
    if not path.exists() and not path.is_symlink():
        return []
    raw = _stable_read(path, "Nightwatch event journal", max_bytes=64 * 1024 * 1024)
    if raw and not raw.endswith(b"\n"):
        _fail("Nightwatch event journal has an incomplete trailing event")
    events: list[dict[str, Any]] = []
    allowed_events = {
        "campaign_started",
        "campaign_reconciled",
        "slot_started",
        "slot_finished",
        "slot_requires_manual_review",
        "campaign_stopped",
        "campaign_cycle_completed",
    }
    campaign_slots = {
        slot["ordinal"]: slot["planned_run_id"]
        for slot in campaign_envelope["campaign"]["slots"]
    }
    for expected_sequence, line in enumerate(raw.splitlines(keepends=True), start=1):
        event = _exact_keys(
            _decode_canonical(line, "Nightwatch event"),
            {
                "schema",
                "sequence",
                "campaign_id",
                "campaign_sha256",
                "event",
                "slot_ordinal",
                "planned_run_id",
                "status",
                "at_utc",
            },
            "Nightwatch event",
        )
        if (
            event.get("schema") != EVENT_SCHEMA
            or event.get("sequence") != expected_sequence
            or event.get("campaign_id") != campaign_envelope["campaign"]["campaign_id"]
            or event.get("campaign_sha256") != campaign_envelope["campaign_sha256"]
            or event.get("event") not in allowed_events
            or event.get("status") not in SLOT_STATUSES | CAMPAIGN_STATUSES
        ):
            _fail("Nightwatch event journal is inconsistent")
        slot_event = event["event"] in {
            "slot_started",
            "slot_finished",
            "slot_requires_manual_review",
        }
        if slot_event != (event.get("slot_ordinal") is not None):
            _fail("Nightwatch event journal mixes campaign and slot identities")
        if event.get("slot_ordinal") is None:
            if event.get("planned_run_id") is not None:
                _fail("Nightwatch campaign event has a run ID without a slot")
        elif (
            not isinstance(event["slot_ordinal"], int)
            or isinstance(event["slot_ordinal"], bool)
            or not isinstance(event.get("planned_run_id"), str)
            or not SAFE_RUN_ID.fullmatch(event["planned_run_id"])
            or campaign_slots.get(event["slot_ordinal"])
            != event["planned_run_id"]
        ):
            _fail("Nightwatch slot event identity is malformed")
        if (
            (event["event"] == "slot_started" and event["status"] != "running")
            or (
                event["event"] == "slot_finished"
                and event["status"] not in TERMINAL_SLOT_STATUSES
            )
            or (
                event["event"] == "slot_requires_manual_review"
                and event["status"] != "manual_review"
            )
            or (
                event["event"] == "campaign_stopped"
                and event["status"] not in {"stopped", "manual_review"}
            )
            or (
                event["event"] == "campaign_cycle_completed"
                and event["status"]
                not in {"completed_ungraded", "completed_with_failures"}
            )
        ):
            _fail("Nightwatch event status is inconsistent with its event type")
        _parse_utc(event.get("at_utc"), "Nightwatch event at_utc")
        events.append(event)
    return events


def _append_event(
    path: Path,
    campaign_envelope: dict[str, Any],
    sequence: int,
    event: str,
    status: str,
    at: dt.datetime,
    *,
    slot: dict[str, Any] | None = None,
) -> None:
    payload = {
        "schema": EVENT_SCHEMA,
        "sequence": sequence,
        "campaign_id": campaign_envelope["campaign"]["campaign_id"],
        "campaign_sha256": campaign_envelope["campaign_sha256"],
        "event": event,
        "slot_ordinal": None if slot is None else slot["ordinal"],
        "planned_run_id": None if slot is None else slot["planned_run_id"],
        "status": status,
        "at_utc": _utc_text(at),
    }
    event_raw = _canonical_json(payload, newline=True)
    handle = None
    try:
        if path.exists() or path.is_symlink():
            before = path.lstat()
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_nlink != 1
                or _is_link_like(path)
            ):
                raise OSError
            handle = path.open("r+b")
        else:
            handle = path.open("x+b")
        opened = os.fstat(handle.fileno())
        after = path.lstat()
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or after.st_nlink != 1
            or _is_link_like(path)
            or (opened.st_dev, opened.st_ino) != (after.st_dev, after.st_ino)
        ):
            raise OSError
        handle.seek(0, os.SEEK_END)
        handle.write(event_raw)
        handle.flush()
        os.fsync(handle.fileno())
    except OSError:
        _fail("Nightwatch event journal cannot be appended safely")
    finally:
        if handle is not None:
            handle.close()


def _record_transition(
    paths: dict[str, Path],
    campaign_envelope: dict[str, Any],
    ledger: dict[str, Any],
    event: str,
    status: str,
    at: dt.datetime,
    *,
    slot: dict[str, Any] | None = None,
) -> None:
    sequence = ledger["event_sequence"] + 1
    _append_event(
        paths["events"],
        campaign_envelope,
        sequence,
        event,
        status,
        at,
        slot=slot,
    )
    ledger["event_sequence"] = sequence
    ledger["updated_utc"] = _utc_text(at)
    ledger["status"] = status if slot is None else _campaign_status(ledger)
    _write_canonical_atomic(paths["ledger"], ledger)


def _windows_identity(value: str) -> str:
    return value.rstrip(". ").casefold()


def _matching_run_directories(runs_root: Path, run_id: str) -> list[Path]:
    if not runs_root.exists():
        return []
    prefix = _windows_identity(run_id) + "--"
    try:
        return [
            candidate
            for candidate in runs_root.iterdir()
            if _windows_identity(candidate.name).startswith(prefix)
        ]
    except OSError:
        _fail("local runs directory cannot be enumerated safely")


def _manual_state(slot: dict[str, Any], category: str) -> dict[str, Any]:
    result = dict(slot)
    result.update(
        {
            "status": "manual_review",
            "failure_category": category,
        }
    )
    return result


def _reconcile_slot(repo_root: Path, slot: dict[str, Any]) -> dict[str, Any]:
    runs_root = _runs_root(repo_root)
    run_id = slot["planned_run_id"]
    claims_root = runs_root / ".slot-claims"
    if claims_root.exists() or claims_root.is_symlink():
        if not claims_root.is_dir() or _is_link_like(claims_root):
            return _manual_state(slot, "slot_claim_directory_requires_review")
    claim_path = claims_root / f"{run_id}.json"
    matching_dirs = _matching_run_directories(runs_root, run_id)
    if not claim_path.exists() and not claim_path.is_symlink():
        if matching_dirs or slot["status"] != "pending":
            return _manual_state(slot, "claim_or_ledger_state_requires_review")
        return dict(slot)
    try:
        claim = _exact_keys(
            _decode_canonical(_stable_read(claim_path, "logical slot claim"), "logical slot claim"),
            {"schema", "planned_run_id", "attempt_id"},
            "logical slot claim",
        )
        if (
            claim.get("schema") != "marb_logical_slot_claim.v1"
            or claim.get("planned_run_id") != run_id
        ):
            raise NightwatchError("logical slot claim identity is inconsistent")
        attempt_id = _parse_uuid4(claim.get("attempt_id"), "logical slot claim attempt_id")
        expected_name = f"{run_id}--{attempt_id}"
        if len(matching_dirs) != 1 or matching_dirs[0].name != expected_name:
            raise NightwatchError("logical slot claim has no unique attempt directory")
        run_dir = matching_dirs[0]
        if not run_dir.is_dir() or _is_link_like(run_dir):
            raise NightwatchError("retained attempt directory is unsafe")
        log_path = run_dir / "run_log.json"
        sidecar_path = run_dir / "run_log.sha256"
        log_raw = _stable_read(log_path, "retained run log", max_bytes=64 * 1024 * 1024)
        sidecar_raw = _stable_read(sidecar_path, "retained run-log digest", max_bytes=256)
        measured_digest = hashlib.sha256(log_raw).hexdigest()
        if sidecar_raw != (measured_digest + "\n").encode("ascii"):
            raise NightwatchError("retained run-log digest is inconsistent")
        log = _decode_canonical(log_raw, "retained run log")
        status_value = log.get("status")
        source = log.get("source")
        authorization = log.get("authorization")
        journal = log.get("journal")
        publication = log.get("publication")
        if (
            log.get("schema") != cohort_executor.RUN_LOG_SCHEMA
            or log.get("logical_run_id") != run_id
            or log.get("attempt_id") != attempt_id
            or status_value not in {"completed_ungraded", "failed", "partial"}
            or not isinstance(source, dict)
            or source.get("marb_revision") != slot["source_revision"]
            or source.get("plan_sha256") != slot["plan_sha256"]
            or not isinstance(authorization, dict)
            or authorization.get("authorization_sha256")
            != slot["authorization_sha256"]
            or not isinstance(journal, dict)
            or journal.get("status") != "sealed"
            or not isinstance(publication, dict)
            or set(publication)
            != {"graded", "registry_mutated", "board_mutated", "site_rebuilt", "deployed"}
            or any(publication.get(key) is not False for key in publication)
        ):
            raise NightwatchError("retained run log is not a sealed matching attempt")
        failure = log.get("failure")
        failure_category = None
        if status_value in {"failed", "partial"}:
            if not isinstance(failure, dict) or not isinstance(failure.get("category"), str):
                raise NightwatchError("retained failed attempt has no safe failure category")
            failure_category = failure["category"]
        result = dict(slot)
        result.update(
            {
                "status": status_value,
                "attempt_id": attempt_id,
                "run_dir": f"runs/{run_dir.name}",
                "run_log_sha256": measured_digest,
                "failure_category": failure_category,
            }
        )
        return result
    except NightwatchError:
        return _manual_state(slot, "claimed_attempt_requires_manual_review")


def _campaign_status(ledger: dict[str, Any]) -> str:
    statuses = [slot["status"] for slot in ledger["slots"]]
    if "manual_review" in statuses:
        return "manual_review"
    failures = sum(status in {"failed", "partial"} for status in statuses)
    if failures >= ledger["limits"]["failure_stop_threshold"]:
        return "stopped"
    if all(status == "completed_ungraded" for status in statuses):
        return "completed_ungraded"
    if all(status in TERMINAL_SLOT_STATUSES for status in statuses):
        return "completed_with_failures"
    if "running" in statuses:
        return "running"
    return "ready"


def _reconcile_ledger(repo_root: Path, ledger: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    changed = False
    reconciled: list[dict[str, Any]] = []
    for slot in ledger["slots"]:
        current = _reconcile_slot(repo_root, slot)
        if current != slot:
            changed = True
        reconciled.append(current)
    ledger["slots"] = reconciled
    status_value = _campaign_status(ledger)
    if ledger["status"] != status_value:
        ledger["status"] = status_value
        changed = True
    return ledger, changed


def _replay_unpublished_events(
    ledger: dict[str, Any], events: list[dict[str, Any]]
) -> bool:
    """Conservatively replay events flushed after the last ledger snapshot."""
    changed = False
    by_ordinal = {slot["ordinal"]: slot for slot in ledger["slots"]}
    for event in events[ledger["event_sequence"] :]:
        ordinal = event["slot_ordinal"]
        if ordinal is not None and event["event"] in {
            "slot_finished",
            "slot_requires_manual_review",
        }:
            slot = by_ordinal[ordinal]
            slot.update(
                {
                    "status": "manual_review",
                    "failure_category": "unpublished_terminal_event_requires_review",
                }
            )
            changed = True
        ledger["event_sequence"] = event["sequence"]
        changed = True
    return changed


def _load_state(
    repo_root: Path,
    paths: dict[str, Path],
    campaign_envelope: dict[str, Any],
    validated_slots: list[_ValidatedSlot],
    now: dt.datetime,
) -> tuple[dict[str, Any], bool, list[dict[str, Any]]]:
    if paths["ledger"].exists():
        ledger = _validate_ledger(
            _stable_read(paths["ledger"], "Nightwatch ledger"),
            campaign_envelope,
            validated_slots,
        )
        created = False
    else:
        if paths["events"].exists():
            _fail("Nightwatch event journal exists without its ledger snapshot")
        ledger = _new_ledger(campaign_envelope, validated_slots, now)
        created = True
    events = _read_events(paths["events"], campaign_envelope)
    if events:
        if ledger["event_sequence"] > events[-1]["sequence"]:
            _fail("Nightwatch ledger is ahead of its append-only event journal")
        replayed = _replay_unpublished_events(ledger, events)
    elif ledger["event_sequence"] != 0:
        _fail("Nightwatch ledger references a missing event journal")
    else:
        replayed = False
    ledger, reconciled = _reconcile_ledger(repo_root, ledger)
    return ledger, created or replayed or reconciled, events


def _validate_campaign_inputs(
    repo_root: Path,
    campaign_envelope: dict[str, Any],
    *,
    now: dt.datetime,
    require_current: bool,
) -> list[_ValidatedSlot]:
    campaign = campaign_envelope["campaign"]
    if controller_identity() != campaign["controller"]:
        _fail("executing Nightwatch controller does not match the approved source identity")
    return [
        _validate_slot_inputs(
            repo_root,
            campaign,
            slot,
            now=now,
            require_current=require_current,
        )
        for slot in campaign["slots"]
    ]


def _assert_campaign_current(campaign: dict[str, Any], now: dt.datetime) -> None:
    current = now.astimezone(dt.timezone.utc)
    not_before = _parse_utc(campaign["window"]["not_before_utc"], "window.not_before_utc")
    expires = _parse_utc(campaign["window"]["expires_utc"], "window.expires_utc")
    if current < not_before or current >= expires:
        _fail("Nightwatch campaign is outside its approved execution window")


def run_campaign(
    repo_root: Path,
    *,
    campaign_raw: bytes,
    expected_campaign_sha256: str,
    execute: bool = False,
    confirmation_literal: str | None = None,
    executor: Callable[..., dict[str, Any]] = cohort_executor.execute_plan,
    now: Callable[[], dt.datetime] | None = None,
) -> dict[str, Any]:
    """Inspect by default, or serially execute an explicitly approved campaign."""
    repo_root = _resolve_repo_root(repo_root)
    campaign_envelope = verify_campaign_envelope(
        campaign_raw, expected_sha256=expected_campaign_sha256
    )
    clock = now or (lambda: dt.datetime.now(dt.timezone.utc))
    observed_now = clock()
    _utc_text(observed_now)
    campaign = campaign_envelope["campaign"]
    if execute:
        expected_literal = f"{EXECUTE_LITERAL_PREFIX}:{expected_campaign_sha256}"
        if confirmation_literal != expected_literal:
            _fail("exact Nightwatch execution confirmation literal is missing or mismatched")
        _assert_campaign_current(campaign, observed_now)
    validated_slots = _validate_campaign_inputs(
        repo_root,
        campaign_envelope,
        now=observed_now,
        require_current=execute,
    )
    paths = _campaign_paths(repo_root, campaign["campaign_id"])
    if not execute:
        ledger, _changed, _events = _load_state(
            repo_root, paths, campaign_envelope, validated_slots, observed_now
        )
        return {
            "schema": "marb_nightwatch_status.v1",
            "mode": "read_only",
            "campaign_sha256": expected_campaign_sha256,
            "status": ledger["status"],
            "would_execute": [
                slot["planned_run_id"]
                for slot in ledger["slots"]
                if slot["status"] == "pending"
            ],
            "ledger": ledger,
        }

    with _campaign_writer_lock(paths["lock"]):
        current_now = clock()
        _assert_campaign_current(campaign, current_now)
        ledger, state_changed, events = _load_state(
            repo_root, paths, campaign_envelope, validated_slots, current_now
        )
        if not paths["ledger"].exists():
            _write_canonical_atomic(paths["ledger"], ledger)
        if not events:
            _record_transition(
                paths,
                campaign_envelope,
                ledger,
                "campaign_started",
                _campaign_status(ledger),
                current_now,
            )
        elif state_changed:
            _record_transition(
                paths,
                campaign_envelope,
                ledger,
                "campaign_reconciled",
                _campaign_status(ledger),
                current_now,
            )

        window_closed = False
        for index, validated in enumerate(validated_slots):
            slot = ledger["slots"][index]
            ledger["status"] = _campaign_status(ledger)
            if ledger["status"] in {"manual_review", "stopped"}:
                break
            if slot["status"] in TERMINAL_SLOT_STATUSES:
                continue
            if slot["status"] != "pending":
                ledger["slots"][index] = _manual_state(
                    slot, "nonterminal_ledger_state_requires_review"
                )
                _record_transition(
                    paths,
                    campaign_envelope,
                    ledger,
                    "slot_requires_manual_review",
                    "manual_review",
                    clock(),
                    slot=ledger["slots"][index],
                )
                break
            current_now = clock()
            try:
                _assert_campaign_current(campaign, current_now)
            except NightwatchError:
                window_closed = True
                break
            try:
                current_validated = _validate_slot_inputs(
                    repo_root,
                    campaign,
                    validated.campaign_slot,
                    now=current_now,
                    require_current=True,
                )
            except NightwatchError:
                ledger["slots"][index] = _manual_state(
                    slot, "authorization_or_input_drift_requires_review"
                )
                _record_transition(
                    paths,
                    campaign_envelope,
                    ledger,
                    "slot_requires_manual_review",
                    "manual_review",
                    current_now,
                    slot=ledger["slots"][index],
                )
                break
            slot["status"] = "running"
            ledger["status"] = "running"
            _record_transition(
                paths,
                campaign_envelope,
                ledger,
                "slot_started",
                "running",
                current_now,
                slot=slot,
            )
            try:
                executor(
                    repo_root,
                    plan_raw=current_validated.plan_raw,
                    expected_plan_sha256=slot["plan_sha256"],
                    authorization_raw=current_validated.authorization_raw,
                    expected_authorization_sha256=slot["authorization_sha256"],
                    authorization_literal=validated.campaign_slot["authorization_literal"],
                    planned_run_id=slot["planned_run_id"],
                    environ={},
                )
            except cohort_executor.RunExecutionError:
                pass
            except BaseException as exc:
                reconciled = _reconcile_slot(repo_root, slot)
                if reconciled["status"] not in {
                    "completed_ungraded",
                    "failed",
                    "partial",
                }:
                    reconciled = _manual_state(
                        slot, "executor_preflight_or_unsealed_failure"
                    )
                ledger["slots"][index] = reconciled
                _record_transition(
                    paths,
                    campaign_envelope,
                    ledger,
                    "slot_finished",
                    reconciled["status"],
                    clock(),
                    slot=reconciled,
                )
                if reconciled["status"] == "manual_review":
                    if not isinstance(exc, Exception):
                        raise
                    break
                if not isinstance(exc, Exception):
                    raise
                continue
            reconciled = _reconcile_slot(repo_root, slot)
            if reconciled["status"] not in {
                "completed_ungraded",
                "failed",
                "partial",
            }:
                reconciled = _manual_state(
                    slot, "executor_returned_without_sealed_attempt"
                )
            ledger["slots"][index] = reconciled
            _record_transition(
                paths,
                campaign_envelope,
                ledger,
                "slot_finished",
                reconciled["status"],
                clock(),
                slot=reconciled,
            )
            if reconciled["status"] == "manual_review":
                break

        final_status = "stopped" if window_closed else _campaign_status(ledger)
        ledger["status"] = final_status
        _record_transition(
            paths,
            campaign_envelope,
            ledger,
            "campaign_stopped" if final_status in {"stopped", "manual_review"} else "campaign_cycle_completed",
            final_status,
            clock(),
        )
        return {
            "schema": "marb_nightwatch_result.v1",
            "mode": "executed",
            "campaign_sha256": expected_campaign_sha256,
            "status": final_status,
            "ledger_path": paths["ledger"].relative_to(repo_root).as_posix(),
            "events_path": paths["events"].relative_to(repo_root).as_posix(),
            "ledger": ledger,
        }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect or serially run one explicitly approved local MARB campaign."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("status", "run"):
        command = subparsers.add_parser(name)
        command.add_argument(
            "--repo-root", type=Path, default=Path(__file__).resolve().parents[1]
        )
        command.add_argument("--campaign", type=Path, required=True)
        command.add_argument("--expected-campaign-sha256", required=True)
        if name == "run":
            command.add_argument("--confirmation-literal", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        campaign_raw = _stable_read(
            args.campaign, "Nightwatch campaign", max_bytes=4 * 1024 * 1024
        )
        result = run_campaign(
            args.repo_root,
            campaign_raw=campaign_raw,
            expected_campaign_sha256=args.expected_campaign_sha256,
            execute=args.command == "run",
            confirmation_literal=getattr(args, "confirmation_literal", None),
        )
    except (NightwatchError, CampaignLockError) as exc:
        print(f"Nightwatch error: {exc}", file=sys.stderr)
        return 2
    sys.stdout.buffer.write(_canonical_json(result, newline=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
