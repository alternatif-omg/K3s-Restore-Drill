# Manual restore lab

Run this procedure with a training cluster only. The tool must not be the first attempt to restore an environment.

## 1. Prepare a training source

1. Create a separate K3s server using embedded etcd (`--cluster-init`), not SQLite.
2. Create a non-secret marker before the snapshot:

   ```bash
   kubectl -n drill create configmap k3s-drill-marker --from-literal=nonce="training-only-random-value"
   ```

3. Save a snapshot and securely copy both the resulting snapshot and the **original** server token to the dedicated test VM. Keep the token `0600`.

   ```bash
   sudo k3s etcd-snapshot save --name drill
   sudo install -m 600 /var/lib/rancher/k3s/server/token /secure-backups/server-token
   ```

Do not put either input in Git, issue trackers, cloud paste services, or example reports.

## 2. Manually restore on the separate test VM

Follow the [K3s snapshot restore procedure](https://docs.k3s.io/cli/etcd-snapshot#restoring-snapshots) for the exact K3s version. Use a dedicated data directory, `--token-file`, and `--etcd-s3=false` for a local snapshot. Restart K3s normally after `--cluster-reset`; reset completion alone is not success.

## 3. Validate and record

Wait for the API and read the original marker:

```bash
kubectl --kubeconfig /path/to/test-kubeconfig -n drill get configmap k3s-drill-marker
```

Record the K3s version, successful commands, required server flags, VM hostname/network conditions, and duration here before using `verify`. If this manual drill fails, fix the lab first; do not automate an unproven restore path.

## 4. HA plus local-path proof (2026-09-26)

- Source: `neb` plus two AWS K3s servers, all `v1.36.4+k3s1`; WireGuard mesh connectivity passed between every pair.
- Source PVC: `drill/drill-data`, PV `pvc-5ec4015a-a236-4b17-9c3a-6161a1b00538`, local path `/var/lib/rancher/k3s/storage/pvc-5ec4015a-a236-4b17-9c3a-6161a1b00538_drill_drill-data`.
- Snapshot plus original server token alone restored Kubernetes metadata but not the local-path bytes. A tar archive of that local directory was required.
- Target: separate disposable `ben`; `k3s server --cluster-reset` restored the HA snapshot to one etcd member.
- Target node metadata initially retained the source internal IP. Rebinding the restored `neb` Node status to the target internal IP was required before flannel could start.
- Result: API ready, `drill/k3s-drill-marker=ha-pvc-2026-09-26`, and the restored PVC file SHA-256 matched `27867d916a9e9ea435b6a5168d8c53d359c6587856198c9862b8fa0748ed49ca`.

- CLI acceptance run: `topology=ha` passed in 52.926 seconds; the report recorded API ready, recovered node Ready, marker found, and `pvc_checksum_match=true`.
