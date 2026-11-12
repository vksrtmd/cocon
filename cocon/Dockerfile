# syntax=docker/dockerfile:1@sha256:4edf897a3ffa55b89f906fc8cc78afdb3f1834cc9c7083565e611a8a7d5fe99e
# cocon: Node/TS dev image with Claude Code and gh, plus the extras a session asks for
# (`cocon --with`): each one a stage after the base, switched on by a WITH_<NAME>=1 build arg.
# Unselected extras cost nothing: BuildKit skips the stages the chosen path doesn't reach.
# Like the base build, an extras build doesn't go through the egress proxy.
# Multi-arch: builds natively on Apple Silicon (arm64) and x86_64.

ARG WITH_CLICKHOUSE=0 WITH_DOCKER=0 WITH_GO=0 WITH_HELM=0 WITH_KAFKA=0 WITH_PLAYWRIGHT=0 WITH_POSTGRES=0 \
    WITH_PYTHON=0 WITH_RUST=0 WITH_TAURI=0

FROM golang:1.27.4-bookworm@sha256:4fa7c968ba93f1635670522dbb01a2c78150443e1385746924566079f36570ea AS go-src
FROM clickhouse/clickhouse-server:26.10.3.7@sha256:739e099abd1157390960a59e4532d21781d8fde57e6e4151cb920ea02c30b1ce AS clickhouse-src
FROM docker:29.9.3-cli@sha256:2357639ad1ea7d0194134bf3292622fd4e562483966ed674e5d7c945b5f3b28d AS docker-src
FROM astral/uv:0.13.7@sha256:96f40d3904aad8d0bd010539001d36c8e2188b981e1334471a90c4a1d93bb490 AS uv-src
FROM rust:1.99.1-slim-bookworm@sha256:ed2c95ca377d6571b124b05e75e01207973ded639dce7d5542eb7bcd797e32aa AS rust-src

# The Go tools: actionlint for the base, gopls for the go extra. Static binaries, so the base ships no
# Go toolchain. One module per `go install`: a single call rejects modules at different versions.
FROM go-src AS go-tools
# Renovate bumps the pins through the `# renovate:` comments.
# renovate: datasource=go depName=golang.org/x/tools/gopls
ARG GOPLS_VERSION=v0.24.0
# renovate: datasource=go depName=github.com/rhysd/actionlint
ARG ACTIONLINT_VERSION=v1.7.12
RUN export CGO_ENABLED=0 GOBIN=/out \
 && go install "golang.org/x/tools/gopls@${GOPLS_VERSION}" \
 && go install "github.com/rhysd/actionlint/cmd/actionlint@${ACTIONLINT_VERSION}"

# The global npm tools, every transitive version from the kit's lockfile (npm-tools/, which Renovate's
# npm manager updates). Install scripts run only for the manifest's allowScripts; the check catches
# a postinstall that left Claude Code's stub in place.
FROM node:26.13.0-bookworm@sha256:a546d3f8acd2af4382668a7e6acd3bc4418e6a27478373e3246f8162f5181509 AS npm-tools
WORKDIR /npm-tools
COPY npm-tools/package.json npm-tools/package-lock.json ./
RUN npm ci --ignore-scripts --no-audit --no-fund \
 && npm rebuild @anthropic-ai/claude-code --ignore-scripts=false \
 && v="$(node -p 'require("./package-lock.json").packages["node_modules/@anthropic-ai/claude-code"].version')" \
 && node_modules/.bin/claude --version | grep -qxF "$v (Claude Code)"

FROM node:26.13.0-bookworm@sha256:a546d3f8acd2af4382668a7e6acd3bc4418e6a27478373e3246f8162f5181509 AS base

