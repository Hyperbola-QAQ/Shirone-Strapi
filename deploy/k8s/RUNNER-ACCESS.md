# Candidate access for the existing release runner

`runner-rbac-candidate.yaml` is an unapplied, namespace-scoped ServiceAccount,
Role, and RoleBinding for updating the two Shirone Deployments. It permits
`get`, `list`, `watch`, and `patch` only for `shirone-cms` and `shirone-ssr`.
The Role does not grant access to Secrets, Pods, other Deployments, or other
namespaces. Service account token automount is disabled; a separate controlled
host credential and renewal method are required before a runner can use it.

Kubernetes RBAC cannot restrict a `patch` permission to an image field. An
identity with this Role can change other fields of those two Deployments, and
could gain access to data available to those workloads. The runner and every
workflow it can execute therefore remain trusted; this Role is not a substitute
for restricting which workflows and revisions can run on a public repository's
self-hosted runner. The guarded Ansible image patch provides operational checks,
not an authorization boundary against a compromised runner.

On 2026-11-12, a disposable namespace with synthetic Deployments exercised the
equivalent Role using Kubernetes impersonation. The scoped identity got,
patched, and watched rollouts for both named Deployments; attempts to read
Secrets or Pods, read another Deployment, create Pods, or patch the production
namespace were denied. The temporary namespace was removed. This did not issue
or install runner credentials, modify production workloads, or test registry
access from the runner.

The current internal registry uses shared htpasswd authentication and does not
offer per-repository permissions. A separate username alone would still be able
to write the whole registry. Registry credential provisioning must account for
that boundary and preserve existing clients; do not copy root-only containerd
credentials into a runner or change the shared registry spec without a reviewed
Ansible change.
