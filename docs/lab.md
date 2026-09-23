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
