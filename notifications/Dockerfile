# The notifications service (see docs/notifications.md): the lollo's push keys,
# the devices' subscriptions and the person's /notifications/ page. Built by
# lolloctl via compose/notifications.yml; tests/run-sidecar.sh builds the `test`
# stage and runs it. Both pass the shared package as the build context named
# `lollo-common`.
FROM python:3.14-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY --from=lollo-common . /tmp/lollo-common
RUN pip install --root-user-action=ignore /tmp/lollo-common && rm -rf /tmp/lollo-common
COPY pyproject.toml ./
COPY src ./src
RUN pip install --root-user-action=ignore .

FROM base AS test
RUN pip install --root-user-action=ignore ".[test]"
COPY tests ./tests
CMD ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider"]

FROM base
# A fixed non-root user that owns /data, so the named volume Docker creates on
# first start inherits that ownership.
RUN groupadd --system --gid 10005 notifications \
 && useradd --system --uid 10005 --gid 10005 --no-create-home notifications \
 && mkdir /data && chown 10005:10005 /data && chmod 700 /data
USER 10005
VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"]
CMD ["python", "-m", "lollo_notifications"]
