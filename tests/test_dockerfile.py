"""The image's final stage strips setuid and setgid bits as its last root step, after every extra,
so no later install brings one back; the build itself fails if one survives. Everything the build
fetches is checked against a pin in the Dockerfile (download_problems has the rules), and only the
npm packages allowScripts names run install scripts (install_script_problems). Text only, no Docker
needed."""
import json
import posixpath
import re
import unittest

from harness import KIT, ROOT


class SetuidStrip(unittest.TestCase):
    def test_final_stage_strips_and_asserts_last(self):
        text = (KIT / "Dockerfile").read_text().replace("\\\n", " ")
        final = re.split(r"^FROM ", text, flags=re.M)[-1]
        steps = re.findall(r"^(RUN|COPY|ADD|USER)\b(.*)", final, re.M)
        strip = [i for i, (_, body) in enumerate(steps) if "-perm /6000 -exec chmod a-s" in body]
        self.assertEqual(len(strip), 1, "the final stage has no setuid/setgid strip")
        i = strip[0]
        self.assertIn("exit 1", steps[i][1], "the strip doesn't fail the build when a bit survives")
        self.assertEqual(steps[i + 1:], [("USER", " claude")], "steps after the strip can add bits")


def instructions(text):
    """(line, keyword, rest) per instruction; a continuation's backslash becomes a space, keeping the
    newline, so `rest` tells which line a match is on. A heredoc's body (`RUN <<EOF`) joins `rest` by
    its newlines."""
    out, cur, start, ends = [], None, 0, []
    for n, raw in enumerate(text.splitlines(), 1):
        if ends:
            cur += "\n" + raw
            if (raw.lstrip("\t") if ends[0][0] else raw) == ends[0][1]:
                ends.pop(0)
        else:
            if cur is None:
                if not raw.strip() or raw.lstrip().startswith("#"):
                    continue
                cur, start = "", n
            cont = raw.endswith("\\")
            cur += raw[:-1] + " \n" if cont else raw
            if cont:
                continue
            ends = re.findall(r"""(?:^|\s)<<(?!<)(-?)\s*["']?(\w+)["']?""", cur)
        if not ends:
            kw, _, rest = cur.strip().partition(" ")
            out.append((start, kw.upper(), rest))
            cur = None
    return out


def segments(cmd):
    """The shell command split at top-level &&, || and ; (quotes respected): (offset, piece, the separator
    after it, "" for the last)."""
    out, quote, begin, i = [], None, 0, 0
    while i < len(cmd):
        c = cmd[i]
        if quote:
            if c == "\\" and quote == '"':
                i += 1
            elif c == quote:
                quote = None
        elif c in "'\"":
            quote = c
        elif cmd.startswith(("&&", "||"), i) or c == ";":
            sep = c if c == ";" else cmd[i:i + 2]
            out.append((begin, cmd[begin:i], sep))
            i += len(sep)
            begin = i
            continue
        i += 1
    out.append((begin, cmd[begin:], ""))
    return out


DOWNLOAD = re.compile(r"(?<![\w.-])(curl|wget)(?![\w.-])([^|]*)(\|?)")
OUTPUT = re.compile(r"(?:\s-[oO]\s*|\s--output(?:-document)?[=\s]|>\s*)(\S+)")
PINNED = re.compile(r"[0-9a-fA-F]{40,128}(?: [0-9a-fA-F]{40,128})*")
CHECKER = re.compile(r"\bsha(?:256|384|512)sum (?:-c|--check)\b|(?<![\w./-])gpg\s")
APT_INSTALL = re.compile(r"\s*apt(?:-get)?\s+install(?:\s+[\w.+:=~-]+)*\s*")
REMOTE = re.compile(r"\w[\w+.-]*://|[^/\s]*@[^/\s]*:")
RUSTUP_ADD = re.compile(r"(?<![\w.-])rustup\s+(?:component|target)\s+add\b")
MANIFEST_CMP = re.compile(r"(?<![\w.-])cmp\s[^\n]*multirust-channel-manifest\.toml")


