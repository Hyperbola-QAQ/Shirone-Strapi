# Inactive attested-image copy candidate

`copy-attested-image-candidate.py` is a review candidate only. It is not wired
into CI or deployment, and it has not been tested against actual attestations,
GHCR, `skopeo`, or the destination registry. Its default is read-only: verify
the exact fixed GHCR image with the same source, independently trusted signer,
workflow, main ref and hosted-runner checks as the provenance candidate, then
hash the raw source OCI manifest. `--apply` explicitly copies all manifests
with `--preserve-digests` to a random staging tag and hashes both the destination
digest reference and that tag. Deploy by digest only, never by staging tag.

```sh
python3 deploy/ci/test-copy-attested-image-candidate.py
python3 deploy/ci/copy-attested-image-candidate.py cms BUILD_COMMIT TRUSTED_SIGNER_COMMIT sha256:IMAGE_DIGEST
```

Execution needs an independently installed and verified `skopeo`, authenticated
GitHub CLI and GHCR/registry access through a secure external credential helper.
The candidate does not accept, print, create or store credentials, and it never
disables TLS verification. If the existing internal registry does not provide
trusted TLS, the copy must fail until a secure transport is arranged; do not
switch TLS verification off or send credentials over plaintext HTTP. A copy can
fail after creating a staging tag; that tag may require separate careful cleanup.
Digest identity is an integrity property, not proof that a registry has retained
all required blobs indefinitely; production validation must separately test
pulls on the target cluster and the actual multi-architecture/index behavior.
This copy check does not authorize deployment or replace the source/deployment
commit and manifest verification in `verify-provenance-candidate.py`.
