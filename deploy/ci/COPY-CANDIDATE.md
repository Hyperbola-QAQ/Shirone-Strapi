# Inactive attested-image copy candidate

`copy-attested-image-candidate.py` is a review candidate only. It is not wired
into CI or deployment, and it has not been tested against actual attestations,
GHCR or an actual cross-registry copy. Its default is read-only: verify
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
disables TLS verification. The controller's existing administrator-managed
`/etc/docker/certs.d/192.168.86.100:30500/ca.crt` has been checked with curl
against the registry: TLS verification succeeds and the endpoint returns 401
without authentication. On `--apply` the candidate requires that directory
and the regular CA file to be root-owned and not group/world-writable, with
no other files in the directory. It passes this protected directory as
`--dest-cert-dir` to `skopeo copy` and as `--cert-dir` to destination inspections.
It never copies or accepts private keys, and must fail if the CA or TLS is not
trusted. A checksum-verified temporary `skopeo` 1.24.1 from the configured
Tsinghua mirror successfully read an existing internal Node manifest using
this CA and existing local authentication; its raw SHA-256 matched the pinned
digest. Cross-registry copying and GHCR authentication remain untested. A copy can
fail after creating a staging tag; that tag may require separate careful cleanup.
Digest identity is an integrity property, not proof that a registry has retained
all required blobs indefinitely; production validation must separately test
pulls on the target cluster and the actual multi-architecture/index behavior.
This copy check does not authorize deployment or replace the source/deployment
commit and manifest verification in `verify-provenance-candidate.py`.