def digest_pinned(ref, stages):
    """An image ref with @sha256:<digest>, or a local stage whose FROM chain ends in one. A stage named
    through a build arg (`extra-${WITH_EXTRA}`) is pinned when every stage it can name is."""
    if re.search(r"@sha256:[0-9a-f]{64}$", ref) or ref == "scratch":
        return True
    if "$" in ref:
        names = re.compile(re.sub(r"\\\$\\\{?\w+\\\}?", ".*", re.escape(ref.lower())))
        matches = [s for s in stages if names.fullmatch(s)]
        return bool(matches) and all(digest_pinned(stages[s], stages) for s in matches)
    return ref.lower() in stages and digest_pinned(stages[ref.lower()], stages)


def download_problems(text):
    """Each network fetch in a Dockerfile that isn't checked against a pinned value, as `line N: why`:
    - curl or wget anywhere in a RUN, a heredoc's body too (only an apt install's package list is
      exempt), must save to a file (not a pipe) that a later step of the same RUN checks with
      sha256sum -c (or sha384/sha512) or the gpg command, referencing an ARG of this stage whose
      value is hex digests or fingerprints; the download, the check and the steps between are joined
      by && and the check isn't followed by || or ;, which would let the build go on past a failure;
    - ADD of a non-local source (a URL, ssh://, git@host:), shell or JSON form, needs
      --checksum=sha256:<digest>;
    - COPY --from and RUN --mount from= need a digest-pinned image, directly or through a local stage;
    - rustup component add or target add checks each download against the toolchain's installed
      multirust-channel-manifest.toml, which the digest-pinned image carries, so a later step joined by
      && must cmp it against a copy taken before: a rustup that fetched a fresh manifest fails the build.
    Other fetchers (apt, go, npm) are out of scope: their own checks or lockfiles cover them."""
    problems, pinned, stages = [], set(), {}
    for line, kw, rest in instructions(text):
        if kw == "FROM":
            pinned = set()
            words = [w for w in rest.split() if not w.startswith("--")]
            if len(words) == 3 and words[1].upper() == "AS":
                stages[words[2].lower()] = words[0]
        elif kw == "ARG":
            pinned |= {name for name, value in re.findall(r"""(\w+)=("[^"]*"|'[^']*'|\S+)""", rest)
                       if PINNED.fullmatch(value.strip("\"'"))}
        elif kw == "ADD":
            srcs = re.findall(r'[^\s\[\]",]+', rest)
            if any(REMOTE.match(w) for w in srcs) and not re.search(r"--checksum=sha256:[0-9a-f]{64}\b", rest):
                problems.append(f"line {line}: ADD of a URL has no --checksum=sha256:<digest>")
        if kw in ("COPY", "RUN"):
            for src in re.findall(r"--from=(\S+)" if kw == "COPY" else r"--mount=\S*?\bfrom=([^,\s]+)", rest):
                if not digest_pinned(src, stages):
                    what = "COPY --from=" if kw == "COPY" else "RUN --mount from="
                    problems.append(f"line {line}: {what}{src} isn't digest-pinned")
        if kw == "RUN":
            segs = segments(rest)
            for k, (off, seg, _) in enumerate(segs):
                if APT_INSTALL.fullmatch(seg):
                    continue
                for m in RUSTUP_ADD.finditer(seg):
                    at = line + rest[:off + m.start()].count("\n")
                    if not any(MANIFEST_CMP.search(later) and all(sep in ("&&", "") for _, _, sep in segs[k:j + 1])
                               for j, (_, later, _) in enumerate(segs) if j > k):
                        problems.append(f"line {at}: rustup's add isn't followed by a cmp of the image's "
                                        "multirust-channel-manifest.toml")
                for m in DOWNLOAD.finditer(seg):
                    at = line + rest[:off + m.start(1)].count("\n")
                    out = OUTPUT.search(m.group(2))
                    file = out and out.group(1).strip("\"'")
                    checked = file and file != "-" and not m.group(3) and any(
                        file in later and CHECKER.search(later)
                        and any(re.search(r"\$\{?%s\b" % a, later) for a in pinned)
                        and all(sep in ("&&", "") for _, _, sep in segs[k:j + 1])
                        for j, (_, later, _) in enumerate(segs) if j > k)
                    if not checked:
                        problems.append(f"line {at}: {m.group(1)}'s download isn't checked against a pinned value")
    return problems


