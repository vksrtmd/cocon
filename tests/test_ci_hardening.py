"""The CI workflows and the Renovate config keep the hardening docs/threat-model.md (CI and supply
chain) promises. Each *_problems function returns one line per break, naming the file and the step
or rule; the tests run it on the real files (none) and on a copy with the unsafe setting (one).
Text only: the standard library has no YAML parser, so the workflows are read as lines."""
import json
import re
import unittest

from harness import ROOT

WORKFLOWS = ("cocon.yml", "shell.yml")
RENOVATE_AGE = {"npm": "7 days", "github-releases": "7 days", "github-release-attachments": "7 days",
                "docker": "7 days"}


def workflow(name):
    return (ROOT / ".github/workflows" / name).read_text()


def steps(text):
    """(line number, step text) for each `- ` list item under a `steps:` key."""
    out = []
    for m in re.finditer(r"^( +)steps:\n((?:\1 +.*\n|\s*\n)*)", text, re.M):
        base = text.count("\n", 0, m.start(2)) + 1
        for s in re.finditer(r"^ +- .*\n(?:(?! +- ).*\n)*", m.group(2), re.M):
            out.append((base + m.group(2).count("\n", 0, s.start()), s.group()))
    return out


def permission_problems(name, text):
    m = re.search(r"^permissions:(.*(?:\n[ ]+\S.*)*)", text, re.M)
    if not m:
        return [f"{name}: no top-level `permissions:` block, the token gets the repo's default"]
    body = m.group(1)
    if re.fullmatch(r"\s*\{\s*contents:\s*read\s*\}\s*(#.*)?", body) or re.fullmatch(
        r"\s*(#.*)?\n\s+contents:\s*read\s*(#.*)?", body
    ):
        return []
    return [f"{name}: top-level `permissions:{body.strip()}` grants more than `contents: read`"]


def checkout_problems(name, text):
    return [
        f"{name}:{n}: actions/checkout step doesn't set `persist-credentials: false`"
        for n, s in steps(text)
        if "uses: actions/checkout@" in s and not re.search(r"persist-credentials:\s*false\b", s)
    ]


def pin_problems(name, text):
    out = [f"{name}: `runs-on: {m}` floats with `-latest`" for m in re.findall(r"^\s*runs-on:\s*(\S*-latest)\s*$", text, re.M)]
    if "shellcheck" not in text:
        return out
    if not re.search(r"SHELLCHECK_VERSION:\s*v\d+\.\d+\.\d+\s*$", text, re.M):
        out.append(f"{name}: the shellcheck step has no `SHELLCHECK_VERSION: vX.Y.Z` pin")
    if not re.search(r"SHELLCHECK_SHA256:\s*[0-9a-f]{64}\s*$", text, re.M):
        out.append(f"{name}: the shellcheck step has no 64-hex `SHELLCHECK_SHA256`")
    if not re.search(r'echo "\$SHELLCHECK_SHA256 .*(shasum -a 256|sha256sum) -c', text):
        out.append(f"{name}: the shellcheck step never checks the download against `$SHELLCHECK_SHA256`")
    return out


def renovate_problems(cfg):
    rules = cfg.get("packageRules", [])
    out = []
    for ds, age in RENOVATE_AGE.items():
        if not any(ds in r.get("matchDatasources", []) and r.get("minimumReleaseAge") == age for r in rules):
            out.append(f"renovate.json: no packageRules entry gives matchDatasources `{ds}` minimumReleaseAge `{age}`")
    for i, r in enumerate(rules):
        if r.get("groupName") == "digests":
            for pkg in ("mitmproxy/mitmproxy", "ubuntu/squid"):
                if f"!{pkg}" not in r.get("matchPackageNames", []):
                    out.append(f"renovate.json: packageRules[{i}] (groupName digests) doesn't exclude `{pkg}`")
    return out


class CiHardening(unittest.TestCase):
    def test_workflows_grant_only_contents_read(self):
        for name in WORKFLOWS:
            self.assertEqual(permission_problems(name, workflow(name)), [])
        text = workflow("shell.yml")
        for bad in ("", "permissions: { contents: write }\n", "permissions:\n  contents: read\n  pull-requests: write\n"):
            fixture = re.sub(r"^permissions:.*\n", bad, text, flags=re.M)
            self.assertEqual(len(permission_problems("shell.yml", fixture)), 1, bad)

    def test_every_checkout_drops_the_token(self):
        for name in WORKFLOWS:
            self.assertEqual(checkout_problems(name, workflow(name)), [])
        self.assertTrue(any(workflow(n).count("actions/checkout@") for n in WORKFLOWS))
        for name in WORKFLOWS:
            fixture = re.sub(r",? ?persist-credentials: false", "", workflow(name))
            self.assertEqual(len(checkout_problems(name, fixture)), workflow(name).count("actions/checkout@"))

    def test_runners_and_shellcheck_are_pinned(self):
        for name in WORKFLOWS:
            self.assertEqual(pin_problems(name, workflow(name)), [])
        text = workflow("shell.yml")
        self.assertIn("shellcheck", text)
        for old, new in (
            ("runs-on: macos-26", "runs-on: macos-latest"),
            ("SHELLCHECK_VERSION: v0.11.0", "SHELLCHECK_VERSION: latest"),
            ("SHELLCHECK_SHA256: 339b", "SHELLCHECK_SHA256: xyz"),
            ('echo "$SHELLCHECK_SHA256 ', 'echo "skipped '),
        ):
            self.assertIn(old, text)
            self.assertEqual(len(pin_problems("shell.yml", text.replace(old, new))), 1, new)

    def test_renovate_waits_7_days_and_isolates_the_broker_and_proxy_images(self):
        cfg = json.loads((ROOT / "renovate.json").read_text())
        self.assertEqual(renovate_problems(cfg), [])
        age = json.loads(json.dumps(cfg))
        age["packageRules"][0]["minimumReleaseAge"] = "1 day"
        self.assertEqual(len(renovate_problems(age)), 4)
        grouped = json.loads(json.dumps(cfg))
        rule = next(r for r in grouped["packageRules"] if r.get("groupName") == "digests")
        rule["matchPackageNames"] = ["!ubuntu/squid"]
        self.assertEqual(len(renovate_problems(grouped)), 1)


if __name__ == "__main__":
    unittest.main()
