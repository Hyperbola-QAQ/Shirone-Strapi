# Encrypted OCI release artifacts: design only

This candidate avoids giving a public repository's Actions access to private
GHCR base packages and avoids uploading plaintext release images as public
workflow artifacts. It is not implemented or approved for production. This
document does not generate keys, enable workflows, access production, or
retain database or upload content. Existing workflows remain disabled.

The proposal concerns only short-lived transfer of rebuildable OCI release
artifacts, not scheduled backups of the CMS database, uploaded files or other
user data. The user explicitly removed that backup mechanism; do not recreate
it or add production content to release artifacts.

## Build, test, encrypt and attest

1. Use a reviewed exact `main` source commit and reconstruct required build and
   test bases from digest-pinned public parents. Verify Node version, binary,
   CA removals and the remaining filesystem and image configuration as set out
   in `BASE-RECONSTRUCTION-CANDIDATE.md`; unverified reconstruction cannot be a
   release input. Build exact CMS, SSR and reconciler OCI archives on a fresh
   GitHub-hosted runner without any production credentials or signing/OIDC
   permissions.
2. In the **same trusted job**, verify the archive and its selected platform and
   OCI descriptors; run source and full disposable CMS, database, SSR, search,
   protected-media and search-recovery integration tests against those precise
   image identities. Record each plaintext archive SHA-256 and selected OCI
   manifest digest before and after testing, and stop if either changes. The
   job must stop on any test failure, stale or unreviewed commit, unexpected
   platform, invalid descriptor, changed archive or missing result.
3. Create an envelope containing only the release archives and a strictly
   parsed manifest binding component names, source commit, plaintext archive
   SHA-256 values, OCI manifest digests and the intended recipient public-key
   identity. Validate exact expected entries and bounded sizes. Encrypt the
   envelope with a reviewed, pinned `age` implementation and an independently
   checked controller public key whose exact value is bound to the trusted
   workflow revision. Do not generate or import the controller's private key
   into CI. Remove plaintext from the upload set, and upload only the resulting
   ciphertext as a run-specific immutable workflow artifact. No production
   credentials, database, uploads, logs or `.env` files belong in the envelope.
4. A **separate fresh signing job** runs only after all prior checks succeed.
   It does not check out or execute project scripts. Download the exact
   build-run artifact ID, independently compare its ciphertext SHA-256 to the
   expected build-job output, and use a pinned, reviewed `actions/attest` with
   `subject-path` to attest that exact ciphertext file. Give OIDC and
   attestation permissions only to this signing job; no GHCR package permission
   is required. Fail closed on missing artifacts, hashes, jobs or unexpected
   source/ref/workflow identity.

`age` recipient encryption provides confidentiality to the private-key
holder, but **does not identify the sender**: anyone knowing the public key can
encrypt a file to it. The GitHub attestation is a separate, necessary origin
and integrity check for the ciphertext. It must be verified, not merely present.

## Trust boundaries and disclosure

Attestation identifies the signed ciphertext and workflow; it does not prove
that meaningful tests ran or that the plaintext is safe. The build/test job,
its dependency installers and all code it executes remain trusted. Malicious
code or a surviving process in that job could falsify test results, alter the
archive between checks, replace the recipient key or expose plaintext. The
test-to-encrypt sequence, process isolation, hash checks, workflow revision,
reviewed source/ref, runner image and immutable artifact IDs require an
independent security review and actual tests. A separate sign runner reduces
access to the signing OIDC identity; it does not eliminate trust in the build
job. Protect the exact recipient public key against unauthorized substitution.

The public artifact service can expose ciphertext size, names, timing, source
run and attestation metadata. Avoid revealing sensitive names or logging
plaintext, credentials, database content or secrets; disable caches and debug
outputs that might retain plaintext. Do not rely on encryption to excuse
copying secrets into images. Validate `.dockerignore`, complete image layers,
build context and job outputs before real use. Runner compromise or malicious
build code can still leak contents before encryption.

## Controller verification and deployment

`release-envelope-candidate.py` is a local-only, inactive experiment for this
stage. It builds an unencrypted envelope with exactly three named OCI archives
and fixed-format metadata. Inspection requires externally supplied expected
source commit, recipient fingerprint and each expected manifest digest; it
checks exact members, sizes and archive hashes before optionally extracting
into a newly created private directory. It rechecks extracted OCI descriptors
and diff IDs using `verify-oci-archive-candidate.py`. The flag
`--authenticated-input` is only the caller's acknowledgment; it does **not**
verify an attestation or perform age decryption. The caller must do those
operations successfully first. Failures can leave a new private partial file or
directory for the operator to inspect and clean; the candidate never removes
existing data or overwrites existing output. Its local mock tests and a small
real OCI fixture do not prove real GitHub or cryptographic operation.

Download the exact expected run artifact, then **verify the ciphertext
attestation before decrypting** with `gh attestation verify`, enforcing the
repository, independently trusted signer workflow and revision, source commit,
main ref, GitHub-hosted signer and expected artifact identity. Pin the intended
release and live deployment preconditions to prevent accepting a valid but old
ciphertext as a new release. Public-key encryption alone does not prevent replay
or an attacker generating new ciphertext.

The controller private key stays in the existing protected operations
environment, is never printed or committed, and is not sent to GitHub, a runner
or a container image. Key access, replacement and revocation need reviewed
procedures; losing the key makes old ciphertext unreadable. Do not add key
copies or automatic backups contrary to the user's instructions. A rotation
must account for intentionally retained, still-needed releases, without
silently destroying existing production credentials.

Decrypt into a private temporary location with restrictive permissions and
bounded available space. Wait for successful completion and authentication of
the **entire** ciphertext before unpacking or importing anything; reject
partial plaintext on failure. Parse the envelope without unsafe extraction,
verify the expected component set, source commit, recipient identity and
plaintext archive hashes, then independently verify every OCI descriptor,
platform, layer digest and manifest. Compare the derived manifest digest to
the signed-inside envelope value; the ciphertext digest, plaintext archive
digest and OCI manifest digest are three distinct values.

Only after these checks copy to the internal registry using its trusted CA,
preserving manifest digests; verify destination manifests/blobs and an actual
target-node pull. Use guarded Ansible promotion with expected live deployment
UID, previous image and resource version. Verify runtime image identities and
application publication/search/media/draft behavior. A valid attestation does
not prove database schema compatibility: rollback requires a separately
reviewed last-known-good image and compatible data state, and must not depend
on a short-lived ciphertext artifact still existing.

## Retention and verification still required

Set the shortest useful explicit artifact retention supported by GitHub;
document quota, potential charges, failed-run cleanup and the fact that
deletion on expiration is not immediate or equivalent to erasing prior
downloads. Encrypt before any upload. Retain only what is needed for release
transfer; do not schedule periodic backups or package CMS data. Validate
controller disk capacity and cleanup of plaintext on failures, cancellations
and process crashes, including whether swap or filesystem snapshots affect the
confidentiality guarantee.

No key, encrypted artifact, workflow, real GitHub-hosted run, signed ciphertext,
decryption, registry transfer, production rollout or rollback has been created
or exercised under this proposal. Failure, replay, wrong key, key rotation,
truncation, artifact replacement, bad attestation, OCI corruption and recovery
cases must be independently tested before enabling it.

References: [age](https://github.com/FiloSottile/age),
[GitHub artifact attestations](https://docs.github.com/en/actions/how-tos/secure-your-work/use-artifact-attestations/use-artifact-attestations),
and [GitHub workflow artifact access](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/download-workflow-artifacts).