def install_script_problems(lock, manifest, dockerfile):
    """Where the lockfile's packages with an install script, package.json's allowScripts and the
    packages the Dockerfile's `npm rebuild` runs scripts for disagree: each scripted package must be
    allowed, and the allowed ones are exactly the rebuilt ones."""
    scripted = {key.rpartition("node_modules/")[2] for key, pkg in lock["packages"].items()
                if pkg.get("hasInstallScript")}
    allowed = set(manifest.get("allowScripts", {}))
    m = re.search(r"\bnpm rebuild\b([^&|;\n]*)", dockerfile.replace("\\\n", " "))
    rebuilt = {w for w in m.group(1).split() if not w.startswith("-")} if m else set()
    return ([f"{p} has an install script but isn't in package.json's allowScripts" for p in sorted(scripted - allowed)]
            + [f"{p} is in allowScripts but not in the Dockerfile's npm rebuild" for p in sorted(allowed - rebuilt)]
            + [f"{p} is in the Dockerfile's npm rebuild but not in allowScripts" for p in sorted(rebuilt - allowed)])


def filter_problems(dockerfile, workflow):
    """Each local COPY or ADD source the Dockerfile reads from the build context (cocon/) that none of
    the paths on the workflow's `dockerfile=1` line (the diff that triggers the extras build) covers, as
    `line N: <the source's top path under cocon/> isn't under a dockerfile=1 path`. A path covers itself
    and what's under it. The `base` and `final` stages are exempt: the `cocon=1` base build
    covers their files and the extras don't depend on them."""
    m = re.search(r'git diff --quiet\s+"[^"]*"\s+--\s+([^|\n]*?)\s*\|\|\s*echo dockerfile=1', workflow)
    if not m:
        return ["the workflow has no `git diff ... -- <paths> || echo dockerfile=1` line"]
    covering = [posixpath.normpath(w) for w in m.group(1).split()]
    problems, stage = [], ""
    for line, kw, rest in instructions(dockerfile):
        if kw == "FROM":
            stage = rest.lower().split()[-1] if " as " in rest.lower() else ""
        if kw not in ("COPY", "ADD") or "--from=" in rest or stage in ("base", "final"):
            continue
        words = re.findall(r'[^\s\[\]",]+', rest) if rest.startswith("[") else rest.split()
        for src in [w for w in words[:-1] if not w.startswith("--") and not REMOTE.match(w)]:
            path = posixpath.normpath("cocon/" + src)
            if not any(path == c or path.startswith(c + "/") for c in covering):
                top = "/".join(path.split("/")[:2])
                if f"line {line}: {top} isn't under a dockerfile=1 path" not in problems:
                    problems.append(f"line {line}: {top} isn't under a dockerfile=1 path")
    return problems


DECLARES = re.compile(r"(?:^|[{(])\s*(?:(?:then|do|else)\s+)*(?:export|readonly|local|declare|typeset)\b[^\n]*(?:\$\(|`)")


def substitution_problems(text):
    """Each export, readonly, local, declare or typeset in a RUN whose value is a command substitution, as
    `line N: why`: the builtin's own status replaces the substitution's, so a failure doesn't stop
    the build (CODING_STANDARDS.md, Shell). Assign first, then declare: `A="$(cmd)" && export A`."""
    problems = []
    for line, kw, rest in instructions(text):
        if kw != "RUN":
            continue
        for off, seg, _ in segments(rest):
            # A heredoc's body is a script, one command per line; a plain RUN's newlines are continuations.
            for part in (re.finditer(r"[^\n]+", seg) if "<<" in rest else [re.match(r"[\s\S]*", seg)]):
                if DECLARES.search(part.group()):
                    at = line + rest[:off + part.start()].count("\n")
                    problems.append(f"line {at}: export of a command substitution hides its failure from set -e "
                                    "(CODING_STANDARDS.md, Shell): assign it first, then export")
    return problems


