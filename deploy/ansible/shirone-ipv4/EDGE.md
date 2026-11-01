# HAProxy edge cutover

`edge.yml` is read-only unless `edge_apply=true` is passed. It accepts inventory aliases `imini` and `txy` with verified SSH host keys. The candidate maintains the existing HTTP and non-Shirone HTTPS backends, including their PROXY v2 setting; the four selected SNI names receive TCP/TLS passthrough to the dedicated SSH tunnel, also with PROXY v2.

Before applying, independently establish that the dedicated gateway only accepts traffic from the trusted tunnel origin, consumes PROXY v2, isolates the HTTP Host from other virtual hosts, enforces the intended Higress WAF, and is backed by the reviewed Service and functioning reverse tunnel. The playbook verifies the Service UID, ClusterIP, selector and port and sends PROXY v2 followed by certificate-verified HTTPS through the tunnel for both site and admin. Service identity and successful probes are not proof of isolation or trusted source restrictions.

The original root-owned edge configuration is recorded in `haproxy-edge-before.cfg`. Its SHA-256 and the exact current file checksum must match the reviewed baseline or the exact candidate; a concurrent change is refused. Candidate configuration is validated by the installed HAProxy binary before any write. After reload, site and admin must return HTTP 200 and an unrelated site's status must match its pre-change result; a failed verification restores the original configuration only if the current file still matches the candidate. A concurrent change causes a hold for operator reconciliation instead of an overwrite. Automatic restoration cannot cover controller or network failure during a write or reload; inspect actual service state after interrupted runs.

Run from the Ansible project directory containing these files, with verified `txy`/`imini` inventory aliases:

```sh
ansible-playbook -i 'txy,imini,' --syntax-check edge.yml
ansible-playbook -i 'txy,imini,' edge.yml
# Only after the independent safety and recovery checks:
# ansible-playbook -i 'txy,imini,' edge.yml -e edge_apply=true -e edge_gateway_uid=REVIEWED_UID -e edge_gateway_ip=REVIEWED_CLUSTER_IP
# Explicit rollback, after checking current traffic and identity:
# ansible-playbook -i 'txy,imini,' edge-rollback.yml -e edge_rollback=true
```

The 2026-11-01 preview passed with no changes; the guarded apply then succeeded. Public IPv4 checks returned 200 for the three site hosts and the Strapi admin (using the reviewed A target), and the draft API returned 404. The unrelated twikoo response was unchanged across the reload. Cloudflare's new admin A record receipt is recorded in `/home/hyperbola/Work/ops/reports/shirone-ipv4-dns-20261101.json`; preserve its existing AAAA. The prior blog backend was already unreachable, so restoring the original HAProxy configuration alone restores its old 502 behavior. User sessions and long-lived connections can be interrupted by a reload or tunnel failure.
