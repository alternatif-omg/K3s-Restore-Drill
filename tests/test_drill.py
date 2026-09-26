from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

from k3s_restore_drill.drill import verify
from k3s_restore_drill.preflight import PreflightResult, Readiness
from k3s_restore_drill.runner import CommandResult


class FakeProcess:
    class Output:
        def read(self) -> str:
            return ""

    stdout = Output()

    def __init__(self) -> None:
        self.running = True

    def poll(self) -> int | None:
        return None if self.running else 0

    def terminate(self) -> None:
        self.running = False

    def wait(self, timeout: float) -> int:
        self.running = False
        return 0

    def kill(self) -> None:
        self.running = False


class FakeRunner:
    def __init__(self, results: list[CommandResult]) -> None:
        self.results = iter(results)

    def run(self, argv: list[str], timeout: float) -> CommandResult:
        return next(self.results)

    def start(self, argv: list[str]) -> FakeProcess:
        return FakeProcess()


class DrillTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.snapshot, self.token, self.binary = root / "snapshot", root / "token", root / "k3s"
        self.snapshot.write_text("snapshot", encoding="utf-8")
        self.token.write_text("secret-token", encoding="utf-8")
        self.token.chmod(0o600)
        self.binary.write_text("binary", encoding="utf-8")
        self.preflight = PreflightResult((), self.binary, "k3s version v1", self.snapshot.stat().st_size)
        self.work = root / "work"
        self.pvc_archive = root / "pvc.tar"
        self.expected_digest = hashlib.sha256(b"payload").hexdigest()
        with tarfile.open(self.pvc_archive, "w") as archive:
            payload = root / "payload.txt"
            payload.write_bytes(b"payload")
            archive.add(payload, arcname="payload.txt")
        self.pvc_manifest = root / "pvc.json"
        self.pvc_manifest.write_text(json.dumps({"local_path": f"/var/lib/rancher/k3s/storage/test-pvc-{id(self):x}", "relative_path": "payload.txt", "sha256": self.expected_digest, "namespace": "drill", "claim": "data", "pv": "test-pv"}), encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_verify(self, runner: FakeRunner, **kwargs: object):
        options: dict[str, object] = {
            "restore_timeout": 10,
            "startup_timeout": 1,
            "runner": runner,
            "sleep": lambda _: None,
            "topology": "single",
            "pvc_checksum_file": self.pvc_manifest,
            "pvc_volume_archive": self.pvc_archive,
        }
        options.update(kwargs)
        return verify(preflight=self.preflight, snapshot=self.snapshot, token_file=self.token, work_dir=self.work, marker_name="marker", marker_namespace="default", **options)

    def test_pass_requires_restore_api_and_marker(self) -> None:
        report = self.run_verify(FakeRunner([CommandResult(0, ""), CommandResult(0, "ok"), CommandResult(0, "node/test"), CommandResult(0, "configmap/marker")]), keep_artifacts=False)
        self.assertEqual(report.status, "PASS")
        self.assertTrue(report.api_ready)
        self.assertTrue(report.marker_found)

    def test_wrong_token_or_corrupt_snapshot_fails_at_restore(self) -> None:
        for label in ("wrong token", "corrupt snapshot"):
            with self.subTest(label=label):
                report = self.run_verify(FakeRunner([CommandResult(1, f"restore rejected: {label}")]), keep_artifacts=False)
                self.assertEqual((report.status, report.stage, report.error_code), ("FAIL", "restore", "RESTORE_FAILED"))

    def test_prepares_data_dir_token_required_by_cluster_reset(self) -> None:
        report = self.run_verify(FakeRunner([CommandResult(1, "restore rejected")]), keep_artifacts=True)
        self.assertEqual(report.error_code, "RESTORE_FAILED")
        run_dirs = list(self.work.glob("drill-*"))
        self.assertEqual(len(run_dirs), 1)
        data_token = run_dirs[0] / "data" / "server" / "token"
        self.assertTrue(data_token.is_file())
        self.assertEqual(data_token.read_text(encoding="utf-8"), "secret-token")

    def test_api_not_ready_fails_after_start(self) -> None:
        report = self.run_verify(FakeRunner([CommandResult(0, "")]), startup_timeout=0, keep_artifacts=False)
        self.assertEqual((report.stage, report.error_code), ("check_api", "API_TIMEOUT"))

    def test_missing_marker_fails_after_healthy_api(self) -> None:
        report = self.run_verify(FakeRunner([CommandResult(0, ""), CommandResult(0, "ok"), CommandResult(0, "node/test"), CommandResult(1, "not found")]), keep_artifacts=False)
        self.assertEqual((report.stage, report.error_code), ("check_marker", "MARKER_MISSING"))

    def test_blocked_preflight_never_runs_restore(self) -> None:
        blocked = PreflightResult((), None, None, None)
        report = verify(preflight=blocked, snapshot=self.snapshot, token_file=self.token, work_dir=self.work, marker_name="marker", marker_namespace="default", topology="single", pvc_checksum_file=self.pvc_manifest, pvc_volume_archive=self.pvc_archive, restore_timeout=10, startup_timeout=10, keep_artifacts=False)
        self.assertEqual((report.stage, report.error_code), ("preflight", "PREFLIGHT_BLOCKED"))


if __name__ == "__main__":
    unittest.main()
