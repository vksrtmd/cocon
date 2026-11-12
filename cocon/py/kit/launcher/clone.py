"""cocon clone OWNER/REPO [NAME]: clone a GitHub repo under $COCON_REPOS and say which token file to make."""
import os
import shutil

from kit.util import Fail, cfg_dir, must, run


def clone(args, repos):
    spec = args[0] if args else ""
    if "/" not in spec:
        raise Fail("usage: cocon clone OWNER/REPO [NAME]")
    owner, repo = spec.split("/", 1)
    repo = repo[:-4] if repo.endswith(".git") else repo
    name = args[1] if len(args) > 1 else repo
    dest = f"{repos}/{name}"
    if os.path.lexists(dest):
        raise Fail(f"{dest} already exists")
    os.makedirs(repos, exist_ok=True)
    if shutil.which("gh"):
        must(run(["gh", "repo", "clone", f"{owner}/{repo}", dest], capture=False))
    else:
        must(run(["git", "clone", f"git@github.com:{owner}/{repo}.git", dest], capture=False))
    cfg = cfg_dir()
    tok = f"{cfg}/tokens/{owner}__{repo}.env"
    if os.path.isfile(tok):
        print(f"Token found: {tok}")
        return 0
    print(f"""
Cloned to {dest}. Create a fine-grained token for {owner}/{repo}
(see README > GitHub access), then:

  mkdir -p '{cfg}/tokens'
  printf 'GH_TOKEN=%s\\n' 'github_pat_...' > '{tok}'
  chmod 600 '{tok}'

Start working:  cocon {name}""")
    return 0