PINNED_FROM = "FROM node:24@sha256:" + "a" * 64 + "\n"


class Downloads(unittest.TestCase):
    def test_image_build_checks_every_download(self):
        self.assertEqual(download_problems((KIT / "Dockerfile").read_text()), [])

    def test_unverified_curl_is_reported_with_its_line(self):
        text = (PINNED_FROM + "RUN apt-get install -y curl \\\n"
                " && curl -fsSL https://example.com/tool -o /usr/local/bin/tool\n")
        self.assertEqual(download_problems(text), ["line 3: curl's download isn't checked against a pinned value"])

    def test_download_checked_against_a_pinned_sha256_passes(self):
        text = (PINNED_FROM + "ARG TOOL_SHA256=" + "0" * 64 + "\n"
                "RUN curl -fsSL https://example.com/tool.tgz -o /tmp/tool.tgz \\\n"
                ' && echo "$TOOL_SHA256  /tmp/tool.tgz" | sha256sum -c - \\\n'
                " && tar -xzf /tmp/tool.tgz -C /usr/local\n")
        self.assertEqual(download_problems(text), [])

    def test_checksum_fetched_from_the_same_server_is_reported(self):
        text = (PINNED_FROM +
                "RUN curl -fsSL https://example.com/tool -o /tmp/tool \\\n"
                ' && echo "$(curl -fsSL https://example.com/tool.sha256 | cut -d" " -f1)  /tmp/tool" | sha256sum -c -\n')
        self.assertEqual(download_problems(text), [
            "line 2: curl's download isn't checked against a pinned value",
            "line 3: curl's download isn't checked against a pinned value"])

    def test_download_piped_into_a_command_is_reported(self):
        text = (PINNED_FROM + "ARG UV_SHA256=" + "0" * 64 + "\n"
                'RUN curl -fsSL "https://example.com/uv.tar.gz" | tar -xz -C /usr/local/bin\n')
        self.assertEqual(download_problems(text), ["line 3: curl's download isn't checked against a pinned value"])

    def test_unverified_wget_is_reported(self):
        text = PINNED_FROM + "RUN wget -q -O /tmp/x.deb https://example.com/x.deb && dpkg -i /tmp/x.deb\n"
        self.assertEqual(download_problems(text), ["line 2: wget's download isn't checked against a pinned value"])

    def test_keyring_checked_against_pinned_fingerprints_passes(self):
        text = (PINNED_FROM + "ARG KEY_FPRS=\"" + "A" * 40 + " " + "B" * 40 + "\"\n"
                "RUN curl -fsSL https://example.com/key.gpg -o /tmp/key.gpg \\\n"
                " && gpg --show-keys --with-colons /tmp/key.gpg | awk -F: -v pinned=\" $KEY_FPRS \" 'x; y' \\\n"
                " && mv /tmp/key.gpg /usr/share/keyrings/\n")
        self.assertEqual(download_problems(text), [])

    def test_remote_add_without_a_checksum_is_reported(self):
        text = (PINNED_FROM + "ADD https://example.com/a.tgz /tmp/\n"
                "ADD --checksum=sha256:" + "0" * 64 + " https://example.com/b.tgz /tmp/\n"
                "ADD local.tgz /tmp/\n")
        self.assertEqual(download_problems(text), ["line 2: ADD of a URL has no --checksum=sha256:<digest>"])

    def test_copy_from_an_image_without_a_digest_is_reported(self):
        text = (PINNED_FROM + "COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /usr/local/bin/\n"
                "COPY --from=astral/uv:0.13.6@sha256:" + "0" * 64 + " /uv /usr/local/bin/\n")
        self.assertEqual(download_problems(text), ["line 2: COPY --from=ghcr.io/astral-sh/uv:0.13.6 isn't digest-pinned"])

    def test_copy_from_a_stage_resolves_through_its_from_chain(self):
        text = ("FROM rust:1.99 AS rust-src\n"
                "FROM node:24@sha256:" + "a" * 64 + " AS base\n"
                "FROM base AS npm-tools\n"
                "FROM base AS extra-0\n"
                "FROM base AS extra-1\n"
                "FROM extra-${WITH_EXTRA} AS extra\n"
                "FROM extra\n"
                "COPY --from=npm-tools /opt/npm /opt/npm\n"
                "COPY --from=extra /x /x\n"
                "COPY --from=rust-src /usr/local/cargo /usr/local/cargo\n"
                "RUN --mount=type=bind,from=rust-src,target=/r cp -r /r/usr/local/rustup /usr/local/\n")
        self.assertEqual(download_problems(text), ["line 10: COPY --from=rust-src isn't digest-pinned",
                                                   "line 11: RUN --mount from=rust-src isn't digest-pinned"])

    def test_download_anywhere_in_a_run_is_reported(self):
        cases = {
            "bash -c": "RUN bash -c 'curl -fsSL https://example.com/i.sh | sh'\n",
            "if !": "RUN if ! curl -fsSL https://example.com/x -o /tmp/x; then exit 1; fi\n",
            "then": "RUN if [ ! -e /x ]; then wget -q -O /x https://example.com/x; fi\n",
            "path": "RUN /usr/bin/curl -fsSL https://example.com/x -o /usr/local/bin/x\n",
            "env": "RUN env A=1 curl -fsSL https://example.com/x -o /usr/local/bin/x\n",
            "xargs": "RUN echo https://example.com/x | xargs curl -fsSLO\n",
            "heredoc": "RUN <<EOF\nset -e\ncurl -fsSL https://example.com/x -o /usr/local/bin/x\nEOF\n",
        }
        for name, run in cases.items():
            with self.subTest(name):
                tool = "wget" if "wget" in run else "curl"
                problems = [p.partition(": ")[2] for p in download_problems(PINNED_FROM + run + "RUN true\n")]
                self.assertEqual(problems, [f"{tool}'s download isn't checked against a pinned value"])

    def test_heredoc_body_reports_its_own_line(self):
        text = PINNED_FROM + "RUN <<EOF\nset -e\ncurl -fsSL https://example.com/x -o /usr/local/bin/x\nEOF\n"
        self.assertEqual(download_problems(text), ["line 4: curl's download isn't checked against a pinned value"])

    def test_check_whose_failure_is_swallowed_is_reported(self):
        text = (PINNED_FROM + "ARG TOOL_SHA256=" + "0" * 64 + "\n"
                "RUN curl -fsSL https://example.com/tool.tgz -o /tmp/tool.tgz \\\n"
                ' && echo "$TOOL_SHA256  /tmp/tool.tgz" | sha256sum -c - || true \\\n'
                " && tar -xzf /tmp/tool.tgz -C /usr/local\n")
        self.assertEqual(download_problems(text), ["line 3: curl's download isn't checked against a pinned value"])

    def test_gpg_filename_is_not_a_gpg_check(self):
        text = (PINNED_FROM + "ARG KEY_FPRS=\"" + "A" * 40 + "\"\n"
                "RUN curl -fsSL https://example.com/key.gpg -o /tmp/key.gpg \\\n"
                ' && echo "trusting $KEY_FPRS" | cat - /tmp/key.gpg > /usr/share/keyrings/key.gpg\n')
        self.assertEqual(download_problems(text), ["line 3: curl's download isn't checked against a pinned value"])

    def test_remote_add_in_any_form_without_a_checksum_is_reported(self):
        text = (PINNED_FROM + 'ADD ["https://example.com/a.tgz", "/tmp/"]\n'
                "ADD ssh://git@example.com/o/r.git /src\n"
                "ADD git@github.com:o/r.git /src\n"
                'ADD ["local dir/a.tgz", "/tmp/"]\n')
        self.assertEqual(download_problems(text), [f"line {n}: ADD of a URL has no --checksum=sha256:<digest>"
                                                   for n in (2, 3, 4)])

    def test_rustup_add_without_the_manifest_cmp_is_reported(self):
        text = PINNED_FROM + "RUN mkdir /x \\\n && rustup component add clippy\n"
        self.assertEqual(download_problems(text), [
            "line 3: rustup's add isn't followed by a cmp of the image's multirust-channel-manifest.toml"])

    def test_rustup_add_followed_by_the_manifest_cmp_passes(self):
        text = (PINNED_FROM + "RUN cp /r/multirust-channel-manifest.toml /tmp/ \\\n"
                " && rustup target add wasm32-unknown-unknown \\\n"
                " && cmp /tmp/multirust-channel-manifest.toml /r/multirust-channel-manifest.toml\n")
        self.assertEqual(download_problems(text), [])

    def test_manifest_cmp_that_cannot_fail_the_build_is_reported(self):
        cmp = "cmp /tmp/multirust-channel-manifest.toml /r/multirust-channel-manifest.toml"
        cases = {"swallowed": f"RUN rustup component add clippy && {cmp} || true\n",
                 "after ;": f"RUN rustup component add clippy; {cmp}\n",
                 "before the add": f"RUN {cmp} && rustup component add clippy\n"}
        for name, run in cases.items():
            with self.subTest(name):
                self.assertEqual(len(download_problems(PINNED_FROM + run)), 1)


