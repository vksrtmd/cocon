# Security policy

cocon is an isolation tool, so a way out of it is a vulnerability: a session that reaches host files,
credentials or the network beyond its allowlist, plants code the host runs without a report, or reaches another
repo's cocoon. [docs/threat-model.md](docs/threat-model.md) lists what is promised and the gaps already known;
a known gap is not a vulnerability, but a cheaper way through one is.

## Reporting

Report privately through GitHub: **Security > Report a vulnerability** on this repo. Don't open a public issue.

Include the version (commit), the host Docker runtime (OrbStack, Colima or Docker Desktop) and the steps to
reproduce. Expect an answer within a week.

## Supported versions

Only `main`. Fixes land there; there are no release branches.
