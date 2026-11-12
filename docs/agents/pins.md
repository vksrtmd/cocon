# Looking up a pin

Images in `cocon/` (Dockerfile `FROM`, `compose.yml` `image:`) are `name:tag@sha256:<digest>`. The digest is the
tag's index (multi-platform) digest. In the cocoon, read it from Docker Hub's registry API through the proxy
(`registry-1.docker.io` and `auth.docker.io` are allowlisted); `docker manifest inspect` and
`docker buildx imagetools inspect` print the per-platform manifests, whose digests are not the pin.

```sh
repo=mitmproxy/mitmproxy tag=12.3.1   # official images: repo=library/busybox
tok=$(curl -s "https://auth.docker.io/token?service=registry.docker.io&scope=repository:$repo:pull" \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["token"])')
curl -sI -H "Authorization: Bearer $tok" \
  -H 'Accept: application/vnd.oci.image.index.v1+json,application/vnd.docker.distribution.manifest.list.v2+json' \
  "https://registry-1.docker.io/v2/$repo/manifests/$tag" | grep -i '^docker-content-digest'
```

The `docker-content-digest` header is the digest to pin. A tag that was republished since its pin was written
gives a different digest than the file's: Renovate moves those.

The image's npm tools are pinned by `cocon/npm-tools/package-lock.json`: change the version in its
`package.json`, then run `npm install --package-lock-only` in that directory. A new package with an install
script stays off until it is in `allowScripts` and in the Dockerfile's `npm rebuild` with a reason (the
manifest's `//` note); `tests/test_dockerfile.py` fails until the lockfile and both lists agree.

Renovate puts pin and digest bumps in one "digests" PR, except the credential broker's and the egress proxy's
images, which get a PR each (`renovate.json`).

## Other pins

Reachable hosts are the ones in `cocon/squid/allowlist.txt`. Not reachable from the cocoon, with the way round:

- `ghcr.io`, `docs.docker.com`, `hub.docker.com`: use the Docker Hub image, or read the registry API above.
- `static.rust-lang.org`, `cli.github.com`, the keyservers: a build step that fetches from them can't run here (CI builds it).
- `release-assets.githubusercontent.com`, where GitHub release downloads redirect: take the asset's sha256 from the API
  instead of hashing the download: `gh api repos/<owner>/<repo>/releases/tags/<tag> --jq '.assets[] | {name, digest}'`.
- The GitHub CLI apt key's fingerprints are listed in `cli/cli`'s install_linux.md (in its docs folder) on `raw.githubusercontent.com`.

A release download pinned by sha256 next to a `# renovate:` version comment fails Renovate's bump until a person
copies the new digests in. Prefer one of the two shapes Renovate can bump alone:

- The project signs its releases: verify the signature, the signer's primary key fingerprint pinned (Helm's
  `HELM_SIGNERS`). A new signer fails the build until pinned: take its fingerprint from the release notes and
  check it is in the project's KEYS file.
- The asset is on a GitHub release: one `ARG <NAME>_TAG_<ARCH>=<tag> <NAME>_SHA256_<ARCH>=<digest>` line per
  arch under a `# renovate-digest:` comment (cargo-nextest); Renovate's `github-release-attachments` datasource
  takes the new digest from the new release's assets. One line per arch: two matches sharing a version line
  leave the second digest stale.

Renovate config: `npx --yes --package renovate renovate-config-validator --strict --no-global` validates it; ignore its
re2 stack trace, the exit status is the result. `renovate --dry-run=lookup` can't finish from the cocoon (GitHub API rate
limit without a token, Docker Hub's API refused), so say in the PR body that grouping and release age went unobserved.

`docker build --check cocon` rejects an `ARG` or `ENV` name containing `KEY` or `SECRET` (`SecretsUsedInArgOrEnv`).