LOCK_SCRIPTED = {"packages": {"": {}, "node_modules/a": {"hasInstallScript": True},
                              "node_modules/b/node_modules/c": {"hasInstallScript": True},
                              "node_modules/d": {}}}


class InstallScripts(unittest.TestCase):
    def test_kit_lockfile_manifest_and_rebuild_agree(self):
        tools = KIT / "npm-tools"
        self.assertEqual(install_script_problems(json.loads((tools / "package-lock.json").read_text()),
                                                 json.loads((tools / "package.json").read_text()),
                                                 (KIT / "Dockerfile").read_text()), [])

    def test_matching_lists_pass(self):
        manifest = {"allowScripts": {"a": True, "c": True}}
        dockerfile = "RUN npm ci \\\n && npm rebuild a c --ignore-scripts=false\n"
        self.assertEqual(install_script_problems(LOCK_SCRIPTED, manifest, dockerfile), [])

    def test_scripted_package_missing_from_allow_scripts_is_reported(self):
        manifest = {"allowScripts": {"a": True}}
        self.assertEqual(install_script_problems(LOCK_SCRIPTED, manifest, "RUN npm rebuild a --ignore-scripts=false\n"),
                         ["c has an install script but isn't in package.json's allowScripts"])

    def test_allow_scripts_and_npm_rebuild_must_match(self):
        manifest = {"allowScripts": {"a": True, "c": True}}
        dockerfile = "RUN npm rebuild a d --ignore-scripts=false\n"
        self.assertEqual(install_script_problems(LOCK_SCRIPTED, manifest, dockerfile),
                         ["c is in allowScripts but not in the Dockerfile's npm rebuild",
                          "d is in the Dockerfile's npm rebuild but not in allowScripts"])


