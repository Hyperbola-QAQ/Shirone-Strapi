# Shirone-Strapi CI/CD candidate

Nothing in this directory is an active GitHub Actions workflow. The legacy
`CI` and `Deploy Mizuki to Kubernetes` workflows remain disabled; do not install
or enable the candidate until the dedicated runner, image provenance and
deployment controls below have been provisioned and tested. No article
publication or Strapi webhook invokes this pipeline: content stays in Strapi and
is served dynamically without rebuilding.

## Validation

`candidate-workflow.yml` is a reviewable, manual-dispatch-only candidate. Its
`shirone-ci-isolated` runner label is intentionally distinct from the old
`mizuki-deploy` runners. Do not attach it to any production host or existing
privileged runner. Provision a fresh disposable Linux runner with Node.js 22,
pnpm 12.16.1, Docker and read-only access to the verified internal image mirror;
it must have no production credentials, kubeconfig, Docker registry write
credentials or persistent state. Access to Docker grants the job host-level
privileges. Do not run untrusted pull requests or unreviewed branches there.

`validate.sh` insists on the disposable opt-in, frozen lockfiles and local
preverified base images. It runs unit tests, Astro and TypeScript checks, builds
the CMS, SSR and search-key reconciler images, then runs real disposable
PostgreSQL/Strapi/SSR/Meilisearch tests, administrator media isolation and the
uncertain search-swap recovery test. The project `.npmrc` configures the domestic
mirror. An unavailable/outdated mirror is an explicit failure to investigate;
never replace versions or skip integrity checks to make the build pass.

The candidate currently does **not** push images or create signed provenance.
Before promotion, establish a trustworthy binding between a reviewed commit,
successful validation, the built image and its immutable registry digest. A
mutable tag, a self-asserted commit label, or an unsigned file is insufficient.
Until that mechanism and the isolated runner exist, do not claim fully automated
CI/CD or install the candidate workflow.

## Controlled deployment candidate

`promote-reviewed.sh` is a controller-side entrypoint, not an automated job.
It requires a clean `main` checkout exactly matching the current remote main,
explicit reviewed deployment UID, old immutable digest and new immutable digest
matching the committed deployment manifest. It invokes the existing Ansible
project's guarded UID/resourceVersion and server-admission preview. The default
is preview only. `--apply` runs the same guarded playbook, waits for rollout and
checks the public site and admin endpoints. It does not turn on a workflow,
issue credentials, or trigger when CMS content changes.

```sh
bash deploy/ci/promote-reviewed.sh cms COMMIT UID OLD_IMAGE NEW_IMAGE
# Only when the image-to-commit validation/provenance has been independently
# verified and the operator intends this rollout:
bash deploy/ci/promote-reviewed.sh cms COMMIT UID OLD_IMAGE NEW_IMAGE --apply
```

The operator must also verify representative publication/search and protected
media behaviour after rollout. **There is no automatic rollback.** An interruption,
failed rollout or failed health check leaves the deployed image as-is and
requires inspection; the script exits nonzero. For a reviewed rollback, first
check schema and data compatibility, commit and push a reviewed manifest that
points to the prior immutable image, then invoke the same guarded entrypoint
using that new main commit, the current Deployment UID and the **currently
deployed** image as the old value:

```sh
bash deploy/ci/promote-reviewed.sh cms ROLLBACK_COMMIT UID CURRENT_IMAGE LAST_GOOD_IMAGE --apply
```

Substitute `ssr` for an SSR rollback. The source-commit, remote-main, expected
old-image and live UID/resourceVersion checks apply equally to rollback. Do not
blindly roll back a CMS schema migration. Existing site cutover rollback remains in
`deploy/k8s/README.md`; IPv4 edge rollback is described in
`deploy/ansible/shirone-ipv4/EDGE.md`.

The Ansible inventory and Vault authentication remain under the protected
operations project; no kubeconfig, API token, password or registry credential
belongs in this repository or a GitHub Actions log. The current scripts and
isolated tests do not prove that an actual GitHub runner, image-provenance path,
or production CD automation has been migrated.

The promotion entrypoint forces the operations project's Ansible configuration
and strict SSH host-key checking, verifies the control-plane identity from its
inventory without logging inventory values, and requires its playbook to match
the reviewed Git copy byte for byte. It executes a temporary exact copy with
SSH-key authentication and a one-host inventory because these narrow playbooks
do not need the unrelated encrypted group variables. A real read-only CMS
admission preview of that execution path passed with zero changes. The complete
wrapper also passed a real CMS admission preview from a clean synthetic Git
repository with a local bare remote and a reviewed prior digest; that test did
not use the production GitHub remote or apply the patch. No production apply or
failure/rollback drill was performed by this candidate.
