from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import os
from pathlib import Path
import platform
import shutil
import stat
from typing import Callable

from .runner import CommandRunner


class Readiness(StrEnum):
    READY_TO_TRY = "READY_TO_TRY"
    BLOCKED = "BLOCKED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class Finding:
    code: str
    readiness: Readiness
    message: str


@dataclass(frozen=True)
class PreflightResult:
    findings: tuple[Finding, ...]
    k3s_path: Path | None
    k3s_version: str | None
    snapshot_size_bytes: int | None

    @property
    def readiness(self) -> Readiness:
        states = {finding.readiness for finding in self.findings}
        if Readiness.BLOCKED in states:
            return Readiness.BLOCKED
        if Readiness.UNKNOWN in states:
            return Readiness.UNKNOWN
        return Readiness.READY_TO_TRY


def _readable_nonempty(path: Path, kind: str) -> Finding | None:
    if not path.is_file():
        return Finding(f"{kind.upper()}_NOT_FILE", Readiness.BLOCKED, f"{kind} must be a regular file: {path}")
    if not os.access(path, os.R_OK):
        return Finding(f"{kind.upper()}_UNREADABLE", Readiness.BLOCKED, f"{kind} is not readable: {path}")
    if path.stat().st_size == 0:
        return Finding(f"{kind.upper()}_EMPTY", Readiness.BLOCKED, f"{kind} is empty: {path}")
    return None


def _service_active(runner: CommandRunner) -> Finding | None:
    for service in ("k3s", "k3s-agent"):
        active = runner.run(["systemctl", "is-active", "--quiet", service], timeout=5)
        if active.returncode == 0:
            return Finding("K3S_SERVICE_ACTIVE", Readiness.BLOCKED, f"{service} is active; use a dedicated VM with no active K3s service")
        if active.returncode == 127:
            return Finding("SERVICE_STATE_UNKNOWN", Readiness.UNKNOWN, "cannot determine K3s service state because systemctl is unavailable")
        enabled = runner.run(["systemctl", "is-enabled", "--quiet", service], timeout=5)
        if enabled.returncode == 0:
            return Finding("K3S_SERVICE_ENABLED", Readiness.BLOCKED, f"{service} is enabled; disable it so a reboot cannot create default K3s state")
    return None


DEFAULT_K3S_PATHS = (
    (Path("/var/lib/rancher/k3s"), "K3S_STATE_EXISTS", "default K3s state exists at"),
    (Path("/etc/rancher/k3s/config.yaml"), "K3S_CONFIG_EXISTS", "existing K3s configuration exists at"),
    (Path("/etc/rancher/k3s/config.yaml.d"), "K3S_CONFIG_EXISTS", "existing K3s configuration directory exists at"),
)


def _existing_k3s_state() -> list[Finding]:
    findings: list[Finding] = []
    for path, code, description in DEFAULT_K3S_PATHS:
        if path.exists():
            findings.append(Finding(code, Readiness.BLOCKED, f"{description} {path}; use a clean disposable VM"))
    return findings


def inspect(
    snapshot: Path,
    token_file: Path,
    work_dir: Path,
    k3s_binary: str,
    runner: CommandRunner | None = None,
    system_name: str | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> PreflightResult:
    runner = runner or CommandRunner()
    findings: list[Finding] = []
    system_name = system_name or platform.system()
    if system_name != "Linux":
        findings.append(Finding("LINUX_REQUIRED", Readiness.BLOCKED, "verify must run on a Linux disposable VM"))

    for path, kind in ((snapshot, "snapshot"), (token_file, "token file")):
        finding = _readable_nonempty(path, kind)
        if finding:
            findings.append(finding)

    if token_file.is_file() and os.name == "posix":
        try:
            mode = stat.S_IMODE(token_file.stat().st_mode)
            if mode & 0o077:
                findings.append(Finding("TOKEN_PERMISSIONS", Readiness.BLOCKED, "token file must not be readable by group or others (use chmod 600)"))
        except OSError:
            findings.append(Finding("TOKEN_MODE_UNKNOWN", Readiness.UNKNOWN, "cannot read token-file permissions"))

    forbidden = {Path("/").resolve(), Path("/var/lib/rancher/k3s").resolve()}
    try:
        resolved_work_dir = work_dir.resolve()
        if resolved_work_dir in forbidden:
            findings.append(Finding("UNSAFE_WORK_DIR", Readiness.BLOCKED, "work directory must be dedicated and must not be the default K3s data directory"))
        parent = resolved_work_dir if resolved_work_dir.exists() else resolved_work_dir.parent
        usage = shutil.disk_usage(parent)
        required = (snapshot.stat().st_size if snapshot.is_file() else 0) * 3 + 2 * 1024**3
        if usage.free < required:
            findings.append(Finding("INSUFFICIENT_DISK", Readiness.BLOCKED, f"need at least {required} free bytes in work-directory filesystem"))
    except OSError as exc:
        findings.append(Finding("WORK_DIR_UNAVAILABLE", Readiness.BLOCKED, f"cannot use work directory: {exc}"))

    requested_path = Path(k3s_binary)
    resolved_binary = requested_path if requested_path.is_file() else which(k3s_binary)
    binary_path = Path(resolved_binary) if resolved_binary else None
    version: str | None = None
    if binary_path is None or not binary_path.is_file():
        findings.append(Finding("K3S_NOT_FOUND", Readiness.BLOCKED, "K3s binary was not found; pass --k3s-binary or install K3s on the test VM"))
    else:
        result = runner.run([binary_path, "--version"], timeout=10)
        if result.returncode:
            findings.append(Finding("K3S_VERSION_FAILED", Readiness.BLOCKED, "K3s binary did not return a version"))
        else:
            version = result.output.strip().splitlines()[0] if result.output.strip() else "unknown"

    if system_name == "Linux":
        active = _service_active(runner)
        if active:
            findings.append(active)
    if system_name == "Linux":
        findings.extend(_existing_k3s_state())
    if not findings:
        findings.append(Finding("PREFLIGHT_OK", Readiness.READY_TO_TRY, "inputs and local VM prerequisites are ready to try; this does not validate the token or snapshot"))
    elif not any(item.readiness is Readiness.BLOCKED for item in findings):
        findings.append(Finding("PREFLIGHT_INCOMPLETE", Readiness.UNKNOWN, "resolve unknown prerequisites before starting a destructive test-VM restore"))

    return PreflightResult(tuple(findings), binary_path, version, snapshot.stat().st_size if snapshot.is_file() else None)
