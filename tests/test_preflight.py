from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from k3s_restore_drill.preflight import Readiness, inspect
from k3s_restore_drill.runner import CommandResult


class FakeRunner:
    def __init__(self, active: bool = False, enabled: bool = False) -> None:
        self.active = active
        self.enabled = enabled

    def run(self, argv: list[str], timeout: float) -> CommandResult:
        if "--version" in argv:
            return CommandResult(0, "k3s version v1.32.0+k3s1\n")
        if "is-active" in argv:
            return CommandResult(0 if self.active and argv[-1] == "k3s" else 3, "")
        if "is-enabled" in argv:
            return CommandResult(0 if self.enabled and argv[-1] == "k3s" else 1, "")
        raise AssertionError(argv)


class PreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.snapshot = self.root / "snapshot"
        self.token = self.root / "token"
        self.binary = self.root / "k3s"
        for path in (self.snapshot, self.token, self.binary):
            path.write_text("data", encoding="utf-8")
        self.token.chmod(0o600)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_ready_inputs_report_ready_to_try_not_restore_pass(self) -> None:
        result = inspect(self.snapshot, self.token, self.root / "work", str(self.binary), runner=FakeRunner(), system_name="Linux")
        self.assertEqual(result.readiness, Readiness.READY_TO_TRY)
        self.assertEqual(result.k3s_version, "k3s version v1.32.0+k3s1")

    def test_missing_snapshot_blocks_before_restore(self) -> None:
        result = inspect(self.root / "missing", self.token, self.root / "work", str(self.binary), runner=FakeRunner(), system_name="Linux")
        self.assertEqual(result.readiness, Readiness.BLOCKED)
        self.assertIn("SNAPSHOT_NOT_FILE", {finding.code for finding in result.findings})

    def test_active_k3s_service_blocks_dedicated_vm_requirement(self) -> None:
        result = inspect(self.snapshot, self.token, self.root / "work", str(self.binary), runner=FakeRunner(active=True), system_name="Linux")
        self.assertEqual(result.readiness, Readiness.BLOCKED)
        self.assertIn("K3S_SERVICE_ACTIVE", {finding.code for finding in result.findings})

    def test_enabled_k3s_service_blocks_reboot_risk(self) -> None:
        result = inspect(self.snapshot, self.token, self.root / "work", str(self.binary), runner=FakeRunner(enabled=True), system_name="Linux")
        self.assertEqual(result.readiness, Readiness.BLOCKED)
        self.assertIn("K3S_SERVICE_ENABLED", {finding.code for finding in result.findings})

    def test_existing_default_k3s_state_blocks_restore(self) -> None:
        legacy_state = self.root / "legacy-k3s"
        legacy_state.mkdir()
        paths = ((legacy_state, "K3S_STATE_EXISTS", "default K3s state exists at"),)
        with mock.patch("k3s_restore_drill.preflight.DEFAULT_K3S_PATHS", paths):
            result = inspect(self.snapshot, self.token, self.root / "work", str(self.binary), runner=FakeRunner(), system_name="Linux")
        self.assertEqual(result.readiness, Readiness.BLOCKED)
        self.assertIn("K3S_STATE_EXISTS", {finding.code for finding in result.findings})

    @unittest.skipUnless(os.name == "posix", "POSIX mode bits are enforced only on the supported Linux platform")
    def test_insecure_token_permissions_block(self) -> None:
        self.token.chmod(0o644)
        result = inspect(self.snapshot, self.token, self.root / "work", str(self.binary), runner=FakeRunner(), system_name="Linux")
        self.assertEqual(result.readiness, Readiness.BLOCKED)
        self.assertIn("TOKEN_PERMISSIONS", {finding.code for finding in result.findings})


if __name__ == "__main__":
    unittest.main()
