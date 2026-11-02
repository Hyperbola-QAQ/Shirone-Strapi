# Public OCI archive release path: design only

This is an alternative to granting a public repository's Actions access to
private GHCR packages. It is not implemented, tested, or approved for
production. The existing workflows remain disabled and the current GHCR-based
candidate does not implement this design.

## Disclosure and resource boundary

GitHub Actions artifacts for a public repository can be downloaded by
GitHub users with repository read access. Treat every uploaded image archive,
including its metadata, file names and **all historical layers**, as public.
Before uploading anything, inspect the complete archives and build inputs for
credentials, private content and data that the owner has not authorized for
publication. Scanning only the final image filesystem is insufficient because
deleted files can survive in lower layers. Source code being public does not
prove the archive is safe. Build in a clean checkout, disable persisted checkout
credentials, exclude protected environment files and obtain no production
secrets. Reject the run if disclosure safety cannot be established. If the
image must remain private, this design is unsuitable.

The current local CMS and SSR images are large (approximately 1.32 GB and
1.84 GB uncompressed at the time of inspection). Archive transfer, hosted
runner free disk, network limits, artifact retention, GitHub storage quota and
possible billing need actual measurement before any enabled or recurring use.
Use the shortest useful retention supported by GitHub; never assume the free
storage allowance is sufficient.

## Proposed isolated jobs

1. **Source validation:** on a fresh GitHub-hosted runner, run the reviewed
   tests and builds from an exact main commit, using checksum-pinned tools and
   domestic mirrors first. Expose neither registry credentials nor OIDC signing
   permissions to the build process.
2. **Build:** independently reconstruct and verify the reviewed Node and
   PostgreSQL inputs using public, digest-pinned parent images and the pinned
   same-version Node archive. The CA remediation, filesystem and execution
   configuration equivalence checks in `BASE-RECONSTRUCTION-CANDIDATE.md` must
   pass. A different OCI digest is a new input, never the existing production
   digest under another name. Build CMS, SSR and search reconciler into OCI
   archives, check their complete disclosure boundary, calculate each archive's
   SHA-256 and upload them as immutable, run-specific Actions artifacts. Fail on
   a missing archive, unexpected platform, digest mismatch or unsafe content.
3. **Integration test:** on a separate fresh runner, download the exact artifact
   IDs from that build and verify each archive hash against the build output.
   Import the archives into a disposable test runtime, verify the imported
   manifest and platform identity, then run the existing real CMS/PostgreSQL,
   SSR, Meilisearch, protected-media and search-recovery tests against those
   exact images. Record precisely how archive identity relates to the tested
   manifest; do not silently accept an import that rewrites its manifest.
4. **Sign:** only after source, build and integration jobs succeed, use a fresh
   runner with no project checkout or project scripts. Download the same fixed
   artifact IDs and check their hashes against the build outputs. `actions/attest`
   can attest the archive **files** using `subject-path`; pin and review all
   actions by commit SHA. Grant only the necessary OIDC and attestation
   permissions here. No `packages: write` or access to the private base packages
   is required. Keep workflow and source revision protected and independently
   reviewed: a malicious build or test job can otherwise attest bad artifacts.

Do not treat a passing signature as proof of the quality of the tests. The
trusted workflow must actually gate signing on tests of the same artifacts,
and an operator must independently trust the signer workflow revision and its
inputs. Run-specific artifact identifiers, file names and hashes must be
strictly constrained; fail closed on ambiguity, a missing artifact, a stale
commit or an unexpected workflow/ref.

## From signed file to cluster image

The attested subject is the **archive file SHA-256**, not automatically an OCI
manifest digest. On the operator controller, first use `gh attestation verify`
on the exact downloaded file, pinning the repository, signer workflow and
independently reviewed signer commit, expected source commit/ref and hosted
runner identity. Then independently hash the archive and parse the OCI layout:
validate its manifest/index descriptors, config and layer digests and select
only the reviewed `linux/amd64` subject. Record both the verified archive hash
and the resulting manifest digest as distinct values. Never infer one hash from
the other or trust a digest written only in a build log.

Copy the verified OCI archive to the existing internal registry using a method
that preserves the complete manifest digest, with the existing trusted
registry CA and scoped credentials outside the repository. Verify the raw
destination manifest against the derived OCI digest, verify all referenced
blobs exist and confirm target nodes can actually pull it. Any media-type,
index or digest rewrite invalidates the claimed identity and must stop the
release. Current copy and promotion candidates expect a GHCR OCI subject and
**must be adapted** before they can implement this file-subject design.

Use the reviewed Ansible-controlled UID, previous-image and resource-version
guards for promotion. Confirm the running Deployment and Pod image identities,
publication/search behavior, draft and media restrictions, and keep a
separately reviewed, schema-compatible rollback plan. None of the actual
archive upload/download, file attestation, OCI import/copy, production rollout
or rollback has been demonstrated by this design.

References: [GitHub artifact download access](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/download-workflow-artifacts),
[GitHub artifact attestations](https://docs.github.com/en/actions/how-tos/secure-your-work/use-artifact-attestations/use-artifact-attestations),
and [actions/attest file subjects](https://github.com/actions/attest).
