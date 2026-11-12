# The Docker sidecar is rootless and unprivileged, and never falls back

`--with docker` runs Docker's rootless dind image in a container that is not privileged. A nested container is root
only in rootlesskit's user namespace, and the sidecar has none of the Docker VM's devices. A rootful privileged dind
gives a privileged nested container the VM's disks, and with them the host Docker's data: every credential broker's
environment (every repo's real tokens), every repo's config volume, the shared image. That would undo ADR 0009 and
ADR 0014 for any session with the extra, through an API that needs no authentication on the internal network.

Rootless alone is not enough. Docker documents a privileged outer container, which gets every device node of the VM;
on Docker Desktop they are mode 0666 (`/dev/vda` and `/dev/mem` among them), so a nested `--privileged` container
reads the VM's disks. The sidecar gets only what rootlesskit needs instead: seccomp, AppArmor and system paths
unconfined, and a tap device.

Where the sidecar doesn't turn healthy, the launcher refuses `--with docker` and names the host runtime. It never
starts a privileged or rootful sidecar in its place: a weaker sidecar the user didn't choose is the gap this closes.

## Considered Options

- **Rootless in a privileged container**, as Docker documents it: the VM's devices stay in reach (above).
- **Rootful dind, unprivileged nested containers only**: the API is unauthenticated, so nothing stops a session from
  asking for `--privileged`; a policy in front of it would be ours to write and keep.
- **Sysbox, or a VM per repo**: a stronger boundary, but a runtime to install on every host (ADR 0004).

## Consequences

- A nested `--privileged` container doesn't start, so nested dind, k3s and the like don't work.
- Without seccomp, AppArmor and the `/proc` and `/sys` masks, an escape from the sidecar's user namespace meets fewer
  walls than one from `dev`. It still has no capabilities on the VM and no device of it.
- The user namespace is what keeps the checkout's read-only hooks mounts read-only for nested containers: the kernel
  locks the mounts the daemon inherits, so a nested bind or unmount can't expose the writable dir below. A rootful
  sidecar would lose that too.
- A host kernel that restricts unprivileged user namespaces can't start it: GitHub's `ubuntu-latest` sets
  `kernel.apparmor_restrict_unprivileged_userns = 1`, which `shell.yml` turns off; a Linux host with that setting needs
  the same.
- Rootless has no cgroup driver: nested `--memory` and `--pids-limit` are ignored, and the sidecar's own caps are the
  only ones. Nested root is uid 1000, the session's, so it writes the checkout as the session does.
