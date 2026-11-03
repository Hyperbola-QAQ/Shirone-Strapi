# Inactive encrypted promotion candidate

`promote-encrypted-candidate.py` connects the existing candidates for review;
it is not installed as a workflow and is **not approved for production**. Its
default invokes only the guarded Ansible admission preview after verification.
No real GitHub-hosted attestation, internal OCI archive transfer, node pull,
deployment or rollback has been tested with this wrapper. An isolated real
`skopeo` 1.24.1 test copied a Buildx OCI archive to a temporary loopback TLS
registry with certificate verification and retained the exact manifest digest;
that does not establish that the production registry or wrapper works.

The operator must independently review and pin the build source commit, the
signing workflow revision, the recipient public key, ciphertext SHA-256 and all
three expected OCI manifest digests. Supplying these values does not establish
that they are trustworthy. The tool verifies the local and remote main commit,
and permits at most two linear CMS/SSR manifest-only commits following the
build commit. Each changed image must equal its independently supplied digest
from the same verified encrypted envelope; the target component must be last.
Other changes, merges, duplicate components, a dirty worktree and stale remote
main fail closed. Deploy the first component before pushing the second manifest
commit; the underlying guarded entrypoint requires the exact current main.

The tool first verifies the separate signed metadata file, requiring it to bind
the independently expected ciphertext digest, recipient, source and all three
OCI manifest digests. It then invokes GitHub's attestation verifier for the
exact ciphertext before opening the private key or decrypting. Both attestations
must have the configured repository, signer workflow, independently trusted
signer revision and build source, main ref and GitHub-hosted signer. Neither a
partially signed release nor an untrusted metadata file authorizes deployment.
After complete `age` authentication it
validates the three actual embedded OCI archives, extracts them to a newly
created private temporary directory, and rechecks the target manifest. The
temporary plaintext is removed when the normal process exits, but abrupt
termination, snapshots and swap may retain it. Protect the temp filesystem and
review available space and cleanup before real use. This is an ephemeral release
transfer and must not include CMS database, uploads, or other production data;
it does not restore the user's deleted scheduled backup mechanism.

By default the verified target image must already be present in the internal
registry. `--copy` explicitly copies the verified local OCI archive to a fresh
random internal staging tag using the protected registry CA, `skopeo copy
--preserve-digests`, and then checks both tag and immutable digest. A failed
copy can leave the new staging tag; do not assume an interrupted copy was
removed. Registry credentials must come from the existing protected external
credential mechanism; no credentials are accepted on the command line. The
wrapper then calls `promote-reviewed.sh`, which independently requires clean
current main, exact prior live image, Deployment UID and guarded resource
version. `--apply` explicitly requests that existing guarded deployment; without
it the wrapper only performs a server admission preview. The optional copy is
a registry write even when `--apply` is absent.

```sh
python3 deploy/ci/test-promote-encrypted-candidate.py
```

Before use, exercise actual file-subject attestation, full archive verification,
`skopeo` OCI archive copy and digest preservation, all node pulls, Deployment
and Pod image identities, readiness and content/search/media behavior in an
isolated environment. These test cases currently mock external cryptography,
registry and Ansible, though separate tests exercise actual `age` and OCI.
This candidate cannot safely authorize rollback to an un-attested older image:
the old running image, approved previous manifest, trusted last-known-good
record and CMS schema compatibility require a separately reviewed recovery
path. Do not automatically revert database migrations or treat a successful
rollout status/HTTP 200 as proof of an application-complete release.
