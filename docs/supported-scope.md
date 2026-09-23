# Supported scope

The MVP verifies only a local K3s **single-server embedded-etcd** snapshot plus its original server token on a dedicated Linux x86_64 VM.

## Required environment

- A disposable VM, isolated from the source-cluster network and production services.
- Neither `k3s` nor `k3s-agent` service may be active or enabled on that VM; enabling either could create default state on reboot.
- A compatible K3s binary. Record its exact `k3s --version` in each report.
- A dedicated `--work-dir`; never `/` or `/var/lib/rancher/k3s`.
- A regular, readable, nonempty snapshot and token file. Token permissions must be `0600`.
- Enough free disk: three times the snapshot size plus 2 GiB.
- A non-secret ConfigMap marker created before the snapshot, with explicit name and namespace.

## Result semantics

`PASS` means the restored test VM reached the Kubernetes API and can read the named marker. It does **not** prove that workloads, images, networking, persistent volumes, external databases, or a production cluster are recoverable.

`FAIL` identifies the stage that failed; it does not prove the snapshot is corrupt. Compatibility flags and test-VM conditions may be missing.

## Explicit exclusions

SQLite and external datastore backups, S3 snapshots, HA/multi-server recovery, production use, VM provisioning, scheduled drills, dashboards, users, applications, databases, and PVC recovery are outside this MVP.
