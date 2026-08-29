"""Fake-only regressions for the approval-bound Nightwatch controller."""
from __future__ import annotations

import ast
import contextlib
import copy
import datetime as dt
import hashlib
import json
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

from harness import nightwatch as NIGHTWATCH


NOW = dt.datetime(2026, 8, 29, 12, 0, 0, tzinfo=dt.timezone.utc)
REVISION = "a" * 40


def canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii") + b"\n"


class NightwatchTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="marb-nightwatch-test-")
        self.addCleanup(temporary.cleanup)
        self.repo = Path(temporary.name).resolve()
        self.controller = NIGHTWATCH.controller_identity()
        self.registry = self.repo / "results" / "marb_runs.json"
        self.board = self.repo / "hf" / "space" / "board.json"
        self.registry.parent.mkdir(parents=True)
        self.board.parent.mkdir(parents=True)
        self.registry.write_bytes(b'{"registry":"sentinel"}\n')
        self.board.write_bytes(b'{"board":"sentinel"}\n')
        self.slot_specs: list[dict[str, object]] = []
        self.plan_payloads: dict[str, dict[str, object]] = {}

    def make_slot(self, ordinal: int) -> dict[str, object]:
        run_id = f"nightwatch-slot-{ordinal}"
        plan_raw = canonical({"fixture": "plan", "ordinal": ordinal})
        plan_sha256 = hashlib.sha256(plan_raw).hexdigest()
        authorization_raw = canonical(
            {
                "authorization": {
                    "issued_utc": "2026-08-29T10:00:00Z",
                    "expires_utc": "2026-08-29T14:00:00Z",
                },
                "fixture": ordinal,
            }
        )
        authorization_sha256 = hashlib.sha256(authorization_raw).hexdigest()
        plan_path = f"approved/plan-{ordinal}.json"
        authorization_path = f"approved/authorization-{ordinal}.json"
        for relative, raw in (
            (plan_path, plan_raw),
            (authorization_path, authorization_raw),
        ):
            target = self.repo / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
        slot = {
            "ordinal": ordinal,
            "plan_path": plan_path,
            "plan_sha256": plan_sha256,
            "planned_run_id": run_id,
            "authorization_path": authorization_path,
            "authorization_sha256": authorization_sha256,
            "authorization_literal": (
                f"{NIGHTWATCH.cohort_executor.EXECUTE_LITERAL_PREFIX}:"
                f"{plan_sha256}:{run_id}"
            ),
        }
        self.slot_specs.append(slot)
        self.plan_payloads[plan_sha256] = {
            "plan": {
                "source": {"revision": REVISION},
                "runs": [{"run_id": run_id, "seed": f"seed-{ordinal}"}],
                "cohort": {"model": {"id": "local-fixture-model"}},
            }
        }
        return slot

    def make_campaign(
        self,
        slots: list[dict[str, object]],
        *,
        failure_stop_threshold: int | None = None,
    ) -> tuple[dict[str, object], bytes, str]:
        campaign = {
            "campaign_id": "11111111-1111-4111-8111-111111111111",
            "approved_by": "MARB operator",
            "issued_utc": "2026-08-29T10:00:00Z",
            "window": {
                "not_before_utc": "2026-08-29T11:00:00Z",
                "expires_utc": "2026-08-29T18:00:00Z",
            },
            "source_revision": REVISION,
            "controller": self.controller,
            "execution": {"execute": True, "max_concurrency": 1},
            "policy": {
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
            },
            "limits": {
                "max_slots": len(slots),
                "failure_stop_threshold": (
                    len(slots)
                    if failure_stop_threshold is None
                    else failure_stop_threshold
                ),
            },
            "slots": slots,
        }
        envelope = NIGHTWATCH.make_campaign_envelope(campaign)
        return campaign, canonical(envelope), envelope["campaign_sha256"]

    def plan_verifier(self, raw: bytes, *, expected_sha256: str):
        if hashlib.sha256(raw).hexdigest() != expected_sha256:
            raise NIGHTWATCH.cohort_runner.PlanError("plan digest drifted")
        try:
            return copy.deepcopy(self.plan_payloads[expected_sha256])
        except KeyError:
            raise NIGHTWATCH.cohort_runner.PlanError("unknown plan") from None

    @staticmethod
    def local_authorization() -> dict[str, object]:
        return {
            "provider": {
                "billing_mode": "local-no-charge",
                "credential_env": None,
                "endpoint": "http://127.0.0.1:8000/v1",
            },
            "spend": {
                "currency": "USD",
                "max_cost_usd": None,
                "zero_cost_attested": True,
            },
        }

    @contextlib.contextmanager
    def mocked_verifiers(self, *, authorization_side_effect=None):
        if authorization_side_effect is None:
            authorization = self.local_authorization()
            side_effect = None
        elif isinstance(authorization_side_effect, dict):
            authorization = authorization_side_effect
            side_effect = None
        else:
            authorization = None
            side_effect = authorization_side_effect
        with mock.patch.object(
            NIGHTWATCH, "controller_identity", return_value=self.controller
        ), mock.patch.object(
            NIGHTWATCH.cohort_runner,
            "verify_plan_envelope",
            side_effect=self.plan_verifier,
        ) as plan_verify, mock.patch.object(
            NIGHTWATCH.cohort_executor,
            "verify_authorization",
            side_effect=side_effect,
            return_value=authorization,
        ) as authorization_verify:
            yield plan_verify, authorization_verify

    def write_attempt(self, slot: dict[str, object], status: str) -> Path:
        attempt_id = str(uuid.uuid4())
        run_id = str(slot["planned_run_id"])
        claims = self.repo / "runs" / ".slot-claims"
        claims.mkdir(parents=True, exist_ok=True)
        (claims / f"{run_id}.json").write_bytes(
            canonical(
                {
                    "schema": "marb_logical_slot_claim.v1",
                    "planned_run_id": run_id,
                    "attempt_id": attempt_id,
                }
            )
        )
        run_dir = self.repo / "runs" / f"{run_id}--{attempt_id}"
        run_dir.mkdir()
        failure = None
        if status in {"failed", "partial"}:
            failure = {"category": f"synthetic_{status}"}
        run_log = {
            "schema": NIGHTWATCH.cohort_executor.RUN_LOG_SCHEMA,
            "logical_run_id": run_id,
            "attempt_id": attempt_id,
            "status": status,
            "source": {
                "marb_revision": REVISION,
                "plan_sha256": slot["plan_sha256"],
            },
            "authorization": {
                "authorization_sha256": slot["authorization_sha256"]
            },
            "journal": {"status": "sealed"},
            "publication": {
                "graded": False,
                "registry_mutated": False,
                "board_mutated": False,
                "site_rebuilt": False,
                "deployed": False,
            },
            "failure": failure,
        }
        log_raw = canonical(run_log)
        (run_dir / "run_log.json").write_bytes(log_raw)
        (run_dir / "run_log.sha256").write_bytes(
            (hashlib.sha256(log_raw).hexdigest() + "\n").encode("ascii")
        )
        return run_dir

    def campaign_call(
        self,
        raw: bytes,
        digest: str,
        *,
        execute: bool,
        executor,
        now=None,
    ):
        return NIGHTWATCH.run_campaign(
            self.repo,
            campaign_raw=raw,
            expected_campaign_sha256=digest,
            execute=execute,
            confirmation_literal=(
                f"{NIGHTWATCH.EXECUTE_LITERAL_PREFIX}:{digest}" if execute else None
            ),
            executor=executor,
            now=now or (lambda: NOW),
        )

    def test_ast_has_no_grading_publication_or_deployment_edges(self) -> None:
        tree = ast.parse(Path(NIGHTWATCH.__file__).read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".", 1)[0])
        forbidden_import_roots = {
            "grader",
            "publishing",
            "requests",
            "subprocess",
            "cloudflare",
        }
        self.assertTrue(imported.isdisjoint(forbidden_import_roots))

        forbidden_calls = {
            "grade",
            "grade_run",
            "publish",
            "deploy",
            "mutate_registry",
            "update_board",
        }
        call_names = {
            node.func.id
            if isinstance(node.func, ast.Name)
            else node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, (ast.Name, ast.Attribute))
        }
        self.assertTrue(call_names.isdisjoint(forbidden_calls))

    def test_read_only_default_makes_zero_writes_and_never_calls_executor(self) -> None:
        slots = [self.make_slot(1), self.make_slot(2)]
        _campaign, raw, digest = self.make_campaign(slots)
        before = {
            path.relative_to(self.repo).as_posix(): path.read_bytes()
            for path in self.repo.rglob("*")
            if path.is_file()
        }
        executor = mock.Mock(side_effect=AssertionError("executor must not run"))
        with self.mocked_verifiers():
            result = self.campaign_call(raw, digest, execute=False, executor=executor)
        after = {
            path.relative_to(self.repo).as_posix(): path.read_bytes()
            for path in self.repo.rglob("*")
            if path.is_file()
        }
        self.assertEqual(result["mode"], "read_only")
        self.assertEqual(result["would_execute"], [slot["planned_run_id"] for slot in slots])
        self.assertEqual(before, after)
        executor.assert_not_called()

    def test_campaign_digest_and_unknown_field_tampering_fail_closed(self) -> None:
        slot = self.make_slot(1)
        campaign, raw, digest = self.make_campaign([slot])
        tampered = json.loads(raw)
        tampered["campaign"]["approved_by"] = "different operator"
        unknown = copy.deepcopy(campaign)
        unknown["unexpected"] = True
        unknown_envelope = NIGHTWATCH.make_campaign_envelope(unknown)
        cases = (
            (canonical(tampered), digest),
            (raw, "0" * 64),
            (canonical(unknown_envelope), unknown_envelope["campaign_sha256"]),
        )
        for candidate, expected in cases:
            with self.subTest(candidate=hashlib.sha256(candidate).hexdigest()):
                with self.assertRaises(NIGHTWATCH.NightwatchError):
                    NIGHTWATCH.verify_campaign_envelope(
                        candidate, expected_sha256=expected
                    )

    def test_multiple_slots_may_share_one_identical_cohort_plan(self) -> None:
        first = self.make_slot(1)
        second = self.make_slot(2)
        second["plan_path"] = first["plan_path"]
        second["plan_sha256"] = first["plan_sha256"]
        second["authorization_literal"] = (
            f"{NIGHTWATCH.cohort_executor.EXECUTE_LITERAL_PREFIX}:"
            f"{first['plan_sha256']}:{second['planned_run_id']}"
        )
        _campaign, raw, digest = self.make_campaign([first, second])

        verified = NIGHTWATCH.verify_campaign_envelope(
            raw, expected_sha256=digest
        )

        self.assertEqual(len(verified["campaign"]["slots"]), 2)

        conflicting = json.loads(raw)
        conflicting["campaign"]["slots"][1]["plan_sha256"] = "f" * 64
        conflicting["campaign"]["slots"][1]["authorization_literal"] = (
            f"{NIGHTWATCH.cohort_executor.EXECUTE_LITERAL_PREFIX}:"
            f"{'f' * 64}:{second['planned_run_id']}"
        )
        conflicting_envelope = NIGHTWATCH.make_campaign_envelope(
            conflicting["campaign"]
        )
        with self.assertRaises(NIGHTWATCH.NightwatchError):
            NIGHTWATCH.verify_campaign_envelope(
                canonical(conflicting_envelope),
                expected_sha256=conflicting_envelope["campaign_sha256"],
            )

    def test_nonlocal_authorizations_reject_before_executor_or_ledger_write(self) -> None:
        slot = self.make_slot(1)
        _campaign, raw, digest = self.make_campaign([slot])
        variants = []
        external = self.local_authorization()
        external["provider"]["endpoint"] = "https://api.example.test/v1"
        variants.append(external)
        credentialed = self.local_authorization()
        credentialed["provider"]["credential_env"] = "SYNTHETIC_API_TOKEN"
        variants.append(credentialed)
        nonlocal_billing = self.local_authorization()
        nonlocal_billing["provider"]["billing_mode"] = "metered"
        variants.append(nonlocal_billing)

        for authorization in variants:
            executor = mock.Mock(side_effect=AssertionError("executor must not run"))
            with self.subTest(provider=authorization["provider"]), self.mocked_verifiers(
                authorization_side_effect=authorization
            ):
                with self.assertRaises(NIGHTWATCH.NightwatchError):
                    self.campaign_call(raw, digest, execute=True, executor=executor)
                executor.assert_not_called()
                self.assertFalse((self.repo / "runs" / ".nightwatch").exists())

    def test_unsafe_campaign_policy_rejects_before_verifiers_executor_or_ledger(self) -> None:
        slot = self.make_slot(1)
        campaign, _raw, _digest = self.make_campaign([slot])
        variants = (
            ("external_providers_allowed", True),
            ("credentials_allowed", True),
            ("billing_mode", "metered"),
        )
        for key, value in variants:
            changed = copy.deepcopy(campaign)
            changed["policy"][key] = value
            envelope = NIGHTWATCH.make_campaign_envelope(changed)
            raw = canonical(envelope)
            digest = envelope["campaign_sha256"]
            executor = mock.Mock(side_effect=AssertionError("executor must not run"))
            with self.subTest(policy=key), mock.patch.object(
                NIGHTWATCH.cohort_runner,
                "verify_plan_envelope",
                side_effect=AssertionError("plan verifier must not run"),
            ) as plan_verify, mock.patch.object(
                NIGHTWATCH.cohort_executor,
                "verify_authorization",
                side_effect=AssertionError("authorization verifier must not run"),
            ) as authorization_verify:
                with self.assertRaises(NIGHTWATCH.NightwatchError):
                    self.campaign_call(raw, digest, execute=True, executor=executor)
                plan_verify.assert_not_called()
                authorization_verify.assert_not_called()
                executor.assert_not_called()
                self.assertFalse((self.repo / "runs" / ".nightwatch").exists())

    def test_os_writer_lock_contention_fails_nonblocking(self) -> None:
        lock = self.repo / "runs" / ".nightwatch" / "campaign" / "writer.lock"
        with NIGHTWATCH._campaign_writer_lock(lock):
            with self.assertRaises(NIGHTWATCH.CampaignLockError):
                with NIGHTWATCH._campaign_writer_lock(lock):
                    self.fail("contended lock must not be acquired")

    def test_atomic_ledger_replace_failure_preserves_prior_snapshot(self) -> None:
        ledger = self.repo / "ledger.json"
        prior = b'{"prior":true}\n'
        ledger.write_bytes(prior)
        with mock.patch.object(NIGHTWATCH.os, "replace", side_effect=OSError("synthetic")):
            with self.assertRaises(OSError):
                NIGHTWATCH._write_canonical_atomic(ledger, {"next": True})
        self.assertEqual(ledger.read_bytes(), prior)
        self.assertEqual(list(self.repo.glob("ledger.json.*.tmp")), [])

    def test_broken_event_journal_link_fails_closed_without_touching_target(self) -> None:
        if not hasattr(os, "symlink"):
            self.skipTest("symbolic links are unavailable")
        slot = self.make_slot(1)
        campaign, raw, digest = self.make_campaign([slot])
        state_root = (
            self.repo / "runs" / ".nightwatch" / str(campaign["campaign_id"])
        )
        state_root.mkdir(parents=True)
        target = self.repo / "outside-event-target.jsonl"
        try:
            os.symlink(target, state_root / "events.jsonl")
        except OSError as exc:
            self.skipTest(f"symbolic-link creation is unavailable: {exc}")
        executor = mock.Mock(side_effect=AssertionError("executor must not run"))

        with self.mocked_verifiers(), self.assertRaises(NIGHTWATCH.NightwatchError):
            self.campaign_call(raw, digest, execute=True, executor=executor)

        executor.assert_not_called()
        self.assertFalse(target.exists())

    def test_hard_linked_event_journal_fails_closed_without_mutating_target(self) -> None:
        slot = self.make_slot(1)
        campaign, raw, digest = self.make_campaign([slot])
        state_root = (
            self.repo / "runs" / ".nightwatch" / str(campaign["campaign_id"])
        )
        state_root.mkdir(parents=True)
        target = self.repo / "outside-event-target.jsonl"
        original = b"outside sentinel\n"
        target.write_bytes(original)
        try:
            os.link(target, state_root / "events.jsonl")
        except OSError as exc:
            self.skipTest(f"hard-link creation is unavailable: {exc}")
        executor = mock.Mock(side_effect=AssertionError("executor must not run"))

        with self.mocked_verifiers(), self.assertRaises(NIGHTWATCH.NightwatchError):
            self.campaign_call(raw, digest, execute=True, executor=executor)

        executor.assert_not_called()
        self.assertEqual(target.read_bytes(), original)

    def test_event_journal_rejects_semantic_event_identity_tampering(self) -> None:
        slot = self.make_slot(1)
        campaign, raw, digest = self.make_campaign([slot])
        state_root = (
            self.repo / "runs" / ".nightwatch" / str(campaign["campaign_id"])
        )
        state_root.mkdir(parents=True)
        ledger = NIGHTWATCH._new_ledger(
            NIGHTWATCH.verify_campaign_envelope(raw, expected_sha256=digest),
            [],
            NOW,
        )
        ledger["slots"] = [
            {
                "ordinal": 1,
                "planned_run_id": slot["planned_run_id"],
                "plan_path": slot["plan_path"],
                "plan_sha256": slot["plan_sha256"],
                "authorization_path": slot["authorization_path"],
                "authorization_sha256": slot["authorization_sha256"],
                "source_revision": REVISION,
                "seed": "seed-1",
                "model_id": "local-fixture-model",
                "status": "pending",
                "attempt_id": None,
                "run_dir": None,
                "run_log_sha256": None,
                "failure_category": None,
            }
        ]
        NIGHTWATCH._write_canonical_atomic(state_root / "ledger.json", ledger)
        tampered = {
            "schema": NIGHTWATCH.EVENT_SCHEMA,
            "sequence": 1,
            "campaign_id": campaign["campaign_id"],
            "campaign_sha256": digest,
            "event": "campaign_started",
            "slot_ordinal": 1,
            "planned_run_id": slot["planned_run_id"],
            "status": "running",
            "at_utc": "2026-08-29T12:00:00Z",
        }
        (state_root / "events.jsonl").write_bytes(canonical(tampered))
        executor = mock.Mock(side_effect=AssertionError("executor must not run"))

        with self.mocked_verifiers(), self.assertRaises(NIGHTWATCH.NightwatchError):
            self.campaign_call(raw, digest, execute=True, executor=executor)

        executor.assert_not_called()

    def test_event_ahead_of_ledger_recovers_sealed_attempt_without_retry(self) -> None:
        slot = self.make_slot(1)
        campaign, raw, digest = self.make_campaign([slot])
        original_write = NIGHTWATCH._write_canonical_atomic
        ledger_writes = 0

        def interrupt_terminal_snapshot(path: Path, value: object) -> None:
            nonlocal ledger_writes
            if path.name == "ledger.json":
                ledger_writes += 1
                if ledger_writes == 4:
                    raise OSError("synthetic terminal snapshot interruption")
            original_write(path, value)

        def executor(_repo_root, **_kwargs):
            self.write_attempt(slot, "completed_ungraded")
            return {"status": "completed_ungraded"}

        with self.mocked_verifiers(), mock.patch.object(
            NIGHTWATCH,
            "_write_canonical_atomic",
            side_effect=interrupt_terminal_snapshot,
        ), self.assertRaises(OSError):
            self.campaign_call(raw, digest, execute=True, executor=executor)

        state_root = (
            self.repo / "runs" / ".nightwatch" / str(campaign["campaign_id"])
        )
        interrupted_ledger = json.loads((state_root / "ledger.json").read_bytes())
        self.assertEqual(interrupted_ledger["event_sequence"], 2)
        self.assertEqual(
            len((state_root / "events.jsonl").read_bytes().splitlines()), 3
        )

        retry = mock.Mock(side_effect=AssertionError("sealed slot must not retry"))
        with self.mocked_verifiers():
            recovered = self.campaign_call(
                raw, digest, execute=True, executor=retry
            )

        retry.assert_not_called()
        self.assertEqual(recovered["status"], "completed_ungraded")
        self.assertEqual(
            recovered["ledger"]["slots"][0]["status"], "completed_ungraded"
        )

    def test_campaign_input_paths_reject_windows_ambiguous_components(self) -> None:
        slot = self.make_slot(1)
        campaign, _raw, _digest = self.make_campaign([slot])
        for unsafe in ("approved/plan.json.", "approved/trailing-space ", "CON/data.json"):
            changed = copy.deepcopy(campaign)
            changed["slots"][0]["plan_path"] = unsafe
            envelope = NIGHTWATCH.make_campaign_envelope(changed)
            with self.subTest(path=unsafe), self.assertRaises(NIGHTWATCH.NightwatchError):
                NIGHTWATCH.verify_campaign_envelope(
                    canonical(envelope),
                    expected_sha256=envelope["campaign_sha256"],
                )

    def test_sealed_claim_reconciliation_prevents_duplicate_execution(self) -> None:
        slot = self.make_slot(1)
        _campaign, raw, digest = self.make_campaign([slot])
        self.write_attempt(slot, "completed_ungraded")
        executor = mock.Mock(side_effect=AssertionError("terminal slot must not retry"))
        with self.mocked_verifiers():
            result = self.campaign_call(raw, digest, execute=True, executor=executor)
        executor.assert_not_called()
        self.assertEqual(result["status"], "completed_ungraded")
        self.assertEqual(result["ledger"]["slots"][0]["status"], "completed_ungraded")

    def test_claim_without_valid_sealed_log_requires_manual_review(self) -> None:
        slot = self.make_slot(1)
        _campaign, raw, digest = self.make_campaign([slot])
        run_id = str(slot["planned_run_id"])
        attempt_id = str(uuid.uuid4())
        claims = self.repo / "runs" / ".slot-claims"
        claims.mkdir(parents=True)
        (claims / f"{run_id}.json").write_bytes(
            canonical(
                {
                    "schema": "marb_logical_slot_claim.v1",
                    "planned_run_id": run_id,
                    "attempt_id": attempt_id,
                }
            )
        )
        (self.repo / "runs" / f"{run_id}--{attempt_id}").mkdir()
        executor = mock.Mock(side_effect=AssertionError("manual slot must not retry"))
        with self.mocked_verifiers():
            result = self.campaign_call(raw, digest, execute=True, executor=executor)
        executor.assert_not_called()
        self.assertEqual(result["status"], "manual_review")
        self.assertEqual(result["ledger"]["slots"][0]["status"], "manual_review")

    def test_failed_and_partial_retained_slots_never_retry(self) -> None:
        slots = [self.make_slot(1), self.make_slot(2)]
        _campaign, raw, digest = self.make_campaign(
            slots, failure_stop_threshold=2
        )
        self.write_attempt(slots[0], "failed")
        self.write_attempt(slots[1], "partial")
        executor = mock.Mock(side_effect=AssertionError("terminal slots must not retry"))
        with self.mocked_verifiers():
            result = self.campaign_call(raw, digest, execute=True, executor=executor)
        executor.assert_not_called()
        self.assertEqual(result["status"], "stopped")
        self.assertEqual(
            [slot["status"] for slot in result["ledger"]["slots"]],
            ["failed", "partial"],
        )

    def test_dispatch_is_serial_ordered_and_preserves_public_state(self) -> None:
        slots = [self.make_slot(1), self.make_slot(2), self.make_slot(3)]
        _campaign, raw, digest = self.make_campaign(slots)
        by_run_id = {slot["planned_run_id"]: slot for slot in slots}
        order: list[str] = []
        active = 0
        max_active = 0
        registry_before = self.registry.read_bytes()
        board_before = self.board.read_bytes()

        def executor(_repo_root, **kwargs):
            nonlocal active, max_active
            run_id = kwargs["planned_run_id"]
            active += 1
            max_active = max(max_active, active)
            order.append(run_id)
            self.write_attempt(by_run_id[run_id], "completed_ungraded")
            active -= 1
            return {"status": "completed_ungraded"}

        with self.mocked_verifiers():
            result = self.campaign_call(raw, digest, execute=True, executor=executor)
        self.assertEqual(order, [slot["planned_run_id"] for slot in slots])
        self.assertEqual(max_active, 1)
        self.assertEqual(result["status"], "completed_ungraded")
        self.assertEqual(self.registry.read_bytes(), registry_before)
        self.assertEqual(self.board.read_bytes(), board_before)

    def test_expired_authorization_is_rejected_during_dispatch_revalidation(self) -> None:
        slot = self.make_slot(1)
        _campaign, raw, digest = self.make_campaign([slot])
        executor = mock.Mock(side_effect=AssertionError("expired slot must not execute"))
        authorization_checks = [
            self.local_authorization(),
            NIGHTWATCH.cohort_executor.ExecutorError("authorization expired"),
        ]
        with self.mocked_verifiers(authorization_side_effect=authorization_checks):
            result = self.campaign_call(raw, digest, execute=True, executor=executor)
        executor.assert_not_called()
        self.assertEqual(result["status"], "manual_review")
        self.assertEqual(
            result["ledger"]["slots"][0]["failure_category"],
            "authorization_or_input_drift_requires_review",
        )

    def test_input_source_drift_is_rejected_before_executor(self) -> None:
        slot = self.make_slot(1)
        _campaign, raw, digest = self.make_campaign([slot])
        executor = mock.Mock(side_effect=AssertionError("drifted slot must not execute"))
        clock_calls = 0

        def clock() -> dt.datetime:
            nonlocal clock_calls
            clock_calls += 1
            if clock_calls == 3:
                (self.repo / str(slot["plan_path"])).write_bytes(
                    canonical({"fixture": "drifted"})
                )
            return NOW

        with self.mocked_verifiers():
            result = self.campaign_call(
                raw, digest, execute=True, executor=executor, now=clock
            )
        executor.assert_not_called()
        self.assertEqual(result["status"], "manual_review")
        self.assertEqual(
            result["ledger"]["slots"][0]["failure_category"],
            "authorization_or_input_drift_requires_review",
        )

    def test_operator_cancellation_is_journaled_and_propagated(self) -> None:
        slot = self.make_slot(1)
        _campaign, raw, digest = self.make_campaign([slot])

        def cancel(_repo_root, **_kwargs):
            raise KeyboardInterrupt

        with self.mocked_verifiers(), self.assertRaises(KeyboardInterrupt):
            self.campaign_call(raw, digest, execute=True, executor=cancel)

        with self.mocked_verifiers():
            status = self.campaign_call(
                raw,
                digest,
                execute=False,
                executor=mock.Mock(side_effect=AssertionError("must not execute")),
            )
        self.assertEqual(status["status"], "manual_review")
        self.assertEqual(status["ledger"]["slots"][0]["status"], "manual_review")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