WORKFLOW = ('          git diff --quiet "origin/$BASE...HEAD" -- cocon/Dockerfile%s || echo dockerfile=1 >> "$GITHUB_OUTPUT"\n'
            '          git diff --quiet "origin/$BASE...HEAD" -- cocon/compose.yml || echo compose=1 >> "$GITHUB_OUTPUT"\n')


class PathFilter(unittest.TestCase):
    def test_every_copied_path_triggers_the_extras_build(self):
        self.assertEqual(filter_problems((KIT / "Dockerfile").read_text(),
                                         (ROOT / ".github/workflows/cocon.yml").read_text()), [])

    def test_copied_directory_outside_the_filter_is_reported_with_its_line(self):
        text = PINNED_FROM + "COPY npm-tools/package.json npm-tools/package-lock.json ./\n"
        self.assertEqual(filter_problems(text, WORKFLOW % ""),
                         ["line 2: cocon/npm-tools isn't under a dockerfile=1 path"])

    def test_copied_directory_in_the_filter_passes(self):
        text = (PINNED_FROM + "COPY npm-tools/package.json npm-tools/package-lock.json ./\n"
                "COPY --chown=0:0 entrypoint.sh /usr/local/bin/e\n"
                "COPY --from=other /x /x\n"
                "ADD https://example.com/a.tgz /tmp/\n"
                'COPY ["claude/a.md", "/etc/a.md"]\n')
        self.assertEqual(filter_problems(text, WORKFLOW % " cocon/npm-tools cocon/entrypoint.sh cocon/claude"), [])

    def test_base_and_final_stage_sources_are_exempt_and_other_stages_are_not(self):
        text = (PINNED_FROM.replace("\n", " AS tools\n") + "COPY npm-tools/package.json ./\n"
                + PINNED_FROM.replace("\n", " AS base\n") + "COPY entrypoint.sh /e\nCOPY claude/a.md /a\n"
                + PINNED_FROM.replace("\n", " AS Extra\n") + "COPY extra.sh /x\n"
                + "FROM Extra AS final\nCOPY claude/b.md /b\n")
        self.assertEqual(filter_problems(text, WORKFLOW % ""), [
            "line 2: cocon/npm-tools isn't under a dockerfile=1 path",
            "line 7: cocon/extra.sh isn't under a dockerfile=1 path"])

    def test_a_parent_path_covers_what_is_under_it(self):
        text = PINNED_FROM + "COPY npm-tools/package.json ./\n"
        self.assertEqual(filter_problems(text, WORKFLOW % " cocon"), [])

    def test_sibling_with_a_shared_prefix_is_not_covered(self):
        text = PINNED_FROM + "COPY npm-tools/package.json ./\n"
        self.assertEqual(filter_problems(text, WORKFLOW % " cocon/npm"),
                         ["line 2: cocon/npm-tools isn't under a dockerfile=1 path"])

    def test_workflow_without_a_dockerfile_line_is_reported(self):
        self.assertEqual(filter_problems(PINNED_FROM + "COPY a /a\n", "run: true\n"),
                         ["the workflow has no `git diff ... -- <paths> || echo dockerfile=1` line"])


