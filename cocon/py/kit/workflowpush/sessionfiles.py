"""Read a file the session wrote, once, without following a symlink anywhere on its path (ADR 0015).

Each path component is opened relative to the one before with O_NOFOLLOW, so a symlink swapped in at any time
fails the open itself: there is no check-then-open window. What was read is the file that was opened."""
import errno
import os
import stat

_DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_LINK_ERRNOS = (errno.ELOOP, errno.ENOTDIR, errno.EMLINK)  # what opening a symlink with O_NOFOLLOW gives


class Symlink(Exception):
    """The file is, or lies behind, a symlink."""


class Unreadable(Exception):
    pass


def _open(name, flags, dir_fd):
    """Open one component; None if it is gone. Raises Symlink or Unreadable."""
    try:
        return os.open(name, flags, dir_fd=dir_fd)
    except FileNotFoundError:
        return None
    except OSError as e:
        if e.errno in _LINK_ERRNOS:
            raise Symlink() from None
        raise Unreadable() from None


def _kind(name, dir_fd):
    """The file type bits of name, not following it; None if it is gone."""
    try:
        return stat.S_IFMT(os.stat(name, dir_fd=dir_fd, follow_symlinks=False).st_mode)
    except FileNotFoundError:
        return None
    except OSError:
        raise Unreadable() from None


def read(path):
    """The bytes of the regular file at path (absolute, physical), or None when it, or a dir on the way, is missing or
    isn't a file or dir. Raises Symlink when any component is a symlink, Unreadable when it can't be read."""
    parts = [p for p in path.split("/") if p]
    fd = _open("/", _DIR, None)
    try:
        for i, part in enumerate(parts):
            last = i == len(parts) - 1
            kind = _kind(part, fd)
            if kind == stat.S_IFLNK:
                raise Symlink()
            if kind != (stat.S_IFREG if last else stat.S_IFDIR):
                return None
            # O_NONBLOCK: a fifo swapped in can't hang the open; the fstat below refuses it.
            nfd = _open(part, (os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK) if last else _DIR, fd)
            if nfd is None:
                return None
            os.close(fd)
            fd = nfd
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise Unreadable()
        os.set_blocking(fd, True)
        chunks = []
        while True:
            chunk = os.read(fd, 1 << 16)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
    except OSError:
        raise Unreadable() from None
    finally:
        os.close(fd)
