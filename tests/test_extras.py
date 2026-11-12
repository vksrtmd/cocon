"""Every place that lists the extras names the same ones: the launcher's known list, the
Dockerfile's build args and stages, compose's build args, and CI's all-extras build. Each
compose profile but `ports` is an extra. Text only, no Docker needed."""
import re
import unittest

from harness import KIT, ROOT


def read(path):
    """path: repo-relative; cocon/... reads the kit under test."""
    return (KIT / path[6:] if path.startswith("cocon/") else ROOT / path).read_text()


def norm(words):
    return sorted({w.lower() for w in words if w})


class Extras(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        m = re.search(r"^KNOWN = \(([^)]*)\)", (KIT / "py/kit/launcher/options.py").read_text(), re.M)
        assert m, "no KNOWN = (...) line in cocon/py/kit/launcher/options.py"
        cls.known = norm(re.findall(r'"([^"]+)"', m.group(1)))

    def test_dockerfile_args_before_first_from(self):
        head = re.split(r"^FROM ", (KIT / "Dockerfile").read_text(), maxsplit=1, flags=re.M)[0]
        self.assertEqual(norm(re.findall(r"WITH_([A-Z0-9_]*)", head)), self.known)

    def test_dockerfile_extra_stages(self):
        stages = re.findall(r"^FROM .* AS ([a-z0-9]*)-1$", (KIT / "Dockerfile").read_text(), re.M)
        self.assertEqual(norm(stages), self.known)

    def test_compose_dev_build_args(self):
        args = re.findall(r"^ *- WITH_([A-Z0-9_]*)$", (KIT / "compose.yml").read_text(), re.M)
        self.assertEqual(norm(args), self.known)

    def test_ci_all_extras_build(self):
        args = re.findall(r"WITH_([A-Z0-9_]*)=1", read(".github/workflows/cocon.yml"))
        self.assertEqual(norm(args), self.known)

    def test_compose_profiles_are_extras(self):
        for p in re.findall(r"^ *profiles: \[([a-z0-9]*)\]$", (KIT / "compose.yml").read_text(), re.M):
            self.assertTrue(p == "ports" or p in self.known, f"compose profile {p} isn't an extra")


if __name__ == "__main__":
    unittest.main()
