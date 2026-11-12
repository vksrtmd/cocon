# The slug is the repo ID

A checkout's slug, which names its compose project, databases, `node_modules` and detached session, is its pinned
repo ID, `<owner>__<repo>`, whatever folder holds it; a worktree's is `<main's slug>--<worktree>`. The folder name
alone would let `~/repos/api` (acme/api) and `~/work/api` (other/api, or acme/web) share databases, and code planted
in one's `node_modules` run in the other's session under its token; `--destroy` in one would remove the other's.

So a second clone of one repo in another folder shares the first's slug, and with it its databases, `node_modules`
and detached session; `--destroy` in one removes the other's. That is unsupported but not refused: worktrees cover
parallel work, and sharing stays within one repo and its token. The launch and `--doctor` warn while another main
checkout pinned to the same repo ID, in any case, still exists. Keying the slug by folder too, or by a hash of the checkout's path,
would separate clones, at the cost of a slug the folder decides, or unreadable names in `--ls` and tmux.

Sanitizing would let `foo.bar`, `foo-bar` and `Foo_Bar` share a slug, and a name `a--b` share one with `a`'s
worktree `b`, as would `x-` with worktree `w` and `x` with worktree `-w`. So a repo name that sanitizing changes beyond
case, or that holds `--` or starts or ends with `-`, gets `_<hash of the lowercased repo ID>`; a worktree or
origin-less folder name like that, or that sanitizing changes at all, gets `_<hash of its path>`. Case alone changes no repo name's slug, since GitHub names ignore case. Sanitizing never makes a `_`, so a
hashed name can't meet a clean one, and clean names, most of them, stay readable. A changed slug moves no data, as
the old project may be another checkout's.

A checkout without a GitHub `origin` keeps the bare folder name, so two such same-named folders share a slug and
per-repo volumes; `--doctor` says so.
