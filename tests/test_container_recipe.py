"""Static regressions for the offline H2b OCI runtime recipe.

These tests parse tracked source only. They never invoke Docker, a provider, or
a model, and they do not claim that a real image has been built or qualified.
"""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from harness import cohort_executor, cohort_runner, isolated_container


REPO = Path(__file__).resolve().parents[1]
CONTAINER = REPO / "harness" / "container"
DOCKERFILE = CONTAINER / "Dockerfile"
LOCK = CONTAINER / "requirements.lock"
LIMITER = CONTAINER / "run_limited.py"
NOTES = CONTAINER / "README.md"
HARNESS_NOTES = REPO / "harness" / "README.md"
EXECUTOR_NOTES = REPO / "harness" / "H2B_EXECUTOR.md"
CHANGELOG = REPO / "CHANGELOG.md"
GITIGNORE = REPO / ".gitignore"
DOCKERIGNORE = CONTAINER / ".dockerignore"
GITATTRIBUTES = REPO / ".gitattributes"

LF_PINNED_INPUTS = (
    "harness/cohort_runner.py",
    "harness/cohort_executor.py",
    "harness/provider_transport.py",
    "harness/isolated_container.py",
    "harness/container/run_limited.py",
    "harness/container/Dockerfile",
    "harness/container/requirements.lock",
)


