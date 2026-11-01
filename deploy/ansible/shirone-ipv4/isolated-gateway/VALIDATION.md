# Independent IPv4 Higress Gateway

On 2026-11-01 a temporary independent gateway was created for testing only; all
its resources and temporary media were subsequently removed. It used a separate
ClusterIP Service and a cloned gateway Deployment with `hostNetwork: false` and
its own selector. The existing shared Gateway, its Service and public routes
were not changed. The default admission preview of `isolated-gateway.yml`
completed with zero changes. The rollback playbook was exercised on an owned
EnvoyFilter: the correct UID was deleted, and an incorrect UID was refused.
After that cleanup, the reviewed create-only playbook created the independent
resources again for subsequent tunnel integration; no public traffic has been
switched. The current nine UID receipts are stored outside Git at
`/home/hyperbola/Work/ops/reports/shirone-ipv4-uids-20261101.json`.

Observed runtime results, with a synthetic PROXY v2 client IP:

- The independent Gateway and its site/admin HTTPRoutes were Accepted and
  Programmed; the gateway Pod became Ready.
- A complete homepage response was 200 with approximately 240 KiB of content.
  The WAF requires the included isolated 8 MiB response-buffer EnvoyFilter;
  without it the response was truncated even though its status was 200.
- SNI `hyperbola.cc` combined with HTTP Host `grafana.hyperbola.cc` returned
  404. Connections without a PROXY header were reset. A request from the laiku
  node to the ClusterIP timed out under the candidate NetworkPolicy. Repeat
  source enforcement tests after any topology or Calico policy changes.
- The gateway access log and X-Forwarded-For contained the synthetic client IP
  from PROXY v2. The deployed Higress WAF loaded `DetectionOnly`; a synthetic
  XSS request returned 200 and produced OWASP CRS audit warnings for that IP.
  This is detection only, not blocking.
- Administrator login and upload succeeded; the uploaded test image returned
  404 anonymously and 200 with the admin's protected media cookie. Both test
  files were removed. The existing CMS and its data were retained.

The Service is restricted by NetworkPolicy to node `192.168.86.11`; the SSH
tunnel must originate only there. Do not expose the Service with NodePort or
otherwise allow arbitrary clients to send spoofed PROXY headers. Dedicated
NetworkPolicy also permits the isolated gateway to connect to CMS TCP 1337.
The test used one replica and is not high availability. Source-UID, image and
namespace guard values must be taken from current live state and reviewed.

The source restriction trusts the imini node address, not an individual
process. Workloads using `hostNetwork` on imini share that source identity and
may be able to forge PROXY headers; node and host-network workloads must be
trusted. After the managed tunnel was installed, its connection from txy
passed TLS and PROXY probes, and the Gateway log recorded the synthetic source
IP. A site-SNI/Grafana-Host mismatch returned 404, missing PROXY was rejected,
and a direct laiku-node connection to the Service timed out.

`isolated-gateway.yml` defaults to read-only server-side admission checks and
requires all names absent; it does not adopt or overwrite existing resources.
On `shirone_gateway_apply=true`, it reports each object UID even after a
partial create. Preserve those non-secret receipts; if any create or receipt is
uncertain, inspect live state before retrying. `isolated-gateway-rollback.yml`
accepts only a nonempty mapping of the exact owned names and reviewed UIDs,
checks every supplied object before making changes, and uses Kubernetes DELETE
UID preconditions. It also accepts a verified subset after a partial create.
Disconnect the external HAProxy/tunnel before removing a live Gateway.

These results do not prove the full public path. Complete the managed SSH
tunnel, HAProxy SNI routing, external HTTPS and negative tests, service restart
and rollback checks, then add DNS only after the public IPv4 path passes.
