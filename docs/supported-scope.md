# Supported scope

The MVP verifies a local K3s embedded-etcd snapshot from either a **single-server** source or a **three-server HA** source on a separate, disposable Linux x86_64 VM.

## Required inputs

- Original server token and a K3s binary compatible with the snapshot.
- A non-secret ConfigMap marker created before the snapshot.
- A `local-path` PVC archive captured with the snapshot.
- A checksum manifest with this shape:

  ```json
  {
    "local_path": "/var/lib/rancher/k3s/storage/pvc-..._drill_drill-data",
    "relative_path": "payload.txt",
    "sha256": "lowercase-sha256",
    "namespace": "drill",
    "claim": "drill-data",
    "pv": "pvc-...",
    "source_node": "source-server-hostname"
  }
  ```

`local_path` is created only on the disposable target and removed by default after the drill. The archive must contain only relative regular files and directories.

## Result semantics

`PASS` means the recovered API and node are ready, the pre-snapshot marker is readable, and the restored local-path payload produced the expected SHA-256 checksum. The report records `topology` and `pvc_checksum_match`.

For HA snapshots, K3s `--cluster-reset` restores one etcd member on the target. It does not recreate the original three-server topology. `source_node` is required for HA so the recovered member keeps the PVC's immutable node affinity while its status is rebound to the target IP.

## Explicit exclusions

SQLite and external datastore backups, S3 snapshots, production restores, VM provisioning, scheduled drills, application-health checks, databases, and non-local-path storage remain outside the MVP.
