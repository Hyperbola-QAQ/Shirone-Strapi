# Inactive promotion candidate

`promote-attested-candidate.py` is a review candidate; it is not wired into CI
and has never performed a real attestation verification, copy, promotion or
rollback. Its isolated test mocks every external action. Do not use it as a
production authorization boundary until the entire path is independently
reviewed and exercised, including actual GHCR and cluster-node pulls.

The candidate accepts a build source commit, a separately committed deployment
manifest, and an **independently reviewed and protected signer workflow commit**;
the caller must establish trust in the signer rather than deriving it from the
repository being deployed. The source verifier checks the entire permitted
release chain and all component attestations. The candidate then verifies the
raw internal-registry manifest against the target digest with the protected CA,
and invokes the existing guarded Ansible entrypoint. Preview is the default;
`--apply` explicitly requests the entrypoint's guarded image change. The image
must already have been copied securely; this candidate does not copy it.

The verifier permits two successive single-component manifest commits. Deploy
the first component before advancing main to the second manifest commit, then
verify and deploy the second; the final component must match the last commit.
The current rollout and HTTP checks do not establish every Pod's platform image
identity or application behavior, and a registry manifest check does not prove
cluster nodes can pull every blob. Those remain required validation.

This is **not a rollback design**. The current verifier requires attestations
for the original build source and a short release chain; older production images
may predate attestations, and database schema compatibility cannot be inferred
from the image digest. An operator needs a separately reviewed last-known-good
record, compatible database state and an explicitly controlled recovery process.
Never silently bypass provenance or automatically revert a CMS migration.

```sh
python3 deploy/ci/test-promote-attested-candidate.py
```
