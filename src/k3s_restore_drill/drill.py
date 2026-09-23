from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import shutil
import tempfile
import time
from typing import Callable

from .preflight import PreflightResult, Readiness
from .report import DrillReport, redact
from .runner import CommandRunner, stop_process


class DrillFailure(RuntimeError):
    def __init__(self, stage: str, code: str, hint: str, detail: str = "") -> None:
        super().__init__(detail)
        self.stage, self.code, self.hint = stage, code, hint


def _copy_private(source: Path, destination: Path) -> None:
    shutil.copy2(source, destination)
    destination.chmod(0o600)


def _argv(binary: Path, *args: str) -> list[str]:
    return [str(binary), *args]


def verify(
    *,
    preflight: PreflightResult,
    snapshot: Path,
    token_file: Path,
    work_dir: Path,
    marker_name: str,
    marker_namespace: str,
    restore_timeout: float,
    startup_timeout: float,
    keep_artifacts: bool,
    runner: CommandRunner | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> DrillReport:
    started = datetime.now(UTC)
    report = DrillReport(status="FAIL", stage="preflight", started_at=started.isoformat().replace("+00:00", "Z"))
    report.k3s_version = preflight.k3s_version
    report.snapshot_size_bytes = preflight.snapshot_size_bytes
    report.stages["preflight"] = preflight.readiness
    if preflight.readiness is not Readiness.READY_TO_TRY or preflight.k3s_path is None:
        report.finish(status="FAIL", stage="preflight", started=started, error_code="PREFLIGHT_BLOCKED", hint="resolve every BLOCKED or UNKNOWN preflight finding before verify")
        return report

    runner = runner or CommandRunner((token_file.read_text(encoding="utf-8", errors="replace").strip(),))
    process = None
    run_dir: Path | None = None
    try:
        work_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        work_dir.chmod(0o700)
        run_dir = Path(tempfile.mkdtemp(prefix="drill-", dir=work_dir))
        run_dir.chmod(0o700)
        local_snapshot, local_token = run_dir / "snapshot", run_dir / "server-token"
        _copy_private(snapshot, local_snapshot)
        _copy_private(token_file, local_token)
        data_dir, kubeconfig = run_dir / "data", run_dir / "kubeconfig.yaml"
        data_token = data_dir / "server" / "token"
        data_token.parent.mkdir(parents=True, mode=0o700)
        data_token.parent.chmod(0o700)
        _copy_private(local_token, data_token)
        report.stages["prepare"] = "PASS"

        restore = runner.run(_argv(preflight.k3s_path, "server", "--cluster-reset", f"--cluster-reset-restore-path={local_snapshot}", f"--token-file={local_token}", f"--data-dir={data_dir}", "--etcd-s3=false", f"--write-kubeconfig={kubeconfig}", "--write-kubeconfig-mode=600"), restore_timeout)
        if restore.timed_out:
            raise DrillFailure("restore", "RESTORE_TIMEOUT", "increase --restore-timeout only after checking the test-VM logs", restore.output)
        if restore.returncode:
            raise DrillFailure("restore", "RESTORE_FAILED", "check K3s version, token, snapshot, and test-VM configuration", restore.output)
        report.stages["restore"] = "PASS"

        process = runner.start(_argv(preflight.k3s_path, "server", f"--token-file={local_token}", f"--data-dir={data_dir}", "--etcd-s3=false", f"--write-kubeconfig={kubeconfig}", "--write-kubeconfig-mode=600"))
        report.stages["start"] = "STARTED"
        deadline = time.monotonic() + startup_timeout
        ready = False
        while time.monotonic() < deadline:
            probe = runner.run(_argv(preflight.k3s_path, "kubectl", f"--kubeconfig={kubeconfig}", "get", "--raw=/readyz"), 10)
            if probe.returncode == 0:
                ready = True
                break
            if process.poll() is not None:
                raise DrillFailure("start", "K3S_EXITED", "inspect retained test-VM logs; K3s exited before its API became ready")
            sleep(2)
        if not ready:
            raise DrillFailure("check_api", "API_TIMEOUT", "inspect retained test-VM logs and restore compatibility settings")
        report.api_ready = True
        report.stages["check_api"] = "PASS"

        marker = runner.run(_argv(preflight.k3s_path, "kubectl", f"--kubeconfig={kubeconfig}", "get", "configmap", marker_name, "--namespace", marker_namespace, "--output=name"), 15)
        if marker.returncode:
            raise DrillFailure("check_marker", "MARKER_MISSING", "ensure the named ConfigMap existed before the snapshot was taken", marker.output)
        report.marker_found = True
        report.stages["check_marker"] = "PASS"
        report.finish(status="PASS", stage="complete", started=started)
    except DrillFailure as exc:
        report.stages.setdefault(exc.stage, "FAIL")
        report.finish(status="FAIL", stage=exc.stage, started=started, error_code=exc.code, hint=exc.hint, error=redact(str(exc)))
    except OSError as exc:
        report.stages.setdefault("prepare", "FAIL")
        report.finish(status="FAIL", stage="prepare", started=started, error_code="WORKSPACE_ERROR", hint="check dedicated work-directory permissions", error=redact(str(exc)))
    finally:
        if process is not None:
            stop_process(process)
        if run_dir and run_dir.exists() and not keep_artifacts:
            shutil.rmtree(run_dir)
    return report