class ContainerRecipeTests(unittest.TestCase):
    def test_runtime_manifest_exactly_matches_executor_preflight(self) -> None:
        text = DOCKERFILE.read_text(encoding="utf-8")
        expected = {
            **cohort_executor.EXPECTED_RUNTIME,
            "cadclaw_pin_basis": cohort_executor.CADCLAW_PIN_BASIS,
            "run_limiter_sha256": "$RUN_LIMITER_SHA256",
        }
        encoded = json.dumps(expected, sort_keys=True, separators=(",", ":"))
        escaped = encoded.replace('"', '\\"')
        self.assertIn(
            f'''printf '%s\\n' "{escaped}" > /opt/marb/runtime.json''',
            text,
        )
        self.assertIn("json.loads(Path('/opt/marb/runtime.json').read_text()) == expected", text)
        self.assertIn("hashlib.sha256(Path('/opt/marb/run_limited.py').read_bytes())", text)

    def test_lock_is_exact_unique_and_matches_frozen_core(self) -> None:
        rows = [
            line.strip()
            for line in LOCK.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        ]
        self.assertTrue(rows)
        self.assertTrue(all(re.fullmatch(r"[a-z0-9][a-z0-9._-]*==[^\s<>=!~]+", row) for row in rows))
        names = [row.split("==", 1)[0].replace("_", "-").lower() for row in rows]
        self.assertEqual(len(names), len(set(names)))
        versions = dict(row.split("==", 1) for row in rows)
        self.assertEqual(versions["cadclaw"], cohort_executor.EXPECTED_RUNTIME["cadclaw_version"])
        self.assertEqual(versions["cadquery"], cohort_executor.EXPECTED_RUNTIME["cadquery_version"])
        self.assertEqual(
            versions["cadquery-ocp"],
            cohort_executor.EXPECTED_RUNTIME["cadquery_ocp_version"],
        )

    def test_recipe_binds_exact_audited_wheel_and_offline_inputs(self) -> None:
        text = DOCKERFILE.read_text(encoding="utf-8")
        wheel = "/opt/marb/wheelhouse/cadclaw-0.10.0-py3-none-any.whl"
        self.assertIn('cad == [\'cadclaw-0.10.0-py3-none-any.whl\']', text)
        self.assertIn(f'"$CADCLAW_WHEEL_SHA256" {wheel}', text)
        self.assertIn(f"--no-deps --only-binary=:all: {wheel}", text)
        self.assertIn("re.fullmatch(r'([0-9a-f]{64})  (wheelhouse/", text)
        self.assertIn("not p.is_symlink()", text)
        self.assertIn("len(wheel_entries) == len(lines)", text)
        self.assertIn("sorted(wheel_entries) == sorted(wheel_paths)", text)
        self.assertLess(
            text.index("sorted(wheel_entries) == sorted(wheel_paths)"),
            text.index("sha256sum -c runtime-wheelhouse.sha256"),
        )
        self.assertIn("observed == os.environ['CADCLAW_WHEEL_SHA256']", text)
        self.assertIn("dist.read_text('RECORD')", text)
        self.assertIn("base64.urlsafe_b64decode", text)
        self.assertIn("--no-index --only-binary=:all:", text)
        self.assertIn("/opt/marb/build-provenance.json", text)
        self.assertIn("ARG DOCKERFILE_SHA256", text)
        self.assertIn("ARG CONTEXT_DOCKERIGNORE_SHA256", text)
        self.assertIn("ARG BUILD_CONTEXT_MANIFEST_SHA256", text)
        self.assertIn('org.sunnyday.marb.dockerfile-sha256="${DOCKERFILE_SHA256}"', text)
        self.assertIn(
            'org.sunnyday.marb.context-dockerignore-sha256="${CONTEXT_DOCKERIGNORE_SHA256}"',
            text,
        )
        self.assertIn(
            'org.sunnyday.marb.build-context-manifest-sha256="${BUILD_CONTEXT_MANIFEST_SHA256}"',
            text,
        )
        self.assertIn("COPY build-context.sha256 /opt/marb/build-context.sha256", text)
        self.assertIn("marb_h2b_image_build_provenance.v2", text)
        for field in (
            "dockerfile_sha256",
            "context_dockerignore_sha256",
            "build_context_manifest_sha256",
        ):
            with self.subTest(field=field):
                self.assertIn(field, text)
        self.assertIn("context_entries['Dockerfile'] == os.environ['DOCKERFILE_SHA256']", text)
        self.assertIn(
            "context_entries['.dockerignore'] == os.environ['CONTEXT_DOCKERIGNORE_SHA256']",
            text,
        )
        self.assertIn('org.sunnyday.marb.base-image="${PYTHON_BASE_IMAGE}"', text)
        self.assertNotRegex(text, r"@sha256:[0-9a-f]{64}")
        self.assertNotIn("curl ", text)
        self.assertNotIn("git clone", text)

        notes = NOTES.read_text(encoding="utf-8")
        self.assertIn("RepoDigest identifies the output", notes)
        self.assertIn("non-self-referential context manifest", notes)
        self.assertIn("sha256sum Dockerfile .dockerignore requirements.lock", notes)
        self.assertIn("sha256sum -c build-context.sha256", notes)
        self.assertIn("--build-arg \"DOCKERFILE_SHA256=$dockerfileSha\"", notes)
        self.assertIn(
            "--build-arg \"CONTEXT_DOCKERIGNORE_SHA256=$contextDockerignoreSha\"",
            notes,
        )
        self.assertIn(
            "--build-arg \"BUILD_CONTEXT_MANIFEST_SHA256=$buildContextManifestSha\"",
            notes,
        )
        self.assertIn("private-build-record.json", notes)

    def test_limiter_and_truthful_manual_gate_are_part_of_recipe(self) -> None:
        text = DOCKERFILE.read_text(encoding="utf-8")
        self.assertTrue(LIMITER.is_file())
        self.assertIn("COPY run_limited.py /opt/marb/run_limited.py", text)
        notes = NOTES.read_text(encoding="utf-8")
        self.assertIn("no approved OCI RepoDigest yet", notes)
        self.assertIn("Mandatory no-provider runtime smoke", notes)
        self.assertIn("CI does not build, pull, push, or run Docker", notes)
        self.assertIn("only writable host bind exposed to the child", notes)
        self.assertIn("`/marb-export/workspace.tar`", notes)

    def test_private_build_inputs_are_anchored_and_ignored(self) -> None:
        ignored = set(GITIGNORE.read_text(encoding="utf-8").splitlines())
        self.assertTrue(
            {
                "/harness/container/wheelhouse/",
                "/harness/container/wheelhouse.sha256",
                "/harness/container/build-context.sha256",
                "/harness/container/private-build-record.json",
            }.issubset(ignored)
        )
        gitignore_notes = GITIGNORE.read_text(encoding="utf-8")
        self.assertIn("tracked default-deny .dockerignore", gitignore_notes)
        self.assertIn("harness/container/private-build-record.json", NOTES.read_text(encoding="utf-8"))
        docker_ignored = set(DOCKERIGNORE.read_text(encoding="utf-8").splitlines())
        self.assertIn("**", docker_ignored)
        self.assertFalse(any(line.startswith("!") for line in docker_ignored))
        self.assertTrue(
            {
                "private-build-record.json",
                "wheelhouse/",
                "wheelhouse.sha256",
                ".env",
                "*secret*",
                "*credential*",
                "*token*",
                "*.key",
                "*.pem",
            }.issubset(docker_ignored)
        )

    def test_byte_hashed_h2b_inputs_are_lf_pinned_for_fresh_checkouts(self) -> None:
        attributes = set(GITATTRIBUTES.read_text(encoding="utf-8").splitlines())
        for relative in LF_PINNED_INPUTS:
            with self.subTest(relative=relative):
                self.assertIn(f"/{relative} text eol=lf", attributes)
                self.assertNotIn(b"\r", (REPO / relative).read_bytes())

    def test_operator_example_uses_only_the_canonical_prompt_variant(self) -> None:
        self.assertEqual(cohort_runner.CANONICAL_PROMPT_VARIANT, "frozen-core")
        notes = HARNESS_NOTES.read_text(encoding="utf-8")
        self.assertIn("--prompt-variant frozen-core", notes)
        self.assertNotIn("--prompt-variant frozen-v012", notes)

    def test_planner_stdout_capture_preserves_canonical_utf8_bytes(self) -> None:
        notes = HARNESS_NOTES.read_text(encoding="utf-8")
        normalized = " ".join(notes.split())
        self.assertIn("PowerShell 7.4", notes)
        self.assertIn("exact canonical UTF-8 stdout bytes", normalized)
        self.assertIn("final newline", normalized)
        self.assertIn("native `>` redirection", normalized)
        self.assertIn("`Out-File`", notes)
        self.assertIn("`Set-Content`", notes)
        self.assertIn("planner itself remains read-only", normalized)
        self.assertIn(".Hash.ToLowerInvariant()", notes)

    def test_operator_digest_examples_normalize_powershell_hashes_to_lowercase(self) -> None:
        executor_notes = EXECUTOR_NOTES.read_text(encoding="utf-8")
        container_notes = NOTES.read_text(encoding="utf-8")
        self.assertIn(
            "$dockerExecutableSha = (Get-FileHash -LiteralPath $dockerExecutable "
            "-Algorithm SHA256).Hash.ToLowerInvariant()",
            executor_notes,
        )
        self.assertIn(
            "$gitExecutableSha = (Get-FileHash -LiteralPath $gitExecutable "
            "-Algorithm SHA256).Hash.ToLowerInvariant()",
            executor_notes,
        )
        for variable in (
            "$lockSha",
            "$runLimiterSha",
            "$wheelhouseManifestSha",
            "$cadclawWheelSha",
            "$dockerfileSha",
            "$contextDockerignoreSha",
            "$buildContextManifestSha",
            "$dockerExeSha",
        ):
            with self.subTest(variable=variable):
                line = next(
                    row for row in container_notes.splitlines() if row.startswith(f"{variable} =")
                )
                self.assertIn(".Hash.ToLowerInvariant()", line)

    def test_operator_mount_contract_matches_isolation_constants(self) -> None:
        self.assertEqual(isolated_container.CONTAINER_HOST_WORKSPACE, "/marb-host-workspace")
        self.assertEqual(isolated_container.CONTAINER_INPUT_ROOT, "/marb-input")
        self.assertEqual(isolated_container.CONTAINER_INPUT, "/workspace/kit")
        self.assertEqual(isolated_container.CONTAINER_EXPORT, "/marb-export")
        notes = EXECUTOR_NOTES.read_text(encoding="utf-8")
        for path in (
            "/marb-host-workspace",
            "/marb-input",
            "/workspace/kit",
            "/marb-export/workspace.tar",
        ):
            with self.subTest(path=path):
                self.assertIn(f"`{path}`", notes)

    def test_operator_docs_keep_display_labels_out_of_model_identity(self) -> None:
        notes = EXECUTOR_NOTES.read_text(encoding="utf-8")
        self.assertIn("`cell_label` and `model.name` are operator-supplied display labels", notes)
        self.assertIn("exact authorized `model.id`", notes)
        self.assertIn("no model-alias policy", notes)

    def test_operator_docs_disclose_attempt_budget_and_negative_modality_scope(self) -> None:
        notes = EXECUTOR_NOTES.read_text(encoding="utf-8")
        normalized = " ".join(notes.split())
        self.assertIn("exactly one logical slot and its one retained attempt", normalized)
        self.assertIn("`max_cost_usd`", normalized)
        self.assertIn(
            "separately approved aggregate Nightwatch campaign authorization",
            normalized,
        )
        self.assertIn("reviewed per-slot H2b authorizations", normalized)
        self.assertIn("H2b does not implement aggregate authority or coordination", normalized)
        self.assertIn(
            "implements the separate serial local ledger and concurrency controller",
            normalized,
        )
        self.assertIn("`execution_modality`", normalized)
        self.assertIn("no native image-view tool", normalized)
        self.assertIn(
            "staged image bytes may be inspected only through model-authored Python",
            normalized,
        )
        self.assertIn("`vision_attested` is `false`", normalized)
        self.assertIn("`/marb-export/workspace.tar`", normalized)
        self.assertIn("runs/.slot-claims/<planned-run-id>.json", normalized)
        self.assertNotIn("slot.reservation", normalized)

    def test_operator_docs_scope_slot_claims_to_one_checkout(self) -> None:
        for path in (EXECUTOR_NOTES, HARNESS_NOTES):
            with self.subTest(path=path.name):
                normalized = " ".join(path.read_text(encoding="utf-8").split())
                self.assertIn("only within the same checkout", normalized)
                self.assertIn("`runs/` is ignored", normalized)
                self.assertIn("not a global lock", normalized)
                self.assertIn("Cross-clone and cross-host uniqueness", normalized)
                self.assertIn("operator/campaign-ledger coordination", normalized)
                self.assertIn("later registry/publication validation", normalized)

    def test_operator_docs_distinguish_owned_paths_from_captured_artifacts(self) -> None:
        for path in (EXECUTOR_NOTES, HARNESS_NOTES):
            with self.subTest(path=path.name):
                normalized = " ".join(path.read_text(encoding="utf-8").split())
                self.assertIn("`marb_plan_output_binding.v2`", normalized)
                self.assertIn("`executor_owned_outputs`", normalized)
                self.assertIn("reserved/bound output paths", normalized)
                self.assertIn("only `artifacts`", normalized)
                self.assertIn("failed", normalized)
                self.assertIn("partial", normalized)

    def test_operator_docs_cover_authenticated_git_executable_boundary(self) -> None:
        for path in (EXECUTOR_NOTES, HARNESS_NOTES):
            with self.subTest(path=path.name):
                normalized = " ".join(path.read_text(encoding="utf-8").split())
                self.assertIn("`marb_execution_authorization.v2`", normalized)
                self.assertIn("normalized absolute", normalized)
                self.assertIn("schema validation bind", normalized)
                self.assertIn(
                    "`validate-authorization` validates those declared bindings "
                    "but does not inspect the host executable",
                    normalized,
                )
                self.assertIn(
                    "Execution preflight verifies the actual host path chain "
                    "and executable file digest",
                    normalized,
                )
                self.assertIn("symlink/reparse", normalized)
                self.assertIn(
                    "Production committed-blob reads use only `authorized_git`; "
                    "no injected blob reader",
                    normalized,
                )
                self.assertIn(
                    "immediately before every committed-blob spawn", normalized
                )
                self.assertIn(
                    "each path component and the executable with native handles",
                    normalized,
                )
                self.assertIn("hash-to-process-creation interval", normalized)
                self.assertIn("exact resolved repository", normalized)
                self.assertIn("`safe.directory`", normalized)
                self.assertIn("never `*`", normalized)
                self.assertIn(
                    "closing the hash-to-spawn replacement window", normalized
                )
                self.assertIn("authorized absolute path as `argv[0]`", normalized)
                self.assertIn("never cwd or ambient `PATH`", normalized)
                self.assertIn("`--no-replace-objects`", normalized)
                self.assertIn("`GIT_NO_REPLACE_OBJECTS=1`", normalized)
                self.assertIn("stdout bytes", normalized)
                self.assertIn("elapsed timeout", normalized)
                self.assertIn("neutral", normalized)
                self.assertIn("basename/label", normalized)
                self.assertIn("verified SHA-256", normalized)

        notes = EXECUTOR_NOTES.read_text(encoding="utf-8")
        self.assertNotIn("marb_execution_authorization.v1", notes)
        self.assertIn('$gitExecutable = "C:/Program Files/Git/cmd/git.exe"', notes)
        self.assertIn("--git-executable $gitExecutable", notes)
        self.assertIn("--git-executable-sha256 $gitExecutableSha", notes)

        parser = cohort_executor._parser()
        subparsers = next(
            action
            for action in parser._actions
            if isinstance(action, cohort_executor.argparse._SubParsersAction)
        )
        template_options = {
            option
            for action in subparsers.choices["authorization-template"]._actions
            for option in action.option_strings
        }
        self.assertIn("--git-executable", template_options)
        self.assertIn("--git-executable-sha256", template_options)

    def test_operator_docs_expose_complete_inputs_and_source_capture_boundary(self) -> None:
        notes = EXECUTOR_NOTES.read_text(encoding="utf-8")
        self.assertIn("`container_root` as `/marb-input`", notes)
        self.assertIn("kit compatibility root as `/workspace/kit`", notes)
        self.assertIn("every safe regular file retained in the authored workspace", notes)
        self.assertIn("`MARB_SOURCE_MANIFEST.json`", notes)
        self.assertIn("`MARB_EXECUTION_STATUS.json`", notes)
        normalized = " ".join(notes.split())
        self.assertIn("content-bound", normalized)
        for change in ("addition", "removal", "replacement", "mutation"):
            with self.subTest(change=change):
                self.assertIn(change, normalized)

    def test_docs_distinguish_trusted_host_broker_from_untrusted_child_writes(self) -> None:
        for path in (EXECUTOR_NOTES, HARNESS_NOTES, CHANGELOG):
            with self.subTest(path=path.name):
                normalized = " ".join(path.read_text(encoding="utf-8").split())
                self.assertIn("provider `write_file`", normalized.casefold())
                self.assertIn("trusted host", normalized.casefold())
                self.assertIn("retained", normalized.casefold())
                self.assertIn("tmpfs", normalized)
                self.assertRegex(
                    normalized,
                    r"(?:writable host bind|writable host export-file bind)",
                )

        container_notes = " ".join(NOTES.read_text(encoding="utf-8").split())
        self.assertIn("untrusted child-process filesystem writes", container_notes)
        self.assertIn("only writable host bind exposed to the child", container_notes)

    def test_operator_docs_cover_every_cli_stage_without_execution(self) -> None:
        parser = cohort_executor._parser()
        subparsers = next(
            action
            for action in parser._actions
            if isinstance(action, cohort_executor.argparse._SubParsersAction)
        )
        self.assertEqual(
            set(subparsers.choices),
            {
                "authorization-template",
                "seal-authorization",
                "validate-authorization",
                "execute",
            },
        )
        notes = EXECUTOR_NOTES.read_text(encoding="utf-8")
        for command in subparsers.choices:
            with self.subTest(command=command):
                self.assertIn(f"cohort_executor.py {command}", notes)
        self.assertIn("output basenames only", notes)
        self.assertIn("repository-relative `runs/<attempt>`", notes)
        self.assertIn("`retained_failure`", notes)
        self.assertIn("exits 2", notes)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
