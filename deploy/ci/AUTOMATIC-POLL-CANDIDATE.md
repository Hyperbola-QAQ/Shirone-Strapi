# Inactive release poller candidate

`automatic-release-poll.py` performs one iteration against a dedicated clean
checkout. It accepts only the expected repository and `main`, fetches with SSH
host-key checking, and refuses dirty or divergent checkouts. After a fast-forward
it exits and waits for the next invocation so that both the poller and controller
are loaded from the new checkout rather than a mixture of revisions. It selects
only successful `main` runs for the exact current commit. An exclusive poll lock,
durable hold marker, and the controller's source/run reservations prevent blind
retries after an uncertain interruption. The operator must reconcile a hold.
The specific bounded download-failure path is eligible for two later polls only
when no transaction reservation or receipt exists and the checkout is unchanged;
on the third failure the hold remains. Any ambiguous failure remains held.

The CLI requires `--isolated-test-only`. This candidate is **not ready for
unattended production use**: a complete controller run and recovery on imini,
trusted runtime dependencies, restricted GitHub Actions read access, a
repository-scoped Git push key, a new locally generated age identity, the
Ansible source files and SSH/kubeconfig assumptions must still be provisioned
and independently verified. Never copy the existing controller identity or a
broad personal access token. CMS or base image changes currently fail the
controller's rollback-compatibility gate.

`deploy/ansible/manage-shirone-release-controller.yml` has a proposed source
counterpart at `/home/hyperbola/Work/ansible-debian-k8s/manage_shirone_release_controller.yml`.
The two currently differ: the repository copy adds state-directory safety
checks. Do not use either for staging until the difference is reviewed and the
authoritative source is synchronized.
By default it creates nothing. An explicit staging flag and confirmation can
place **disabled** user service/timer unit files on imini; enabling is rejected.
It does not install dependencies, clone the repo, generate or copy credentials,
reload systemd, or enable/start a timer. Existing unit names cause refusal.
The existing imini user has lingering enabled, observed 2026-11-11. The default
and explicit-staging Ansible checks passed against imini; the latter predicted
three changes in check mode and made no writes. A direct invocation from the
Ansible source directory attempted to load unrelated Vault variables; the
checks used the byte-identical repository copy and a single-host inventory.

The optional `SHIRONE_CONTROLLER_LOCAL=1` mode avoids imini SSH to itself: it
requires the imini hostname, `hyperbola` account and reviewed management address,
then uses the existing local kubeconfig and Ansible local connection. Default
remote operation keeps strict SSH host-key checking. A disposable copy on imini
successfully read the live CMS deployment UID through local kubectl; no write
was attempted.

On 2026-11-11 temporary user-owned extraction of signed Debian packages from
the configured Tsinghua mirror ran Ansible Core 2.19.11 and a read-only local
kubectl command, and separately ran skopeo 1.18.0. A pinned GitHub CLI 2.102.1
archive passed its official SHA-256, ran, and exposed the required attestation
flag. The matching pinned Arch mirror package returned 404, so that test used
the official GitHub release. All extracted files were removed. These tests are
not a durable installation or a full attestation/registry/deployment test.

`python3 deploy/ci/test-automatic-release-poll.py` verifies filtering, holds,
bounded download failure retries, replay refusal, dirty checkout refusal and a
real local Git fast-forward. The controller and adapter tests exercise explicit
local-target host/address checks. Tests mock GitHub and production changes;
they do not prove unattended deployment, credential isolation, target-host
configuration, or production recovery.

## Runtime preparation candidate

`install-controller-tools.py` prepares a new owner-only user directory, using
eleven version-and-SHA256-pinned Debian packages and pinned GitHub CLI/age
archives. Configured APT sources and the domestic mirror are tried first; an
official same-version source is used only after mirror failure and checksum
validation. Installation refuses to replace an existing directory, validates
tool versions and required attestation flags, and does not touch credentials or
system packages. On 2026-11-12 a disposable local Debian 13 container completed
the installer with real checksum-pinned Debian packages and loaded Ansible
2.19.11, skopeo 1.18.0, GitHub CLI 2.102.1 and age 1.3.2. The two public CLI
archives were fetched and checksum-verified on the controller, then injected
into the container because its direct GitHub connection failed; the installer
verified their checksums again. The pinned GitHub CLI mirror file was absent,
so the same-version official archive was used. This did not validate a private
registry pull or a complete installation on imini.

The Ansible candidate has separate explicit preparation switches for tools,
minimal source and a **fresh** target-side identity. The source preparation
checks the authoritative inventory locally under `no_log`, verifies the two
reviewed image playbooks, and writes a sanitized target inventory with only the
required host address and user. It never copies Vault files or credentials.
Because inventory credentials were exposed during a prior audit, **do not run
source preparation or connect to imini using them until the user has rotated
and securely reconfigured them**. The identity generator makes a new age key
and deploy key under a private directory; it does not authorize either key,
configure Actions credentials, or enable the service. Both scripts have only
offline synthetic tests; no identities or target files have been created.
