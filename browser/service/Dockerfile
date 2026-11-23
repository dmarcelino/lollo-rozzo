# The lollo browser's service (see browser/README.md): the agents' MCP tools,
# driving the engine's Chromium (browser/engine/) over Playwright. Built by
# lolloctl via compose/browser.yml. `tests/run-sidecar.sh browser` builds the
# `test` stage and runs it. Both need the `lollo-common` build context.
# Bookworm, not trixie: the test stage's `playwright install --with-deps`
# supports it. Python 3.14 like the other sidecars (lollo-common needs it).
ARG NOVNC_VERSION=1.7.0
ARG NOVNC_SHA256=32689f18d6abe96bc6530828a6bd0b9ae33bda07c083a6575ed255b5a8f2e903

# The live view's client, served by this service from /browser/novnc/: noVNC's
# ES modules from the npm tarball, pinned by version and SHA-256, so no page
# loads anything from a third-party origin. Upgrading: change both ARGs (the
# hash is the tarball's sha256, e.g. `curl -fsSL <url> | sha256sum`).
FROM python:3.14-slim-bookworm AS novnc
ARG NOVNC_VERSION
ARG NOVNC_SHA256
ADD --checksum=sha256:${NOVNC_SHA256} https://registry.npmjs.org/@novnc/novnc/-/novnc-${NOVNC_VERSION}.tgz /tmp/novnc.tgz
RUN mkdir /opt/novnc \
 && tar -xzf /tmp/novnc.tgz -C /opt/novnc --no-same-owner --strip-components=1 \
      package/core package/vendor package/LICENSE.txt \
 && test -f /opt/novnc/core/rfb.js

FROM python:3.14-slim-bookworm AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
# Playwright's bundled Node driver needs libatomic1. No browser is installed
# here: Chromium runs in the engine.
RUN apt-get update && apt-get install -y --no-install-recommends libatomic1 && rm -rf /var/lib/apt/lists/*
COPY --from=novnc /opt/novnc /opt/novnc
COPY --from=lollo-common . /tmp/lollo-common
RUN pip install --root-user-action=ignore /tmp/lollo-common && rm -rf /tmp/lollo-common
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --root-user-action=ignore .

# Tests drive a Chromium launched in this stage only, through the same
# interface as the engine connection (tests/conftest.py).
FROM base AS test
RUN pip install --root-user-action=ignore ".[test]" \
 && python -m playwright install --with-deps chromium
COPY tests ./tests
CMD ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider"]

FROM base
# A fixed non-root user that owns /data, so the named volume Docker creates on
# first start inherits that ownership.
RUN groupadd --system --gid 10004 browser \
 && useradd --system --uid 10004 --gid 10004 --no-create-home browser \
 && mkdir /data && chown 10004:10004 /data && chmod 700 /data
USER 10004
VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"]
CMD ["python", "-m", "lollo_browser"]
