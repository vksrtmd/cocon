# Launcher map

Where the launcher, `cocon/py/kit/launcher/`, decides what. Each anchor is a string that occurs in the file
named beside it; search for it there. Rename an anchor here with the code. A new row has the file in the second
column and the anchor, in backticks, in the third.

`cocon/cocon` is only the shim that runs it with `python3 -I -B` (ADR 0013); `cocon/py/kit/guard/` is
host-guard, which the launcher calls in-process for every check and every host git command.

| What | File | Anchor |
| --- | --- | --- |
| The usage text, and the order of a launch | `cli.py` | `def launch(argv):` |
| Option parsing, the `KNOWN` extras | `options.py` | `def parse(argv):` |
| Image for the extras: its tag and compose's variables for it | `options.py` | `def image_env(withs):` |
| Slug, repo ID | `project.py` | `def name_it(self):` |
| The pin file | `project.py` | `def check_pin(self):` |
| The `compose` project: its command line and the variables compose.yml reads (`plus` adds to them) | `project.py` | `class Compose:` |
| `--worktree` creation | `worktree.py` | `def create(p, repo, name, wt):` |
| The audit log's path | `project.py` | `self.log_dir =` |
| The audit log's lines and rotation | `audit.py` | `def session_line(p, text):` |
| Credential files (`read_env`), for the launch and `--doctor` | `credentials.py` | `def read_env(path):` |
| The GitHub token's type and reach, for the launch and `--doctor` | `credentials.py` | `def token_check(token, repo_id):` |
| The session's env (`envs`) | `session.py` | `def credentials(self):` |
| Mount decision: the checkout's mounts | `mounts.py` | `def checkout_mount(self, spec):` |
| Mount decision: hooks dirs read-only | `mounts.py` | `def git_dirs(self):` |
| Mount decision: read-only checkout (`ro_checkout`) | `mounts.py` | `self.ro_checkout = self.p.dir ==` |
| Config mounts from `~/.claude` | `mounts.py` | `def config(self):` |
| The sidecar's override (`-f` file with the mounts) | `mounts.py` | `def sidecar_override(self):` |
| The sidecar's health wait | `session.py` | `"up", "-d", "--wait", "docker"` |
| `host_runtime` (the host's Docker by name) | `session.py` | `def host_runtime():` |
| The session's credential broker | `session.py` | `def start_broker(self):` |
| `--port` forwarder | `session.py` | `def start_ports(self):` |
| The session itself | `session.py` | `def run_dev(self, args):` |
| Exit handling and warnings | `session.py` | `def end(self, m, rc):` |
| `--doctor` | `doctor.py` | `class Doctor:` |
| `--down` | `services.py` | `def down(p):` |
| Names of a checkout's brokers, forwarders, CA volumes and sidecar; what gone launchers left | `services.py` | `def stale(kind, slug, ours):` |
| Detached sessions (tmux) | `tmux.py` | `def detach(p):` |
