from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .drill import verify
from .preflight import inspect
from .report import DrillReport


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--snapshot", required=True, type=Path, help="local embedded-etcd snapshot")
    parser.add_argument("--token-file", required=True, type=Path, help="original K3s server token file")
    parser.add_argument("--work-dir", required=True, type=Path, help="dedicated parent directory for test-VM state")
    parser.add_argument("--k3s-binary", default="k3s", help="path or command name of K3s binary")
    parser.add_argument("--topology", required=True, choices=("single", "ha"), help="source embedded-etcd topology")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="k3s-restore-drill", description="Test a K3s embedded-etcd snapshot on a disposable Linux VM.")
    commands = parser.add_subparsers(dest="command", required=True)
    inspect_parser = commands.add_parser("inspect", help="check prerequisites without restoring")
    _common(inspect_parser)
    verify_parser = commands.add_parser("verify", help="restore and verify a marker ConfigMap")
    _common(verify_parser)
    verify_parser.add_argument("--expected-marker", required=True, help="ConfigMap name created before the snapshot")
    verify_parser.add_argument("--marker-namespace", default="default", help="ConfigMap namespace (default: default)")
    verify_parser.add_argument("--expected-pvc-checksum-file", required=True, type=Path, help="JSON manifest describing the archived local-path PVC file and checksum")
    verify_parser.add_argument("--pvc-volume-archive", required=True, type=Path, help="tar archive of the local-path volume captured with the snapshot")
    verify_parser.add_argument("--report", required=True, type=Path, help="destination for sanitized JSON report")
    verify_parser.add_argument("--disposable-vm", action="store_true", help="required acknowledgement that this is a dedicated test VM")
    verify_parser.add_argument("--restore-timeout", type=float, default=900, help="restore timeout in seconds (default: 900)")
    verify_parser.add_argument("--startup-timeout", type=float, default=300, help="API readiness timeout in seconds (default: 300)")
    verify_parser.add_argument("--keep-artifacts", action="store_true", help="retain private test state and logs for debugging")
    return parser


def _print_findings(result: object) -> None:
    print(result.readiness)
    for finding in result.findings:
        print(f"{finding.readiness} {finding.code}: {finding.message}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    preflight = inspect(args.snapshot, args.token_file, args.work_dir, args.k3s_binary)
    if args.command == "inspect":
        _print_findings(preflight)
        return 0 if preflight.readiness == "READY_TO_TRY" else 2
    if not args.disposable_vm:
        parser = build_parser()
        parser.error("verify requires --disposable-vm; it changes state on the test VM")
    report = verify(
        preflight=preflight,
        snapshot=args.snapshot,
        token_file=args.token_file,
        work_dir=args.work_dir,
        marker_name=args.expected_marker,
        marker_namespace=args.marker_namespace,
        topology=args.topology,
        pvc_checksum_file=args.expected_pvc_checksum_file,
        pvc_volume_archive=args.pvc_volume_archive,
        restore_timeout=args.restore_timeout,
        startup_timeout=args.startup_timeout,
        keep_artifacts=args.keep_artifacts,
    )
    report.write(args.report)
    print(json.dumps(report.as_dict(), sort_keys=True))
    return 0 if report.status == "PASS" else 1 if report.stage != "preflight" else 2


if __name__ == "__main__":
    raise SystemExit(main())