class Substitutions(unittest.TestCase):
    def test_image_build_exports_no_substitution(self):
        self.assertEqual(substitution_problems((KIT / "Dockerfile").read_text()), [])

    def test_export_of_a_substitution_is_reported_with_its_line(self):
        text = PINNED_FROM + 'RUN true \\\n && export A="$(mktemp -d)" \\\n && true\n'
        self.assertEqual(substitution_problems(text), [
            "line 3: export of a command substitution hides its failure from set -e "
            "(CODING_STANDARDS.md, Shell): assign it first, then export"])

    def test_assign_then_export_passes(self):
        text = PINNED_FROM + 'RUN A="$(mktemp -d)" && export A && export B=/tmp/b C="$HOME"\n'
        self.assertEqual(substitution_problems(text), [])

    def test_other_declarations_and_backticks_are_reported(self):
        cases = {"readonly": 'RUN readonly A="$(id -u)"\n', "local": 'RUN f() { local A="$(id -u)"; }\n',
                 "declare": "RUN declare -x A=$(id -u)\n", "backtick": "RUN export A=`id -u`\n",
                 "after then": 'RUN if true; then export A="$(id -u)"; fi\n',
                 "heredoc": 'RUN <<EOF\nset -e\nexport A="$(id -u)"\nEOF\n'}
        for name, run in cases.items():
            with self.subTest(name):
                self.assertEqual(len(substitution_problems(PINNED_FROM + run)), 1)

    def test_substitution_in_a_plain_command_passes(self):
        text = PINNED_FROM + 'RUN echo "export A=$(id -u)" && v="$(id -u)" && test "$v" = 0\n'
        self.assertEqual(substitution_problems(text), [])


if __name__ == "__main__":
    unittest.main()
