"""workflow-push's read of the files a session writes (kit.workflowpush.sessionfiles), in-process: a swap between two system calls
is a race the process boundary can't stage."""
import os
import shutil
import tempfile
import unittest
from unittest import mock

from harness import import_kit

import_kit()
from kit.workflowpush import sessionfiles  # noqa: E402


class SessionFiles(unittest.TestCase):
    def setUp(self):
        self.d = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.d, True)
        self.secret = f"{self.d}/secret"
        with open(self.secret, "w") as f:
            f.write("key\n")

    def write(self, rel, text):
        os.makedirs(os.path.dirname(f"{self.d}/{rel}"), exist_ok=True)
        with open(f"{self.d}/{rel}", "w") as f:
            f.write(text)
        return f"{self.d}/{rel}"

    def swap_after_stat_of(self, name, swap):
        """Patch the module's stat to run swap() right after it looks at name, before the open."""
        real = os.stat

        def stat(path, *a, **kw):
            st = real(path, *a, **kw)
            if path == name:
                swap()
            return st
        return mock.patch.object(sessionfiles.os, "stat", stat)

    def test_reads_a_regular_file_and_misses_a_missing_one(self):
        self.assertEqual(sessionfiles.read(self.write("git/pr-title-x.txt", "T\n")), b"T\n")
        self.assertIsNone(sessionfiles.read(f"{self.d}/git/none"))
        self.assertIsNone(sessionfiles.read(f"{self.d}/nodir/none"))

    def test_symlinked_file_or_dir_on_the_way_is_refused(self):
        os.symlink(self.secret, f"{self.d}/link")
        os.makedirs(f"{self.d}/notes")
        os.symlink(f"{self.d}/notes", f"{self.d}/pr-body-x")
        for path in (f"{self.d}/link", f"{self.d}/pr-body-x/secret.md", f"{self.d}/pr-body-x/none"):
            with self.subTest(path=path), self.assertRaises(sessionfiles.Symlink):
                sessionfiles.read(path)

    def test_title_that_is_a_symlink_only_while_opened_is_refused(self):
        """The race: a regular file when looked at, a symlink to a secret when opened."""
        title = self.write("pr-title-race.txt", "T\n")

        def swap():
            os.unlink(title)
            os.symlink(self.secret, title)
        with self.swap_after_stat_of("pr-title-race.txt", swap), self.assertRaises(sessionfiles.Symlink):
            sessionfiles.read(title)

    def test_dir_swapped_for_a_symlink_after_the_look_is_refused(self):
        self.write("pr-body-x/secret.md", "body\n")
        os.makedirs(f"{self.d}/notes")
        with open(f"{self.d}/notes/secret.md", "w") as f:
            f.write("key\n")

        def swap():
            os.rename(f"{self.d}/pr-body-x", f"{self.d}/old")
            os.symlink(f"{self.d}/notes", f"{self.d}/pr-body-x")
        with self.swap_after_stat_of("pr-body-x", swap), self.assertRaises(sessionfiles.Symlink):
            sessionfiles.read(f"{self.d}/pr-body-x/secret.md")

    def test_fifo_swapped_in_neither_hangs_nor_reads(self):
        title = self.write("pr-title-fifo.txt", "T\n")

        def swap():
            os.unlink(title)
            os.mkfifo(title)
        with self.swap_after_stat_of("pr-title-fifo.txt", swap), self.assertRaises(sessionfiles.Unreadable):
            sessionfiles.read(title)


if __name__ == "__main__":
    unittest.main()
