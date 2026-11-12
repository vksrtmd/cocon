# The kit and its tests are Python 3.9, standard library only

The launcher, host-guard, `workflow-push` and the tests are Python 3.9 with only the standard library, because Python is on
every Mac that can run cocon (the Command Line Tools behind `/usr/bin/git` ship `python3`) and bash 3.2 cost too
much as the code grew. Go needs a toolchain or a release step; Node isn't on a clean Mac. The container entrypoint
stays bash: the image has no Python without `--with python`.

- **Floor: 3.9**, the Command Line Tools' `python3`, so a Mac runs the kit and `tests/check.sh` with nothing
  installed. CI and the cocoon run newer Pythons: `tests/test_kit_python.py` parses the kit with the 3.9 grammar;
  library calls newer than 3.9 are caught only by review.
- No pytest, pip, virtualenv or Python linter: nothing to pin or allowlist. Tests are `unittest`.
- Each command in `cocon/` is a bash shim running `python3 -I -B cocon/py COMMAND`, so `bin/` and the commands'
  names stay stable. The launcher and `workflow-push` call host-guard in-process, so a refusal can't be lost between a
  child's exit code and the caller's reading of it.
- Tests run the kit through its executables, with a stub `docker`; a test imports a kit module only for a case no
  process boundary can stage.
