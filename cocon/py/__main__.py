"""The kit's commands, run by their shims in cocon/ as `python3 -I -B cocon/py COMMAND ARGS...` (ADR 0013).

-I keeps PYTHON* variables, the user's site-packages and the current directory out of sys.path; Python still puts
this directory first, so `kit` is the package beside this file and nothing else on the host. -B writes no bytecode
into the checkout. Python 3.9 is the floor: stock macOS python3 (Command Line Tools).
"""
import sys

if sys.version_info < (3, 9):
    sys.exit(f"cocon: needs Python 3.9 or later, found {sys.version.split()[0]}; run: xcode-select --install")

from kit import main  # noqa: E402  # after the version check, which must run on any python3

sys.exit(main(sys.argv[1:]))
