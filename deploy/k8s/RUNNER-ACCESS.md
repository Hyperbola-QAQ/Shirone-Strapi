# Candidate access for the existing release runner

`runner-rbac-candidate.yaml` is an unapplied, namespace-scoped ServiceAccount,
Role, and RoleBinding for updating the two Shirone Deployments. It permits
`get`, `list`, `watch`, and `patch` only for `shirone-cms` and `shirone-ssr`.
The Role does not grant access to Secrets, Pods, other Deployments, or other
namespaces. Service account token automount is disabled. The create-only
`deploy/ansible/provision-runner-access.yml` candidate explicitly creates a
dedicated service-account-token Secret and writes a runner-owned 0600
kubeconfig without logging the token. This is a long-lived token: deleting its
Secret invalidates it, and access also stops if its RoleBinding is removed.
Neither the token nor the kubeconfig belongs in Git. The playbook defaults to
admission checks only, refuses existing names or a destination kubeconfig, and
requires explicit confirmation before creating anything. If interrupted,
inspect the exact objects and destination before cleanup; do not blindly retry
or remove pre-existing objects.

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
or install runner credentials on production, modify production workloads, or
test registry access from the runner. A second disposable namespace exercised
the create-only Ansible entrypoint, its generated token and a private temporary
kubeconfig; synthetic Deployment read/patch succeeded and Secret listing was
denied. The temporary namespace and credential directory were removed.

The current internal registry uses shared htpasswd authentication and does not
offer per-repository permissions. A separate username alone would still be able
to write the whole registry. Registry credential provisioning must account for
that boundary and preserve existing clients; do not copy root-only containerd
credentials into a runner or change the shared registry spec without a reviewed
Ansible change.

On 2026-11-13 the create-only entrypoint was synchronized to the Ansible source
as `provision_shirone_runner_access.yml`. Its real production check returned
11 ok and 0 changed, then explicit application returned 23 ok and 4 changed.
The created `mizuki` objects have these UIDs: ServiceAccount
`41c07216-039d-47e7-b56d-18f09b2db6d9`, Role
`bb2f3ccf-5782-4919-bd94-c15b11203211`, RoleBinding
`a015bfba-e4e8-4512-9b7a-2bed502e768c`, and token Secret
`755c5c84-161c-4071-a0c7-54821ded4f8e`. The generated kubeconfig is owned by
`github-actions`, mode 0600, at
`/home/github-actions/.local/state/shirone-release/kubeconfig` on imini.
As the actual runner user, reads of both Deployments and a no-change CMS patch
through server admission succeeded; listing Secrets and reading the old-site
Deployment were forbidden. No deployment image was changed.

To revoke, stop the release workflow first, verify the current object UIDs and
runner file identity, then remove the dedicated token Secret and RoleBinding;
remove the dedicated kubeconfig, Role and ServiceAccount only after confirming
they are the objects above and no other consumer depends on them. Do not print
the token or kubeconfig. The long-lived token does not expire automatically.