# --- System toolchain: build basics and core utils --------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential pkg-config \
      git curl ca-certificates gnupg jq ripgrep fd-find less procps vim-tiny shellcheck \
 && rm -rf /var/lib/apt/lists/*

# --- GitHub CLI ---------------------------------------------------------------
# The keyring's keys, as docs/install_linux.md in cli/cli lists them: a key it adds fails the build
# until it's pinned here. A key doesn't version, so Renovate has nothing to bump.
ARG GH_CLI_FINGERPRINTS="2C6106201985B60E6C7AC87323F3D4EA75716059 7F38BBB59D064DBCB3D84D725612B36462313325"
RUN curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg -o /tmp/gh.gpg \
 && GNUPGHOME="$(mktemp -d)" && export GNUPGHOME \
 && gpg --show-keys --with-colons /tmp/gh.gpg | awk -F: -v pinned=" $GH_CLI_FINGERPRINTS " ' \
      $1 == "pub" { p = 1 } \
      $1 == "fpr" && p { p = 0; n++; if (!index(pinned, " " $10 " ")) { print "GitHub CLI keyring: key " $10 " is not pinned" > "/dev/stderr"; bad = 1 } } \
      END { if (!n) print "GitHub CLI keyring: no keys" > "/dev/stderr"; exit bad || !n }' \
 && install -m 0644 /tmp/gh.gpg /usr/share/keyrings/githubcli-archive-keyring.gpg && rm -r /tmp/gh.gpg "$GNUPGHOME" \
 && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
      > /etc/apt/sources.list.d/github-cli.list \
 && apt-get update && apt-get install -y --no-install-recommends gh \
 && rm -rf /var/lib/apt/lists/*

# --- actionlint (the go-tools stage)
COPY --from=go-tools /out/actionlint /usr/local/bin/actionlint

# --- Claude Code + TS tooling (the npm-tools stage), their bins on PATH as with npm install -g ----
COPY --from=npm-tools /npm-tools /usr/local/lib/cocon-npm
# Node 25+ no longer bundles corepack, so it comes from the lockfile too.
# ln -s would make a dangling link for a tool the lockfile lost: fail the build instead.
RUN for b in claude corepack tsc typescript-language-server; do \
      t="/usr/local/lib/cocon-npm/node_modules/.bin/$b"; test -x "$t" || { echo "$t missing" >&2; exit 1; }; \
      ln -s "$t" /usr/local/bin/; done \
 && corepack enable --install-directory /usr/local/bin

# --- Git: GitHub over HTTPS via gh's credential helper (SSH remotes rewritten) --
RUN git config --system credential.https://github.com.helper '!gh auth git-credential' \
 && git config --system url."https://github.com/".insteadOf "git@github.com:" \
 && git config --system --add url."https://github.com/".insteadOf "ssh://git@github.com/" \
 && git config --system --add safe.directory '*'

# The base image's user is "node" (UID 1000); rename it, keeping the UID so
# existing volumes stay readable. Pre-create mount points so volumes inherit ownership.
RUN groupmod -n claude node && usermod -l claude -d /home/claude -m node \
 && mkdir -p /home/claude/.claude /home/claude/.cache /home/claude/go/pkg/mod /home/claude/.npm \
 && chown -R claude:claude /home/claude

ENV CLAUDE_CONFIG_DIR=/home/claude/.claude \
    DISABLE_AUTOUPDATER=1 \
    CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1

# ============================ Extras (root, after the base) ============================
# Each is `<name>-0` (nothing) or `<name>-1` (installs), and the stage `<name>` picks one by
# its WITH_ arg and carries on from the previous extra.

# --- ClickHouse client (single binary)
FROM base AS clickhouse-0
FROM base AS clickhouse-1
COPY --from=clickhouse-src /usr/bin/clickhouse /usr/local/bin/clickhouse
RUN ln -s /usr/local/bin/clickhouse /usr/local/bin/clickhouse-client
FROM clickhouse-${WITH_CLICKHOUSE} AS clickhouse

# --- Docker CLI (static) + buildx/compose plugins; the daemon is the docker sidecar
FROM clickhouse AS docker-0
FROM clickhouse AS docker-1
COPY --from=docker-src /usr/local/bin/docker /usr/local/bin/docker
COPY --from=docker-src /usr/local/libexec/docker/cli-plugins /usr/local/libexec/docker/cli-plugins
FROM docker-${WITH_DOCKER} AS docker

# --- Go: the toolchain from the official image, and gopls (the go-tools stage); modules go to the
# gomod volume, the build cache to the cache volume
FROM docker AS go-0
FROM docker AS go-1
COPY --from=go-src /usr/local/go /usr/local/go
COPY --from=go-tools /out/gopls /usr/local/bin/gopls
ENV PATH=/usr/local/go/bin:/home/claude/go/bin:$PATH \
    GOPATH=/home/claude/go \
    GOTOOLCHAIN=local
FROM go-${WITH_GO} AS go

# --- Helm, from its release. A Renovate bump fails the build until the digests (the release notes list them) follow.
FROM go AS helm-0
FROM go AS helm-1
# renovate: datasource=github-releases depName=helm/helm
ARG HELM_VERSION=v3.22.1
ARG TARGETARCH \
    HELM_SHA256_AMD64=896cf63684a42a723d61c980369d6e588330927deb13676ca0d85006dd6da0ea \
    HELM_SHA256_ARM64=4c29e286b279d170fa048ddfc3e4e83dcd069ca9c52102766ba672d7149e694d
RUN curl -fsSL -o /tmp/helm.tgz "https://get.helm.sh/helm-${HELM_VERSION}-linux-${TARGETARCH}.tar.gz" \
 && echo "$(case "$TARGETARCH" in amd64) echo "$HELM_SHA256_AMD64";; arm64) echo "$HELM_SHA256_ARM64";; esac)  /tmp/helm.tgz" \
      | sha256sum -c \
 && tar -xzf /tmp/helm.tgz -C /usr/local/bin --strip-components=1 --no-same-owner "linux-${TARGETARCH}/helm" \
 && rm /tmp/helm.tgz
FROM helm-${WITH_HELM} AS helm

# --- kcat, the Kafka client
FROM helm AS kafka-0
FROM helm AS kafka-1
RUN apt-get update && apt-get install -y --no-install-recommends kcat \
 && rm -rf /var/lib/apt/lists/*
FROM kafka-${WITH_KAFKA} AS kafka

# --- Playwright: Chromium's system libs (apt needs root, so not at runtime)
# Browsers themselves are per-version: a launch with `--with playwright` downloads the
# repo's build into ~/.cache/ms-playwright (cache volume).
FROM kafka AS playwright-0
FROM kafka AS playwright-1
# The lockfile's Playwright (npm-tools stage), so this root step runs no freshly resolved package.
RUN /usr/local/lib/cocon-npm/node_modules/.bin/playwright install-deps chromium \
 && rm -rf /var/lib/apt/lists/*
FROM playwright-${WITH_PLAYWRIGHT} AS playwright

# --- psql
FROM playwright AS postgres-0
FROM playwright AS postgres-1
RUN apt-get update && apt-get install -y --no-install-recommends postgresql-client \
 && rm -rf /var/lib/apt/lists/*
FROM postgres-${WITH_POSTGRES} AS postgres

# --- Python: uv + pip and venv
FROM postgres AS python-0
FROM postgres AS python-1
RUN apt-get update && apt-get install -y --no-install-recommends python3-pip python3-venv \
 && rm -rf /var/lib/apt/lists/*
COPY --from=uv-src /uv /uvx /usr/local/bin/
FROM python-${WITH_PYTHON} AS python

# --- Rust: the toolchain under /usr/local; crates go to CARGO_HOME, a per-repo volume
# rustup and the toolchain come from the official image (minimal profile); rustup adds the other
# components, checking each against the sha256s in the toolchain's installed channel manifest,
# which comes from the digest-pinned image. The cmp fails the build if rustup replaced that manifest
# with one fetched from the server. The image leaves both trees world-writable, so rust-tree makes
# them root-only writable before they copy in root-owned: a symbolic `COPY --chmod=u=rwX,go=rX`
# left rustup unexecutable under GitHub's 20261028 runner image. cmake is for the build scripts of
# -sys crates that call it (aws-lc-sys, rustls's default crypto, among them). protoc, for prost-build
# and tonic-build, comes from protobuf's release with its include/ tree: Debian's 3.21 lacks the well-known
# types' .proto files. protoc and cargo-nextest: a Renovate bump fails the build until the digests (the
# release's assets list them) follow. cargo.sh moves each checkout's target dir off the bind mount; it goes
# first on PATH. RUSTUP_AUTO_INSTALL=0: a rust-toolchain.toml naming a target or component the
# image lacks would otherwise make every cargo call try to install it, which the root-owned tree and
# the allowlist (no static.rust-lang.org) refuse.
FROM rust-src AS rust-tree
RUN chmod -R go-w /usr/local/rustup /usr/local/cargo
FROM python AS rust-0
FROM python AS rust-1
COPY --from=rust-tree --chown=0:0 /usr/local/rustup /usr/local/rustup
COPY --from=rust-tree --chown=0:0 /usr/local/cargo /usr/local/cargo
RUN apt-get update && apt-get install -y --no-install-recommends cmake unzip \
 && rm -rf /var/lib/apt/lists/*
# renovate: datasource=github-releases depName=protocolbuffers/protobuf extractVersion=^v(?<version>.+)$
ARG PROTOC_VERSION=35.1
# renovate: datasource=github-releases depName=nextest-rs/nextest extractVersion=^cargo-nextest-(?<version>.+)$
ARG NEXTEST_VERSION=0.9.151
ARG TARGETARCH \
    PROTOC_SHA256_AMD64=6930ebf62bd4ea607b98fff052596c6ee564b9835b4ce172c75a3f53ae9d91b7 \
    PROTOC_SHA256_ARM64=01bf9d08808c7f96678b63f4bd8efa559bb4f83d5a7a270d5edaf507f9d5d9cf \
    NEXTEST_SHA256_AMD64=1d4ce302b820c41f4b89c5fb9bce45f0f0c94882c1cf08fc360caf08be6e7df4 \
    NEXTEST_SHA256_ARM64=3e35e8f6913af92558ba09d7155f74e35dd1f0024ef3cd891637433ae4eacbdd
RUN case "$TARGETARCH" in amd64) p=x86_64 n=x86_64;; arm64) p=aarch_64 n=aarch64;; esac \
 && curl -fsSL -o /tmp/protoc.zip \
      "https://github.com/protocolbuffers/protobuf/releases/download/v${PROTOC_VERSION}/protoc-${PROTOC_VERSION}-linux-${p}.zip" \
 && echo "$(case "$TARGETARCH" in amd64) echo "$PROTOC_SHA256_AMD64";; arm64) echo "$PROTOC_SHA256_ARM64";; esac)  /tmp/protoc.zip" \
      | sha256sum -c \
 && curl -fsSL -o /tmp/nextest.tgz \
      "https://github.com/nextest-rs/nextest/releases/download/cargo-nextest-${NEXTEST_VERSION}/cargo-nextest-${NEXTEST_VERSION}-${n}-unknown-linux-gnu.tar.gz" \
 && echo "$(case "$TARGETARCH" in amd64) echo "$NEXTEST_SHA256_AMD64";; arm64) echo "$NEXTEST_SHA256_ARM64";; esac)  /tmp/nextest.tgz" \
      | sha256sum -c \
 && unzip -q /tmp/protoc.zip -d /usr/local bin/protoc 'include/*' \
 && tar -xzf /tmp/nextest.tgz -C /usr/local/bin --no-same-owner cargo-nextest \
 && chmod -R go-w /usr/local/bin/protoc /usr/local/include/google \
 && rm /tmp/protoc.zip /tmp/nextest.tgz
COPY --chmod=0755 cargo.sh /usr/local/lib/cocon/cargo/cargo
ENV RUSTUP_HOME=/usr/local/rustup CARGO_HOME=/home/claude/.cargo RUSTUP_AUTO_INSTALL=0 \
    PATH=/usr/local/lib/cocon/cargo:/usr/local/cargo/bin:/home/claude/.cargo/bin:$PATH
RUN m="$(echo /usr/local/rustup/toolchains/*/lib/rustlib/multirust-channel-manifest.toml)" && cp "$m" /tmp/ \
 && CARGO_HOME=/usr/local/cargo rustup component add clippy rustfmt rust-analyzer \
 && cmp /tmp/multirust-channel-manifest.toml "$m" && rm /tmp/multirust-channel-manifest.toml \
 && mkdir /home/claude/.cargo && chown claude:claude /home/claude/.cargo
