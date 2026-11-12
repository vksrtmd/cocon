# Sessions run on Docker Compose, sharing the Docker VM

Each session is `docker compose run` in a per-slug compose project, on any Docker engine for macOS (OrbStack,
Colima, Docker Desktop). Compose gives what the isolation rests on: an `internal` network whose only way out is
the egress proxy, opt-in services by profile, health-gated startup and per-project volumes. The boundary is the
container inside the one Linux VM all sessions share; the `--with docker` sidecar is rootless and unprivileged in it
(ADR 0016).

## Considered Options

- **Apple `container`**: a lightweight VM per container, a stronger boundary than the shared VM, but no Compose
  equivalent, so the internal network and sidecars would be rebuilt by hand. Revisit when it supports
  multi-container networking.
- **gVisor or Kata runtimes**: hard to install on OrbStack and Colima.
- **Podman**: Compose-compatible but still one VM on macOS, so no gain.
- **A VM per repo (Lima, Tart)**: the strongest boundary, but slow to start, heavy, and every piece of
  orchestration would be ours.
