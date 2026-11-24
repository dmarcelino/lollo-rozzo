# The Outlook lollo sidecar (see docs/outlook.md). Built from a clone by
# lolloctl via compose/outlook.yml, published for a release (release/images). `tests/run-sidecar.sh outlook` builds the `test` stage and
# runs it. Both need the `lollo-common` build context (the repo's lollo-common/).
FROM python:3.14-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
COPY --from=lollo-common . /tmp/lollo-common
RUN pip install --root-user-action=ignore /tmp/lollo-common && rm -rf /tmp/lollo-common
WORKDIR /app
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
RUN groupadd --system --gid 10001 outlook \
 && useradd --system --uid 10001 --gid 10001 --no-create-home outlook \
 && mkdir /data && chown 10001:10001 /data && chmod 700 /data
USER 10001
VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"]
CMD ["python", "-m", "lollo_outlook"]
