from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import socket
import tarfile
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


@dataclass(frozen=True)
class PvcCheck:
    local_path: Path
    relative_path: PurePosixPath
    sha256: str
    namespace: str
    claim: str
    pv: str
    source_node: str | None


def _load_pvc_check(manifest_file: Path) -> PvcCheck:
    try:
        data = json.loads(manifest_file.read_text(encoding="utf-8"))
        raw_local_path = data["local_path"]
        local_path = Path(raw_local_path)
        relative_path = PurePosixPath(data["relative_path"])
        digest = data["sha256"]
        namespace, claim, pv = data["namespace"], data["claim"], data["pv"]
        source_node = data.get("source_node")
    except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError) as exc:
        raise DrillFailure("prepare", "PVC_MANIFEST_INVALID", "provide a JSON manifest with local_path, relative_path, sha256, namespace, claim, and pv", str(exc)) from exc
    if not isinstance(raw_local_path, str) or not PurePosixPath(raw_local_path).is_absolute() or not raw_local_path.startswith("/var/lib/rancher/k3s/storage/"):
        raise DrillFailure("prepare", "PVC_PATH_UNSAFE", "local_path must be beneath /var/lib/rancher/k3s/storage", str(local_path))
    if relative_path.is_absolute() or ".." in relative_path.parts or str(relative_path) in ("", "."):
        raise DrillFailure("prepare", "PVC_RELATIVE_PATH_INVALID", "relative_path must name a regular file inside the archived volume")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise DrillFailure("prepare", "PVC_CHECKSUM_INVALID", "sha256 must be a lowercase SHA-256 digest")
    if not all(isinstance(item, str) and item for item in (namespace, claim, pv)):
        raise DrillFailure("prepare", "PVC_MANIFEST_INVALID", "namespace, claim, and pv must be nonempty strings")
    if source_node is not None and (not isinstance(source_node, str) or not source_node):
        raise DrillFailure("prepare", "PVC_MANIFEST_INVALID", "source_node must be a nonempty string when provided")
    return PvcCheck(local_path, relative_path, digest, namespace, claim, pv, source_node)


def _extract_pvc_archive(archive: Path, destination: Path) -> None:
    try:
        with tarfile.open(archive, "r:*") as contents:
            members = contents.getmembers()
            for member in members:
                member_path = PurePosixPath(member.name)
                if member_path.is_absolute() or ".." in member_path.parts or not (member.isdir() or member.isreg()):
                    raise DrillFailure("prepare", "PVC_ARCHIVE_UNSAFE", "PVC archive must contain only regular files and directories with relative paths")
            contents.extractall(destination, members)
    except DrillFailure:
        raise
    except (OSError, tarfile.TarError) as exc:
        raise DrillFailure("prepare", "PVC_ARCHIVE_INVALID", "provide a readable tar archive containing the local-path volume", str(exc)) from exc


def _prepare_pvc_volume(check: PvcCheck, archive: Path, run_dir: Path, destination: Path | None = None) -> Path:
    if not archive.is_file() or archive.stat().st_size == 0:
        raise DrillFailure("prepare", "PVC_ARCHIVE_INVALID", "PVC volume archive must be a readable nonempty regular file")
    destination = destination or (check.local_path if os.name == "posix" else run_dir / "pvc-test-volume")
    if destination.exists():
        raise DrillFailure("prepare", "PVC_PATH_EXISTS", "the disposable VM already has the local-path volume directory; use a clean target VM", str(destination))
    stage = run_dir / "pvc-stage"
    stage.mkdir(mode=0o700)
    _extract_pvc_archive(archive, stage)
    destination.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    shutil.copytree(stage, destination)
    return destination


def _target_ipv4() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.connect(("192.0.2.1", 80))
        return probe.getsockname()[0]


def _wait_for_api(
    runner: CommandRunner,
    binary: Path,
    kubeconfig: Path,
    process: object,
    timeout: float,
    sleep: Callable[[float], None],
) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        probe = runner.run(_argv(binary, "kubectl", f"--kubeconfig={kubeconfig}", "get", "--raw=/readyz"), 10)
        if probe.returncode == 0:
            return True
        if process.poll() is not None:
            raise DrillFailure("start", "K3S_EXITED", "inspect retained test-VM logs; K3s exited before its API became ready")
        sleep(2)
    return False

def _check_pvc(check: PvcCheck, volume_path: Path) -> bool:
    payload = volume_path / check.relative_path
    if not payload.is_file():
        return False
    digest = hashlib.sha256()
    with payload.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest() == check.sha256


