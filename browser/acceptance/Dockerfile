# The browser acceptance suite's helper image (tests/run-browser-acceptance.sh):
# the fixture web server that plays the internet, the stand-ins for the
# stack's services, the rebinding DNS server, the engine's packet capture and
# the agent-side MCP client. Test only: never part of a stack or a release.
FROM python:3.14-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends tcpdump && rm -rf /var/lib/apt/lists/*
# Decodes the screenshots to check the masks.
RUN pip install --no-cache-dir --root-user-action=ignore pillow==12.0.0
COPY . /acc
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
