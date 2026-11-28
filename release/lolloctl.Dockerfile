# syntax=docker/dockerfile:1
# The lolloctl image (release/README.md): the CLI and every file it reads at
# run time (the browser engine's seccomp profile among them), plus the two build
# contexts built on the operator's host (doc-tools, code-server/exec). Built by publish.yml from the repo root, after it has
# written RELEASE; never run but through the wrapper (lolloctl-wrapper.sh).
#
# The Docker CLI and its Compose and Buildx plugins come from Docker's own
# image, pinned by digest, so the Compose that runs the stacks is this one, not
# the host's. To upgrade: the digest of `docker buildx imagetools inspect
# docker:<version>-cli`.
FROM docker:29.10.1-cli@sha256:4e60c31615e83523afbb59773b25d055c0da9edcd5f80a4c6a6eebad49de1a25 AS docker

FROM debian:12-slim
RUN apt-get update \
  && apt-get install -y --no-install-recommends bash ca-certificates curl dnsutils jq openssl util-linux \
  && rm -rf /var/lib/apt/lists/*
COPY --from=docker /usr/local/bin/docker /usr/local/bin/docker
COPY --from=docker /usr/local/libexec/docker/cli-plugins/docker-compose /usr/local/libexec/docker/cli-plugins/docker-compose
COPY --from=docker /usr/local/libexec/docker/cli-plugins/docker-buildx /usr/local/libexec/docker/cli-plugins/docker-buildx
COPY --chmod=0755 lolloctl /opt/lollo-rozzo/lolloctl
COPY config.env.example RELEASE /opt/lollo-rozzo/
COPY lib /opt/lollo-rozzo/lib
COPY compose /opt/lollo-rozzo/compose
COPY agent-skills /opt/lollo-rozzo/agent-skills
COPY release/images /opt/lollo-rozzo/release/images
# Not run here: lolloctl fills it in from a dev build's image (fetch_dev_wrapper).
COPY release/lolloctl-wrapper.sh /opt/lollo-rozzo/release/lolloctl-wrapper.sh
COPY scripts/dns-preflight.sh /opt/lollo-rozzo/scripts/dns-preflight.sh
COPY landing/executor/run.mjs /opt/lollo-rozzo/landing/executor/run.mjs
COPY doc-tools /opt/lollo-rozzo/doc-tools
COPY code-server/exec /opt/lollo-rozzo/code-server/exec
# Compose reads the browser engine's seccomp profile itself (compose/browser.yml).
COPY browser/engine/seccomp.json /opt/lollo-rozzo/browser/engine/seccomp.json
RUN chmod -R a+rX,go-w /opt/lollo-rozzo
ENTRYPOINT ["/opt/lollo-rozzo/lolloctl"]
