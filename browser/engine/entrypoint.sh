#!/bin/bash
# lollo-browser-engine: writes the VNC password from the engine secret into
# /tmp, starts TigerVNC's Xvnc on display :99 (VNC on 5900, X11 not on TCP),
# waits for its socket, then runs the launcher (launcher.mjs), passing on any
# argument so the launcher can refuse it. /tmp is a tmpfs, so nothing here
# survives a restart. Never prints the secret: printf is a builtin, so the
# password is in no command line either.
set -euo pipefail
umask 077

secret=${BROWSER_ENGINE_SECRET:-}
if [[ ! $secret =~ ^[A-Za-z0-9_-]{32,}$ ]]; then
  echo "lollo-browser-engine: BROWSER_ENGINE_SECRET must be at least 32 characters of A-Z, a-z, 0-9, _ and -" >&2
  exit 2
fi

# VNC authentication uses at most 8 characters of a password.
mkdir -p /tmp/vnc
printf '%s\n' "${secret:0:8}" | vncpasswd -f >/tmp/vnc/passwd
unset secret

rm -f /tmp/.X99-lock /tmp/.X11-unix/X99
mkdir -m 1777 /tmp/.X11-unix 2>/dev/null || true
Xvnc :99 -geometry 1280x900 -depth 24 -dpi 96 -desktop lollo-browser \
  -rfbport 5900 -SecurityTypes VncAuth -PasswordFile /tmp/vnc/passwd \
  -nolisten tcp &
xvnc=$!
for _ in $(seq 100); do
  [[ -S /tmp/.X11-unix/X99 ]] && break
  kill -0 "$xvnc" 2>/dev/null || break
  sleep 0.1
done
if [[ ! -S /tmp/.X11-unix/X99 ]]; then
  echo "lollo-browser-engine: Xvnc did not start" >&2
  exit 1
fi

export DISPLAY=:99
exec node /opt/engine/launcher.mjs "$@"
