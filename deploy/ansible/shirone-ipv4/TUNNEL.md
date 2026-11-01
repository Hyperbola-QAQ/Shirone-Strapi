# Restricted origin-to-edge SSH tunnel (candidate)

`tunnel.yml` is an Ansible candidate and defaults to read-only preview. Inventory aliases `txy` and `imini` must use the already trusted SSH host configuration. It pins the edge's Ed25519 public host key obtained through the controller's verified SSH connection, creates a dedicated private key only on imini, and configures a dedicated `shirone-tunnel` account on the existing edge SSH listener. The origin runs one user-level connection; there is no automatic failover.

The SSH `Match User` block permits only remote forwarding to edge loopback port 25443, denies sessions and other forwarding, validates `sshd -t`, compares the existing operator's effective `sshd -T` configuration before and after, and checks the dedicated account's effective policy before reload. The private key and pinned host key are kept in owner-only local state; no secrets go into Ansible logs or Git. An interrupted run can leave owned partial state; inspect it before retrying. Candidate rollback requires an explicit switch, stops the one user service, removes only the marked SSH configuration/account, and retains the origin's key and pinned host key.

A TLS SNI allowlist at the public edge does not stop an HTTP `Host` mismatch on an established connection. The original shared Higress HTTPS NodePort 30181 exposes other hosts. Before setting `tunnel_apply=true`, provision and test an independent gateway/listener with only the approved site and admin routes, identify its ClusterIP Service, and verify mismatch requests cannot reach other services. The playbook requires the reviewed Service name, UID, ClusterIP and exact selector, and verifies them against live Kubernetes state; those checks alone do not prove route isolation. It forwards PROXY v2 followed by original TLS to that ClusterIP on port 8443. The fixed service is `higress-system/shirone-ipv4-gateway` with selector `app=shirone-ipv4-higress`. Preserve TLS and WAF semantics and test rollback. No production operation or DNS change is performed by the preview.

```sh
ansible-playbook -i 'txy,imini,' --syntax-check tunnel.yml
ansible-playbook -i 'txy,imini,' tunnel.yml
# Only after independent gateway validation: provide tunnel_apply=true, tunnel_gateway_verified=true,
# tunnel_gateway_uid and tunnel_origin_host
# using reviewed extra-vars.
# Rollback, after traffic has been safely rerouted:
# ansible-playbook -i 'txy,imini,' tunnel-rollback.yml -e tunnel_remove=true
```

The default preview has been run on txy and imini with zero changes. The controller's `txy` SSH host-key check is enabled. A read-only comparison against the real SSH configuration confirmed that the proposed block does not change the existing operator's effective settings. Syntax checks pass, and application without the explicit verified Service parameters fails before changing anything. The post-start probe sends PROXY v2 and verifies HTTPS/SNI against the site and admin hostnames. Full apply/rollback and reconnection tests remain necessary.

On 2026-11-01 the managed tunnel was applied to the dedicated Gateway Service UID `b3713805-dcfc-4bb1-92c9-83e8197c1b21`, ClusterIP `10.102.238.215:8443`. Both site and admin loopback probes passed with certificate-verified TLS and PROXY v2. Restarting the user service recovered the tunnel and the site probe; a synthetic client IP appeared in the gateway access log and X-Forwarded-For. A cross-host request (site SNI with Grafana Host) returned 404 and a connection without PROXY was rejected. Public HAProxy traffic was not switched by this operation.

The Gateway NetworkPolicy trusts source address `192.168.86.11/32` (imini), not a particular process. The node and any `hostNetwork` workloads sharing that source address are trusted and may be able to forge a PROXY header. A non-authorized laiku-node connection to the Service timed out, but this does not prove process-level isolation on imini. Recheck source restrictions after topology changes and do not present this boundary as exclusive to the SSH process.