FROM rust-${WITH_RUST} AS rust

# --- Tauri: the system libs a Tauri app's CI installs for cargo check, clippy and test (WebKitGTK brings GTK 3,
# libsoup 3, JavaScriptCore); the toolchain is the rust extra's.
FROM rust AS tauri-0
FROM rust AS tauri-1
RUN apt-get update && apt-get install -y --no-install-recommends \
      libwebkit2gtk-4.1-dev libappindicator3-dev librsvg2-dev patchelf pkg-config \
 && rm -rf /var/lib/apt/lists/*
FROM tauri-${WITH_TAURI} AS tauri

FROM tauri AS final
# Claude Code policy + entrypoint: often edited, so here, after the extras, an edit rebuilds none of them.
COPY claude/managed-settings.json /etc/claude-code/managed-settings.json
COPY claude/container-notes.md /opt/cocon/container-notes.md
COPY --chmod=0755 entrypoint.sh /usr/local/bin/cocon-entrypoint
# No setuid or setgid files (su, mount, passwd...): inert under no-new-privileges and no
# capabilities, and kept out so a change to either can't open a root path. Last root step, after
# every extra; the build fails if one survives.
RUN find / -xdev -type f -perm /6000 -exec chmod a-s {} + \
 && left="$(find / -xdev -type f -perm /6000)" \
 && { [ -z "$left" ] || { echo "setuid/setgid files remain: $left" >&2; exit 1; }; }
USER claude
ENTRYPOINT ["cocon-entrypoint"]
CMD ["claude"]
