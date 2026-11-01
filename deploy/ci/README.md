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

`hosted-candidate.yml` is a second inactive, manual-dispatch-only candidate for
GitHub-hosted source validation. Its `hosted-validate.sh` obtains exact Node
22.23.4 and pnpm 12.16.1 from the domestic mirror first, validates the pinned
SHA-256/SHA-512 values, and only falls back to the official source on download
or checksum failure while retaining the same version and checksum. It runs the
source tests, checks and builds; it does not claim to build or attest the exact
private-registry production images. The Node checksum and pnpm integrity value
were checked against both domestic and official metadata. A full clean local
checkout simulation with an empty HOME passed the actual mirror downloads,
integrity checks, source tests, type checks and both production builds. The CMS
build runs in a separate temporary directory so that Strapi does not pick up
the root Astro TypeScript configuration. An actual GitHub-hosted run has not
yet been tested. Neither candidate lives in
`.github/workflows`, and neither has been activated.

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

## Next steps for isolated runner and provenance

`verify-provenance-candidate.py` is an **inactive, unintegrated candidate** for
separating a build source commit from a subsequent deployment-manifest commit.
It has only isolated tests with a simulated `gh`; no real signed attestation,
published GHCR image, registry copy, or deployment has been verified. Do not
use its exit status as authorization to deploy. It requires PyYAML and an
independently reviewed, explicitly supplied workflow revision, and fixes the
candidate signer identity to `.github/workflows/shirone-release.yml` (which
does not yet exist). It requires the deployment commit to be a direct child of
the build commit, change only the relevant deployment YAML and change only its
image value semantically. It pins source commit, signer commit, main ref,
hosted runner and immutable image digest in `gh attestation verify`. The GitHub
CLI must be authenticated to GitHub and to GHCR for OCI subject retrieval;
the internal registry must receive the exact same manifest digest, with actual
copy and pull verification implemented before production use. A new unrelated
commit between source and deployment will be refused by this deliberately
strict candidate. It also cannot sequentially deploy both CMS and SSR built
from the same source commit: after the first component's manifest commit, the
second component's commit cannot be a direct child of that source commit.
Before production integration, design and test a multi-component release that
verifies every intervening commit, each attested subject, and only the
explicitly approved manifest image changes; do not simply relax the ancestry
or manifest-change checks. This check is not wired into
`promote-reviewed.sh`.

```sh
python3 deploy/ci/test-provenance-candidate.py
python3 deploy/ci/verify-provenance-candidate.py cms BUILD_COMMIT DEPLOY_COMMIT TRUSTED_SIGNER_COMMIT sha256:IMAGE_DIGEST
```

The repository is public. Standard GitHub-hosted Linux jobs run on fresh VMs,
which avoid granting a persistent self-hosted runner and its Docker socket access
to the production hosts. The three existing `mizuki-deploy` runners are online
and are **not** suitable substitutes for the proposed isolated runner. At the
time of review, the repository's default workflow token permissions were write
and workflows could approve pull requests; the candidate explicitly limits its
token to `contents: read`. Review repository-wide permissions separately before
activation. The candidate has neither been installed nor enabled.

The exact current Node, PostgreSQL and Meilisearch inputs are pinned in the
private `192.168.86.100:30500` registry, which a GitHub-hosted VM cannot reach.
Before using a hosted runner, establish how it will obtain the exact reviewed
images using a trusted accessible mirror, while preserving required versions,
platforms and digest checks. Do not open the private registry to the internet
or silently substitute a different `ca-clean` image. Confirm whether the
hosted runner's actual free disk and memory can build and test all images. If
the exact inputs cannot safely be supplied, keep the pipeline disabled; a
separate, disposable VM with no production credentials is an alternative only
after its isolation and limited registry access are verified.

A possible trusted release path is a separately reviewed, main-only,
GitHub-hosted build that tests the exact images it publishes, records each OCI
manifest digest and produces a GitHub artifact attestation. The release job
would need narrowly scoped `contents: read`, `id-token: write` and
`attestations: write`, plus `packages: write` only if it publishes to GHCR.
Attestation proves provenance of its subject, not that meaningful tests were
run; the trusted workflow must fail closed on test failures, and its exact
workflow revision must be independently trusted. On the controller, verify the
attested digest against the repository, signer workflow, expected source commit
and ref, and require a GitHub-hosted signer. For example, after choosing and
reviewing an actual signer workflow and obtaining an authenticated OCI image:

```sh
gh attestation verify 'oci://REGISTRY/IMAGE@sha256:DIGEST' \
  --repo Hyperbola-QAQ/Shirone-Strapi \
  --signer-workflow Hyperbola-QAQ/Shirone-Strapi/.github/workflows/REVIEWED.yml \
  --source-ref refs/heads/main --source-digest REVIEWED_COMMIT \
  --signer-digest REVIEWED_WORKFLOW_COMMIT --deny-self-hosted-runners
```

If a verified image is copied into the internal registry, use an OCI copy
method that preserves the complete manifest digest and verify the destination
digest equals the attested source before promotion. A `docker pull/tag/push`
may rewrite the manifest and must not be assumed to preserve it. Registry
access, package visibility, artifact attestation, image transfer, hosted-runner
resource limits and a production promotion have **not** been tested or
configured here. Do not enable automated publishing or deployment until the
full exact-image path and failure/rollback handling have been exercised.

References: [GitHub-hosted runner isolation](https://docs.github.com/en/actions/how-tos/write-workflows/choose-where-workflows-run/choose-the-runner-for-a-job),
[GitHub artifact attestations](https://docs.github.com/en/actions/how-tos/secure-your-work/use-artifact-attestations/use-artifact-attestations),
[attestation verification flags](https://cli.github.com/manual/gh_attestation_verify).
