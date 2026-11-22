# lollo-browser-engine: Playwright's Chromium with its sandbox on, and TigerVNC's
# Xvnc as its display and the live view's source (browser/README.md). Built by
# lolloctl via compose/browser.yml, which runs it with a read-only root, /tmp on
# tmpfs, every capability dropped, no-new-privileges and seccomp.json.
# `browser/engine/tests/run.sh` builds the `test` stage and runs it the same way.
FROM node:22-bookworm-slim AS base
# Chromium's libraries (Playwright's list for Debian 12, without its Xvfb), the
# display, tini as PID 1, and fonts for the common scripts and emoji.
RUN apt-get update && apt-get install -y --no-install-recommends \
      tini tigervnc-standalone-server tigervnc-tools \
      libasound2 libatk-bridge2.0-0 libatk1.0-0 libatspi2.0-0 libcairo2 libcups2 \
      libdbus-1-3 libdrm2 libgbm1 libglib2.0-0 libnspr4 libnss3 libpango-1.0-0 \
      libx11-6 libxcb1 libxcomposite1 libxdamage1 libxext6 libxfixes3 \
      libxkbcommon0 libxrandr2 \
      fontconfig fonts-liberation fonts-noto-core fonts-noto-color-emoji fonts-wqy-zenhei \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/engine
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/engine/browsers
COPY package.json package-lock.json ./
# Chromium at the version this playwright-core pins, without the headless shell
# (the engine runs headed). The build fails if a library it needs is missing.
RUN npm ci --omit=dev --no-audit --no-fund \
 && node node_modules/playwright-core/cli.js install --no-shell chromium \
 && chrome=$(node -e "import('playwright-core').then(m => console.log(m.chromium.executablePath()))") \
 && test -x "$chrome" \
 && ! ldd "$chrome" | grep 'not found' \
 && npm cache clean --force && rm -rf /tmp/* /root/.npm
# Managed policies: the only path Playwright's Chromium build reads.
COPY policies.json /etc/opt/chrome_for_testing/policies/managed/lollo.json
COPY launcher.mjs healthcheck.mjs entrypoint.sh ./
RUN chmod 644 /etc/opt/chrome_for_testing/policies/managed/lollo.json launcher.mjs healthcheck.mjs \
 && chmod 755 entrypoint.sh \
 && groupadd --system --gid 10003 browser \
 && useradd --system --uid 10003 --gid 10003 --no-create-home --shell /usr/sbin/nologin browser
# The root is read-only and /tmp a tmpfs: Chromium, Xvnc and fontconfig write
# only under HOME.
ENV HOME=/tmp DISPLAY=:99 NODE_ENV=production
USER 10003
EXPOSE 3000 5900
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --start-interval=2s \
  CMD ["node", "/opt/engine/healthcheck.mjs"]
ENTRYPOINT ["/usr/bin/tini", "--", "/opt/engine/entrypoint.sh"]

FROM base AS test
COPY tests/*.mjs ./tests/

FROM base
