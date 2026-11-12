# GitHub-hosted attestation and encrypted OCI smoke tests

On 2026-11-04, two manual-only GitHub-hosted smoke workflows were run without
production credentials, production cluster access, automatic triggers, or
automatic deployment. Both temporary workflows are now disabled manually, as
are the old Mizuki CI and deployment workflows.

Run `41746624117`, source and signer commit
`8cf4516ebdbab81869ad99aafaeb304dd8e3defb`, produced two short synthetic
files and independently attested them on a separate GitHub-hosted runner.
For both files, `gh attestation verify` succeeded with the repository, smoke
workflow, main ref, exact source and signer commits, and rejection of
self-hosted signers enforced. Tampered data, an incorrect source commit and
an incorrect signer commit were each rejected. The synthetic artifact was
408 bytes and was deleted by its specific artifact ID after verification.

Run `41747883539`, source and signer commit
`18de3fbac66034a2b8dec506ffbb9269f6e71994`, built three tiny `scratch` OCI
images containing only synthetic markers, encrypted the envelope with the
reviewed release recipient, and attested both encrypted bytes and metadata on
a separate runner. The GitHub artifact was 31,515 bytes. On the controller,
actual GitHub attestations were verified for both files with the corresponding
smoke workflow and all identity constraints; the protected recipient identity
then successfully decrypted the envelope, whose OCI descriptors and diff IDs
were checked. Tampered ciphertext and metadata were rejected by real GitHub
attestation verification. The synthetic artifact was deleted by its specific
artifact ID and the task's temporary plaintext, ciphertext and tooling were
cleaned up. The controller's existing protected identity was retained.

The first encrypted smoke run, `41747512921`, revealed that the default
GitHub-hosted Docker driver cannot use the OCI exporter. The candidate now
creates a separate docker-container builder pinned to a specific BuildKit
linux/amd64 manifest. The controller's DaoCloud request returned HTTP 401;
on the hosted runner the same pinned image was successfully downloaded from
DaoCloud. The official registry was consulted to verify the manifest digest.
Automated checks cover mirror-first selection and same-digest fallback. GitHub
reported Node 20 deprecation annotations for pinned v4
checkout/upload/download actions and ran those actions using Node 24.

An additional manual run, `41748644074`, verified `docker load` and the loaded
image identities for the three synthetic OCI archives. The immediately prior
attempt, `41748430875`, showed that a hosted runner's classic Docker image
store cannot load an OCI archive. The workflow now enables Docker's containerd
image store only inside a disposable GitHub-hosted VM and confirms it before
using the pinned builder; non-hosted invocation is rejected. The corrected
smoke run also completed both attestations. Its 31,517-byte synthetic artifact
was deleted by exact ID, and the temporary smoke workflow was disabled again.

These small synthetic tests validate the actual hosted signing, transport and
controller verification mechanics only. They do **not** validate the full
application images, a full-size encrypted artifact, production promotion,
automatic deployment, rollback, GitHub Actions billing headroom or the actual
release workflow's signer revision. Account-level billing information was not
available to the existing minimally scoped GitHub credential. At the time of
inspection the active production CMS and SSR compressed layers alone totaled
approximately 760 MB; GitHub Free includes 500 MB of Actions artifact storage.
Do not assume a full-size upload will fit or be free.

## imini runner artifact transport (2026-11-12)

Manual run `43144583772` created a small synthetic fixture on a GitHub-hosted
runner, then used the job's short-lived `GITHUB_TOKEN` with `actions: read` on
the uniquely selected `hyqaq-imini-mizuki` self-hosted runner to download it.
The actual runner name, file SHA-256 and cleanup step all verified successfully.
The smoke workflow was disabled, its exact artifact `13910927640` deleted, and
the temporary runner label removed afterward. Old Mizuki CI and deployment
workflows remained disabled. No production credentials or Kubernetes access
were used. This proves same-run synthetic artifact transport, not large
artifact handling, signature verification on the runner, production deployment,
or rollback. Full-size release artifact signing and verification were separately
validated in later runs documented in the release and deployment notes.
