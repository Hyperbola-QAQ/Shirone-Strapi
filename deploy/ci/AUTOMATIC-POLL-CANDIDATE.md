# Inactive release poller candidate

`automatic-release-poll.py` performs one iteration against a dedicated clean
checkout. It accepts only the expected repository and `main`, fetches with SSH
host-key checking, and refuses dirty or divergent checkouts. After a fast-forward
it exits and waits for the next invocation so that both the poller and controller
are loaded from the new checkout rather than a mixture of revisions. It selects
only successful `main` runs for the exact current commit. An exclusive poll lock,
durable hold marker, and the controller's source/run reservations prevent blind
retries after an uncertain interruption. The operator must reconcile a hold.

The CLI requires `--isolated-test-only`. This candidate is **not ready for
unattended production use**: a complete controller run and recovery on imini,
trusted runtime dependencies, restricted GitHub Actions read access, a
repository-scoped Git push key, a new locally generated age identity, the
Ansible source files and SSH/kubeconfig assumptions must still be provisioned
and independently verified. Never copy the existing controller identity or a
broad personal access token. CMS or base image changes currently fail the
controller's rollback-compatibility gate.

`deploy/ansible/manage-shirone-release-controller.yml` is mirrored at
`/home/hyperbola/Work/ansible-debian-k8s/manage_shirone_release_controller.yml`.
By default it creates nothing. An explicit staging flag and confirmation can
place **disabled** user service/timer unit files on imini; enabling is rejected.
It does not install dependencies, clone the repo, generate or copy credentials,
reload systemd, or enable/start a timer. Existing unit names cause refusal.
The existing imini user has lingering enabled, observed 2026-11-11. The default
and explicit-staging Ansible checks passed against imini; the latter predicted
three changes in check mode and made no writes. A direct invocation from the
Ansible source directory attempted to load unrelated Vault variables; the
checks used the byte-identical repository copy and a single-host inventory.

`python3 deploy/ci/test-automatic-release-poll.py` verifies filtering, holds,
replay refusal, dirty checkout refusal and a real local Git fast-forward. These
tests mock GitHub and the controller; they do not prove unattended deployment,
credential isolation, target-host configuration, or production recovery.
