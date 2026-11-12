# GitHub access is one fine-grained token per repo

A session gets a fine-grained personal access token scoped to its own repository, held by the session's credential
broker (ADR 0014) and chosen by the pinned repo ID, without the Workflows permission. A misbehaving session can then
reach only that repo, and can't edit a workflow file. The cost is manual: one token to create per repo and to rotate
every 30-90 days. A GitHub App minting per-repo installation tokens at launch would remove the rotation but needs an
App and its private key kept on the host; switch when rotation becomes the pain.

## Consequences

- Within the repo the session acts as the user: it pushes and deletes unprotected branches and tags, edits releases
  and others' issues, and merges its own PR unless branch protection requires an approval it can't give. The kit
  can't read branch protection with this token, so the README lists the settings.
- The session chooses what CI runs: the workflows run the PR's own code on GitHub's runners with open internet, so
  they get a read-only `GITHUB_TOKEN` not left in the git config. Workflow changes reach the repo only as a patch the
  user applies.
