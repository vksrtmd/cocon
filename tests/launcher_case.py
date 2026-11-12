"""Base for the launcher suites (test_launcher_*.py): launch() runs cocon against the stubs."""
import json
import re
import shlex
from pathlib import Path

from harness import KIT, KitCase

COMPOSE = KIT / "compose.yml"


def shq(s):
    return shlex.quote(str(s))


class LauncherCase(KitCase):
    def launch(self, *args, stub="true", rc=0, answer="", env=None, **kw):
        r = self.run_kit("cocon", *args, input=answer + "\n",
                         env={"STUB": stub, "STUB_RC": str(rc), **(env or {})}, **kw)
        r.text = r.out + r.err
        return r

    def assertLaunch(self, r, rc, *texts):
        self.assertEqual(r.rc, rc, r.text)
        for t in texts:
            self.assertIn(t, r.text)

    def session(self, r):
        runs = [c for c in r.calls if "compose" in c.args and "run" in c.args and not {"ports", "broker"} & set(c.args)]
        return runs[-1].args if runs else None

    def sidecar_mounts(self, r):
        args = self.session(r) or []
        overrides = [args[i + 1] for i, a in enumerate(args) if a == "-f"][1:]
        if not overrides:
            return None
        (f,) = overrides
        return json.loads(Path(f).read_text())["services"]["docker"]["volumes"]

    def assertMount(self, r, m):
        self.assertIn(m, self.session(r) or [], f"no mount {m}")

    def assertEnv(self, r, e):
        """e (NAME=value) is passed to the session as `-e e`."""
        args = self.session(r) or []
        self.assertIn(e, [args[i + 1] for i, a in enumerate(args[:-1]) if a == "-e"], f"no env {e}")

    def assertNoMount(self, r, m):
        self.assertNotIn(m, self.session(r) or [])

    def lines(self, r):
        return [c.line for c in r.calls]

    def assertCall(self, r, line):
        self.assertIn(line, self.lines(r), f"no call: {line}")

    def assertNoCall(self, r, text):
        # Substring match, but a text ending in a digit (a pid) doesn't match a longer number: -222 isn't -222212.
        pat = re.escape(text) + (r"(?!\d)" if text[-1:].isdigit() else "")
        self.assertFalse([l for l in self.lines(r) if re.search(pat, l)], f"unexpected call matching: {text}")

    def assertCallRe(self, r, pattern):
        self.assertTrue([l for l in self.lines(r) if re.search(pattern, l)], f"no call matching {pattern}: {self.lines(r)}")

    def project(self, r):
        (p,) = {c.args[c.args.index("-p") + 1] for c in r.calls if "-p" in c.args}
        return p.removeprefix("cocon-")

    def assertProject(self, r, slug):
        self.assertTrue([l for l in self.lines(r) if f" -p cocon-{slug} " in l], f"no compose project cocon-{slug}")
