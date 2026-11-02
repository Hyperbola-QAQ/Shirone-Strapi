# Hosted reconstruction of private base images: review only

This is a proposed way to avoid granting a public repository's Actions access
to private base packages. It is not complete or approved for production. Do
not replace any existing image pin, enable a workflow, or claim that a rebuilt
image has the original digest until an exact digest comparison proves it.

The controller's Ansible source contains
`/home/hyperbola/Work/ansible-debian-k8s/tools/rebuild_ca_image.py`. It has no
license notice, and the enclosing operations directory is not a Git checkout
with an identifiable license. Do not copy that script into this public
repository without establishing permission and license terms. It uses only
Python's standard library, but its dependencies and permission to redistribute
are separate questions. A clean implementation can be written from the
behavioral requirements below after review; it must not embed credentials,
private registry data or unrelated host configuration.

## Known inputs and desired behavior

`rebuild-node-candidate.sh` implements only a local Node executable replacement
experiment. It requires a pre-downloaded archive, verifies both the fixed
archive and executable SHA-256, and only builds from an already available
digest-pinned parent. `--check` performs no image build; `--build` creates a
local candidate only. There is deliberately no release mode. It does not yet
implement or verify CA remediation or full equivalence, and must not be used
for release. Docker BuildKit may contact a registry for metadata even with
`--network=none`; that flag only restricts build-step networking.

On 2026-11-02, a local smoke test used the configured domestic mirrors for
the exact archive and parent. Archive and executable hash checks passed; the
candidate starts Node 22.23.4 and npm 10.9.9 and matches the production Node
binary hash. Invalid archive and nonexistent release mode were rejected. This
does not establish complete filesystem, CA, runtime or OCI digest equivalence.

`prepare-ca-overlay-candidate.py` is a separate offline experiment: it reads a
trusted Docker-export tar, creates a new build context containing only modified
CA bundles and a deletion script for the affected certificates and aliases,
and refuses unsafe or unexpected paths. It does not establish the input image's
identity, handle arbitrary images, or approve a release. Its four isolated
tests cover removal, alias handling, existing output and unsafe paths.

In a local test against the pinned PostgreSQL parent, it found four affected
bundles, six removed files/aliases and eight certificate occurrences. An
isolated image built with the overlay had identical bundle bytes, file mode and
ownership to the current production PostgreSQL image. A Docker-export
comparison found no path, regular-file content, file type, mode, ownership or
link-target differences (excluding `.dockerenv`); User, Entrypoint, Cmd,
WorkingDir, Env, ExposedPorts, Volumes and Healthcheck also matched. Docker
export does not prove extended attributes, all timestamps, actual mounted
volumes, OCI descriptor equality or runtime behavior. The Node candidate's
export contained no denied certificates; the tool correctly rejected it
instead of creating a pretend remediation. These observations do not replace
the full acceptance checks below, and no image or workflow was deployed.

- On linux/amd64, obtain the exact reviewed parent manifests by digest from a
  trusted mirror: Node `sha256:25330af3531fb5e23318554a0aa911125b6e91b1b777edf7655501d207c067a2`
  and PostgreSQL `sha256:e6e1f467b52112f7e08aa380de81473106ff54b1834d90bcb4265a2a018e1921`.
  Verify manifest bytes, selected platform, config and every layer against
  their descriptors; reject an index selecting the wrong platform.
- The production Node base replaces the parent's Node executable with the
  exact Node 22.23.4 binary and updates `NODE_VERSION`; it does not replace
  the parent's entire Node installation. In particular, production reports
  npm 10.9.9. Download the 22.23.4 archive from the configured domestic
  mirror first, verify its pinned SHA-256, and compare the executable hash
  against an independently established expected value. If the mirror fails or
  mismatches, the official source may be tried with the same version and hash.
- The reviewed remediation removes certificates with SHA-256 fingerprints
  `86a1ecba089c4a8d3bbe2734c612ba341d813e043cf9e8a862cd5c57a36bbe6b`
  and `d7a7a0fb5d7e2731d771e9484ebcdef71d5f0c3e0a2948782bc83ee0ea699ef4`.
  It retains the other certificates in order, removes now-empty certificate
  files and their aliases, and applies equivalent whiteouts. Validate every
  resulting trust bundle; do not silently remove other trust roots.
- Preserve all unrelated filesystem contents and metadata, symlinks,
  ownership, modes and the image's execution configuration, including User,
  Entrypoint, Cmd, WorkingDir, environment, exposed ports, volumes and health
  check. Record intended label/history differences separately. Any file or
  configuration change outside the reviewed set fails verification.

The current operations tool writes the current time into image history, so
rerunning it does not establish identical OCI content. A stronger optional
experiment would pin the original reviewed history timestamp and all
serialization, tar and gzip behavior and compare resulting raw manifest bytes
to the existing production digests: Node
`sha256:4de898ca0d4e1f2169d9b846df3710f29f6787fd3131211e2e74a7898c0528cc`
and PostgreSQL
`sha256:cc634644a1d502108b9282bd6e92474db4b96c4bb2480410fe24cf6cde823457`.
Reproducibility is unproven: Python/gzip version, OCI descriptor selection,
file metadata and timestamp must all match. Stop on any digest mismatch; do
not relabel a different manifest as the original.

## Evidence required before using reconstructed inputs

1. Compare the reconstructed image to the actual production image offline:
   every effective filesystem entry, content hash, uid/gid, mode, symlink
   target and relevant extended attributes, and all execution configuration.
   Compare certificate fingerprints including alias/whiteout behavior.
   Inspect and explicitly approve differences in image metadata.
2. Run the production version and startup checks, including Node executable
   and `process.versions`, npm, PostgreSQL and architecture checks. Verify
   that the denied roots are absent and that unrelated roots still work.
3. Build CMS, SSR and the reconciler from the candidate inputs and run the
   existing disposable end-to-end, media authorization and search recovery
   tests against those exact resulting image digests. This verifies the new
   artifacts; it does not retroactively verify historical production images.
4. Independently review the hosted workflow, input trust and output
   provenance, then verify an actual hosted run, attestation, digest-preserving
   transfer, target-node pull and guarded rollback before considering use.

Even an equivalent rebuilt base will usually have a different digest. Treat it
as a new reviewed input with its own verified identity, not as an alias for the
existing production pin. The current release candidate still requires five
pre-existing private packages and cannot use this proposal without additional
implementation and review.