def verify(
    *,
    preflight: PreflightResult,
    snapshot: Path,
    token_file: Path,
    work_dir: Path,
    marker_name: str,
    marker_namespace: str,
    topology: str,
    pvc_checksum_file: Path,
    pvc_volume_archive: Path,
    restore_timeout: float,
    startup_timeout: float,
    keep_artifacts: bool,
    runner: CommandRunner | None = None,
    sleep: Callable[[float], None] = time.sleep,
    _volume_destination: Path | None = None,
) -> DrillReport:
    started = datetime.now(UTC)
    report = DrillReport(status="FAIL", stage="preflight", started_at=started.isoformat().replace("+00:00", "Z"), topology=topology)
    report.k3s_version = preflight.k3s_version
    report.snapshot_size_bytes = preflight.snapshot_size_bytes
    report.stages["preflight"] = preflight.readiness
    if preflight.readiness is not Readiness.READY_TO_TRY or preflight.k3s_path is None:
        report.finish(status="FAIL", stage="preflight", started=started, error_code="PREFLIGHT_BLOCKED", hint="resolve every BLOCKED or UNKNOWN preflight finding before verify")
        return report

    runner = runner or CommandRunner((token_file.read_text(encoding="utf-8", errors="replace").strip(),))
    process = None
    run_dir: Path | None = None
    check: PvcCheck | None = None
    volume_path: Path | None = None
    pvc_prepared = False
    try:
        check = _load_pvc_check(pvc_checksum_file)
        if topology == "ha" and not check.source_node:
            raise DrillFailure("prepare", "PVC_SOURCE_NODE_REQUIRED", "HA recovery needs source_node from the pre-snapshot local PersistentVolume affinity")
        node_name = check.source_node if topology == "ha" else socket.gethostname()
        work_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        work_dir.chmod(0o700)
        run_dir = Path(tempfile.mkdtemp(prefix="drill-", dir=work_dir))
        run_dir.chmod(0o700)
        volume_path = _prepare_pvc_volume(check, pvc_volume_archive, run_dir, _volume_destination)
        pvc_prepared = True
        local_snapshot, local_token = run_dir / "snapshot", run_dir / "server-token"
        _copy_private(snapshot, local_snapshot)
        _copy_private(token_file, local_token)
        data_dir, kubeconfig = run_dir / "data", run_dir / "kubeconfig.yaml"
        data_token = data_dir / "server" / "token"
        data_token.parent.mkdir(parents=True, mode=0o700)
        data_token.parent.chmod(0o700)
        _copy_private(local_token, data_token)
        report.stages["prepare"] = "PASS"

        server_args = (f"--token-file={local_token}", f"--data-dir={data_dir}", "--etcd-s3=false", f"--write-kubeconfig={kubeconfig}", "--write-kubeconfig-mode=600", f"--node-name={node_name}")
        restore = runner.run(_argv(preflight.k3s_path, "server", "--cluster-reset", f"--cluster-reset-restore-path={local_snapshot}", *server_args), restore_timeout)
        if restore.timed_out:
            raise DrillFailure("restore", "RESTORE_TIMEOUT", "increase --restore-timeout only after checking the test-VM logs", restore.output)
        if restore.returncode:
            raise DrillFailure("restore", "RESTORE_FAILED", "check K3s version, token, snapshot, and test-VM configuration", restore.output)
        report.stages["restore"] = "PASS"

        process = runner.start(_argv(preflight.k3s_path, "server", *server_args))
        report.stages["start"] = "STARTED"
        if not _wait_for_api(runner, preflight.k3s_path, kubeconfig, process, startup_timeout, sleep):
            raise DrillFailure("check_api", "API_TIMEOUT", "inspect retained test-VM logs and restore compatibility settings")
        if topology == "ha":
            node_status = json.dumps({"status": {"addresses": [{"type": "InternalIP", "address": _target_ipv4()}, {"type": "Hostname", "address": node_name}]}}, separators=(",", ":"))
            patch = runner.run(_argv(preflight.k3s_path, "kubectl", f"--kubeconfig={kubeconfig}", "patch", "node", node_name, "--subresource=status", "--type=merge", f"--patch={node_status}"), 30)
            if patch.returncode:
                raise DrillFailure("start", "HA_NODE_STATUS_PATCH_FAILED", "the recovered source node must report the disposable target IP before networking starts", patch.output)
            stop_process(process)
            process = runner.start(_argv(preflight.k3s_path, "server", *server_args))
            if not _wait_for_api(runner, preflight.k3s_path, kubeconfig, process, startup_timeout, sleep):
                raise DrillFailure("check_api", "API_TIMEOUT", "the recovered HA node did not become ready after rebinding its target IP")
        node_ready = runner.run(_argv(preflight.k3s_path, "kubectl", f"--kubeconfig={kubeconfig}", "wait", "--for=condition=Ready", f"node/{node_name}", f"--timeout={int(startup_timeout)}s"), startup_timeout + 5)
        if node_ready.returncode:
            raise DrillFailure("start", "NODE_NOT_READY", "the recovered node did not become ready after starting K3s", node_ready.output)
        report.api_ready = True
        report.stages["check_api"] = "PASS"

        marker = runner.run(_argv(preflight.k3s_path, "kubectl", f"--kubeconfig={kubeconfig}", "get", "configmap", marker_name, "--namespace", marker_namespace, "--output=name"), 15)
        if marker.returncode:
            raise DrillFailure("check_marker", "MARKER_MISSING", "ensure the named ConfigMap existed before the snapshot was taken", marker.output)
        report.marker_found = True
        report.stages["check_marker"] = "PASS"
        report.pvc_checksum_match = _check_pvc(check, volume_path)
        if not report.pvc_checksum_match:
            raise DrillFailure("check_pvc", "PVC_CHECKSUM_MISMATCH", "restore the local-path volume archive that matches the snapshot, then retry")
        report.stages["check_pvc"] = "PASS"
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
        if pvc_prepared and volume_path is not None and volume_path.exists():
            shutil.rmtree(volume_path)
        if run_dir and run_dir.exists() and not keep_artifacts:
            shutil.rmtree(run_dir)
    return report
