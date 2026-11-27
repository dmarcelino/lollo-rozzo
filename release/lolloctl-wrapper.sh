#!/usr/bin/env bash
# lolloctl for lollo-rozzo @VERSION@: runs lolloctl from its published
# image, pinned by digest (release/README.md). publish.yml fills in the three
# values below and attaches the result to the release as `lolloctl`.
#
# It lives in <data>/bin/, and the directory above is the data directory
# (config.env, stacks.conf, stacks/...). Every host path is mounted into the
# container at the same path: Docker reads the paths lolloctl gives it on the
# host, and lolloctl checks this (check_mounts).
#
# It runs on Linux and on macOS, where Docker runs in a VM (Colima, Docker
# Desktop, OrbStack) that shares the home at the same path.
set -euo pipefail
VERSION=@VERSION@
IMAGE=@IMAGE@
REPOSITORY=@REPOSITORY@

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}
warn() { printf 'warning: %s\n' "$*" >&2; }

# macOS ships bash 3.2, which stops at the first mapfile.
((BASH_VERSINFO[0] >= 4)) || die "this needs bash 4 or later, not $BASH_VERSION (macOS: brew install bash, and put it first on your PATH)"

# file_stat GNU_FORMAT BSD_FORMAT PATH -> GNU stat's answer, else BSD stat's (macOS)
file_stat() { stat -c "$1" -- "$3" 2>/dev/null || stat -f "$2" -- "$3"; }

# existing PATH -> PATH with every symlink resolved, when it exists (realpath -e
# is GNU only; readlink -f is on macOS 12.3 and later too)
existing() {
  local p
  p=$(readlink -f -- "$1" 2>/dev/null) && [[ -e $p ]] && printf '%s\n' "$p"
}

self=$(readlink -f -- "${BASH_SOURCE[0]}")
bin=$(dirname -- "$self")
data=$(dirname -- "$bin")
install_hint="create the data directory once and put this file in its bin/:
  install -d -m 700 ~/lollo-rozzo ~/lollo-rozzo/bin
  mv lolloctl ~/lollo-rozzo/bin/ && chmod 755 ~/lollo-rozzo/bin/lolloctl
  ln -s ~/lollo-rozzo/bin/lolloctl ~/.local/bin/lolloctl   # or another directory on your PATH"
[[ ${bin##*/} == bin && $data != / && $data != "$HOME" ]] || die "this wrapper must be in <data directory>/bin/; $install_hint"
[[ -O $data && -w $data ]] || die "$data must be owned by you and writable; $install_hint"

# Run on the host by `self-upgrade`, with the new wrapper: its image must be
# pullable before it replaces the current one.
if [[ ${1:-} == _pull ]]; then
  exec docker pull -q "$IMAGE"
fi
if [[ ${1:-} == wrapper-version ]]; then
  printf 'lollo-rozzo %s, %s, from github.com/%s\n' "$VERSION" "$IMAGE" "$REPOSITORY"
  exit 0
fi

# vm_socket_gid SOCK -> the group of SOCK inside Docker's VM, read there by
# this release's image. That costs a container, so it is kept in <data>/.docker-gid
# for as long as the daemon is the same one: a new VM is a new daemon (its ID),
# whose socket may have another group. lolloctl runs as you, so the group must
# be able to write the socket.
vm_socket_gid() {
  local cache=$data/.docker-gid saved="" gid="" mode=""
  [[ ! -f $cache ]] || read -r saved gid <"$cache" || true
  if [[ $saved == "$docker_id" && $gid =~ ^[0-9]+$ ]]; then
    printf '%s\n' "$gid"
    return
  fi
  read -r gid mode < <(docker run --rm --entrypoint stat -v "$1:$1" "$IMAGE" -c '%g %a' "$1" </dev/null) || true
  [[ $gid =~ ^[0-9]+$ && $mode =~ ^[0-7]+$ ]] ||
    die "could not read the Docker socket's group in Docker's VM; is Docker (Colima, Docker Desktop, OrbStack) running?"
  (((8#$mode & 8#060) == 8#060)) ||
    die "Docker's socket in its VM ($1) is mode $mode, group $gid: that group cannot write it, so lolloctl, which runs as you, cannot use Docker"
  printf '%s %s\n' "$docker_id" "$gid" >"$cache"
  printf '%s\n' "$gid"
}

# The Docker socket as the daemon sees it, which is what a bind mount names,
# and its group. On Linux that is the client's socket. On macOS the client
# reaches the VM through its Docker context, and inside the VM the socket is
# /var/run/docker.sock in Colima, Docker Desktop and OrbStack alike; its group
# is the VM's (vm_socket_gid).
os=$(uname -s)
sock=/var/run/docker.sock
docker_id="" docker_name=""
if [[ $os == Darwin ]]; then
  read -r docker_id docker_name < <(docker info --format '{{.ID}} {{.Name}}' 2>/dev/null </dev/null) || true
  [[ -n $docker_id ]] || die "could not reach Docker; is Docker (Colima, Docker Desktop, OrbStack) running?"
  gid=$(vm_socket_gid "$sock")
else
  if [[ -n ${DOCKER_HOST:-} ]]; then
    [[ $DOCKER_HOST == unix://* ]] || die "DOCKER_HOST must be a unix:// socket; lolloctl mounts it into its container"
    sock=${DOCKER_HOST#unix://}
  fi
  [[ -S $sock ]] || die "no Docker socket at $sock"
  gid=$(file_stat %g %g "$sock")
fi

# --init: as PID 1, lolloctl would ignore the signal Docker forwards on
# Ctrl-C. --user: what it writes is yours. HOME: the Docker CLI and Buildx
# write their settings there, and nothing there outlives the command.
args=(run --rm -i --init --user "$(id -u):$(id -g)" --group-add "$gid"
  --tmpfs /tmp -e HOME=/tmp -e TERM -e TZ -e LOLLO_DATA="$data"
  -v "$sock:$sock" -e DOCKER_HOST="unix://$sock"
  -v "$data:$data")
# A terminal only when stdout is one too, so a pipe gets clean output; with only
# stdin a terminal (`lolloctl apply | tee log`), lolloctl is told, so it
# still asks before it changes anything (stdin_is_terminal).
if [[ -t 0 && -t 1 ]]; then
  args+=(-t)
elif [[ -t 0 ]]; then
  args+=(-e LOLLOCTL_STDIN_TTY=1)
fi
# Relative paths in the arguments mean the same inside.
args+=(-w "$PWD")

# colima_profile -> the Colima profile Docker runs in, or nothing when Docker's VM
# is not Colima's
colima_profile() {
  case $docker_name in
    colima) printf default ;;
    colima-*) printf '%s' "${docker_name#colima-}" ;;
  esac
}

# vm_shares DIR -> dies unless Docker's VM sees DIR, on Colima. It shares only
# the home (and what colima.yaml's `mounts` add), and for any other path Docker
# binds an empty directory it creates in the VM, without a word: a backup would
# land there. Docker Desktop and OrbStack refuse a path they do not share.
vm_shares() {
  local profile
  profile=$(colima_profile)
  [[ -n $profile ]] || return 0
  colima ssh --profile "$profile" -- test -d "$1" </dev/null >/dev/null 2>&1 ||
    die "$1 is not shared with Colima's VM ($profile): Docker would use an empty directory inside the VM instead. Use a directory under your home, or add it to colima.yaml's mounts (colima stop, then colima start --edit)"
}

# mount_same PATH [ro] -> mounts PATH (an existing directory) at the same path
mount_same() {
  local p
  p=$(existing "$1") || return 0
  [[ -d $p ]] || return 0
  [[ $p != *[:,]* ]] || die "$p cannot contain ':' or ','"
  [[ $os != Darwin ]] || vm_shares "$p"
  args+=(-v "$p:$p${2:+:$2}")
}

# positional ARGS... -> the arguments that are not options, one per line
# (backup's --keep takes a value)
positional() {
  while (($#)); do
    case $1 in
      --keep) shift ;;
      -*) ;;
      *) printf '%s\n' "$1" ;;
    esac
    shift || true
  done
}

# mount_ro_outside_home PATH -> mounts PATH (a file or directory) read-only at
# the same path, unless it is under $HOME, which toolbox_host mounts whole
mount_ro_outside_home() {
  local p
  p=$(existing "$1") || return 0
  [[ $p != "$HOME" && $p != "$HOME"/* ]] || return 0
  [[ $p != *[:,]* ]] || die "$p cannot contain ':' or ','"
  [[ $os != Darwin ]] || vm_shares "$(dirname -- "$p")"
  args+=(-v "$p:$p:ro")
}

# The ssh tunnel colima_agent opens, closed when this wrapper exits.
tunnel_pid="" tunnel_cfg="" tunnel_host="" tunnel_sock=""
close_tunnel() {
  [[ -z $tunnel_pid ]] || kill "$tunnel_pid" 2>/dev/null || true
  # sshd leaves the socket behind.
  [[ -z $tunnel_host ]] || ssh -F "$tunnel_cfg" "$tunnel_host" rm -f "$tunnel_sock" </dev/null >/dev/null 2>&1 || true
  [[ -z $tunnel_cfg ]] || rm -f "$tunnel_cfg"
  # Once: vm_agent closes a failed tunnel itself, and the EXIT trap runs after.
  tunnel_pid="" tunnel_host="" tunnel_cfg=""
}

# colima_agent PROFILE VMPATH -> serves the ssh agent at VMPATH inside Colima's
# VM, through an ssh tunnel into it, until this wrapper exits. Colima's own
# forwardAgent reaches only `colima ssh` sessions, never a container.
colima_agent() {
  local host i
  tunnel_cfg=$(mktemp)
  trap close_tunnel EXIT
  colima ssh-config --profile "$1" >"$tunnel_cfg" 2>/dev/null </dev/null || return 1
  host=$(awk '$1 == "Host" { print $2; exit }' "$tunnel_cfg")
  [[ -n $host ]] || return 1
  # The VM's sshd decides whether a -R socket replaces an old one at the path
  # (StreamLocalBindUnlink there; off by default), and an old one would pass
  # the wait below: it goes first.
  ssh -F "$tunnel_cfg" "$host" rm -f "$2" </dev/null >/dev/null 2>&1 || return 1
  tunnel_host=$host tunnel_sock=$2
  # Lima's ssh config shares one master connection (ControlMaster), which would
  # take over the forward and outlive this process: the tunnel gets its own.
  ssh -F "$tunnel_cfg" -N -o ExitOnForwardFailure=yes -o ControlMaster=no -o ControlPath=none \
    -R "$2:$SSH_AUTH_SOCK" "$host" </dev/null >/dev/null 2>&1 &
  tunnel_pid=$!
  for ((i = 0; i < 50; i++)); do
    kill -0 "$tunnel_pid" 2>/dev/null || return 1
    ssh -F "$tunnel_cfg" "$host" test -S "$2" </dev/null >/dev/null 2>&1 && return 0
    sleep 0.2
  done
  return 1
}

# vm_agent -> on macOS, the ssh agent at a path inside Docker's VM, mounted at
# the same path: Docker Desktop and OrbStack forward it at a fixed one; Colima
# has none, so colima_agent opens one for this run. Docker Desktop and OrbStack
# forward the agent macOS starts at login, not one SSH_AUTH_SOCK points at
# (1Password, Secretive, gpg-agent); Colima's tunnel forwards SSH_AUTH_SOCK's.
vm_agent() {
  local name=$docker_name profile vm
  case $name in
    docker-desktop | orbstack) vm=/run/host-services/ssh-auth.sock ;;
    colima | colima-*)
      profile=$(colima_profile)
      vm=/tmp/lolloctl-agent-$$-$RANDOM.sock
      if ! colima_agent "$profile" "$vm"; then
        close_tunnel
        warn "toolbox: could not open an ssh tunnel into Colima's VM ($profile); no ssh agent"
        return 0
      fi
      ;;
    *)
      warn "toolbox: no ssh agent, which needs Colima, Docker Desktop or OrbStack on macOS (Docker's VM is '${name:-unknown}')"
      return 0
      ;;
  esac
  args+=(-v "$vm:$vm" -e SSH_AUTH_SOCK="$vm")
}

# toolbox_host ARGS... -> what `toolbox` takes from the host: the home (read-only,
# for the files it mounts), and the ssh agent and the tokens of the credentials
# the run chose, by flag (--gh, --ssh, --claude in ARGS, before the stack name)
# or by the toolbox file's `use` lines (unless --no-credentials): the grammar
# lolloctl's cmd_toolbox reads. Nothing is fetched for a credential the run did
# not choose. Also the toolbox file's `secret` values, which only the host's
# commands can give. Values go by name (-e NAME), never in argv.
toolbox_host() {
  local file line w name a val k kinds="" no_credentials=""
  while [[ ${1:-} == --* ]]; do
    case $1 in
      --gh | --ssh | --claude) kinds+=" ${1#--}" ;;
      --no-credentials) no_credentials=1 ;;
    esac
    shift
  done
  file=${XDG_CONFIG_HOME:-$HOME/.config}/lolloctl/toolbox
  if [[ -f $file ]]; then
    # The same checks as lolloctl's toolbox_personal, but before anything runs:
    # the `secret` commands run here, as you.
    [[ -O $file ]] || die "$file is not owned by you"
    (((8#$(file_stat %a %Lp "$file") & 8#022) == 0)) || die "$file is writable by others (chmod 600 $file)"
    if [[ -z $no_credentials ]]; then
      while IFS= read -r line || [[ -n $line ]]; do
        read -ra w <<<"$line"
        [[ ${#w[@]} -ge 2 && ${w[0]} == use ]] || continue
        # lolloctl refuses an unknown kind; here it only goes unused.
        for k in "${w[@]:1}"; do kinds+=" $k"; done
      done <"$file"
    fi
  fi
  args+=(-v "$HOME:$HOME:ro" -e LOLLOCTL_HOST_HOME="$HOME" -e XDG_CONFIG_HOME -e CLAUDE_CONFIG_DIR)
  # The Claude config and the toolbox file may live outside the home; just them,
  # read-only, or lolloctl would not find them in its container.
  [[ -z ${CLAUDE_CONFIG_DIR:-} ]] || mount_ro_outside_home "$CLAUDE_CONFIG_DIR"
  mount_ro_outside_home "$file"
  if [[ " $kinds " == *" ssh "* && -n ${SSH_AUTH_SOCK:-} && -S $SSH_AUTH_SOCK ]]; then
    if [[ $os == Darwin ]]; then
      vm_agent
    else
      args+=(-v "$SSH_AUTH_SOCK:$SSH_AUTH_SOCK" -e SSH_AUTH_SOCK)
    fi
  fi
  if [[ " $kinds " == *" gh "* ]] && command -v gh >/dev/null; then
    LOLLOCTL_GH_TOKEN=$(gh auth token 2>/dev/null </dev/null) || LOLLOCTL_GH_TOKEN=""
    export LOLLOCTL_GH_TOKEN
    args+=(-e LOLLOCTL_GH_TOKEN)
  fi
  # The Claude token: the caller's, or from the keyring (libsecret on Linux,
  # the login keychain on macOS, both under the service name claude-toolbox).
  if [[ " $kinds " == *" claude "* ]]; then
    args+=(-e CLAUDE_CODE_OAUTH_TOKEN)
    if [[ -z ${CLAUDE_CODE_OAUTH_TOKEN:-} ]]; then
      if [[ $os == Darwin ]]; then
        LOLLOCTL_CLAUDE_TOKEN=$(security find-generic-password -s claude-toolbox -w 2>/dev/null </dev/null) || LOLLOCTL_CLAUDE_TOKEN=""
        export LOLLOCTL_CLAUDE_TOKEN
        args+=(-e LOLLOCTL_CLAUDE_TOKEN)
      elif command -v secret-tool >/dev/null; then
        LOLLOCTL_CLAUDE_TOKEN=$(secret-tool lookup service claude-toolbox 2>/dev/null </dev/null) || LOLLOCTL_CLAUDE_TOKEN=""
        export LOLLOCTL_CLAUDE_TOKEN
        args+=(-e LOLLOCTL_CLAUDE_TOKEN)
      fi
    fi
  fi
  # The toolbox file's `secret NAME COMMAND [ARG...]` lines; lolloctl checks
  # the rest of the file, and refuses a secret it gets no value for.
  [[ -f $file ]] || return 0
  while IFS= read -r line || [[ -n $line ]]; do
    read -ra w <<<"$line"
    [[ ${#w[@]} -ge 3 && ${w[0]} == secret && ${w[1]} =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    name=${w[1]}
    a=()
    for val in "${w[@]:2}"; do
      # shellcheck disable=SC2088 # matching a literal ~ from the toolbox file
      case $val in
        "~") a+=("$HOME") ;;
        "~/"*) a+=("$HOME/${val#"~/"}") ;;
        *) a+=("$val") ;;
      esac
    done
    val=$("${a[@]}" </dev/null) || die "$file: secret $name: '${a[*]}' failed"
    export "LOLLOCTL_SECRET_$name=$val"
    args+=(-e "LOLLOCTL_SECRET_$name")
  done <"$file"
}

case ${1:-} in
  backup)
    mapfile -t pos < <(positional "${@:2}")
    [[ -z ${pos[1]:-} ]] || mount_same "${pos[1]}"
    ;;
  restore)
    # The backup, read-only: a snapshot directory, or the directory of an archive.
    mapfile -t pos < <(positional "${@:2}")
    if [[ -n ${pos[1]:-} ]]; then
      if [[ -d ${pos[1]} ]]; then
        mount_same "${pos[1]}" ro
      else
        mount_same "$(dirname -- "${pos[1]}")" ro
      fi
    fi
    ;;
  receipts)
    mapfile -t pos < <(positional "${@:2}")
    [[ -z ${pos[1]:-} ]] || mount_same "$(dirname -- "${pos[1]}")"
    ;;
  toolbox) toolbox_host "${@:2}" ;;
  self-upgrade)
    # The current image fetches the new wrapper and checks its sum; the new
    # wrapper pulls its image; only then does it replace this one, which is
    # kept as lolloctl.previous to go back to.
    (($# == 2)) || die "usage: lolloctl self-upgrade vX.Y.Z"
    docker "${args[@]}" "$IMAGE" _fetch-wrapper "$2"
    "$BASH" "$bin/.lolloctl.new" _pull >/dev/null || {
      rm -f "$bin/.lolloctl.new"
      die "could not pull the image of release $2; this install is unchanged"
    }
    cp -p -- "$self" "$bin/lolloctl.previous"
    chmod 755 "$bin/.lolloctl.new"
    mv -f -- "$bin/.lolloctl.new" "$bin/lolloctl"
    printf 'lollo-rozzo %s -> %s (the previous wrapper is %s)\n' "$VERSION" "$2" "$bin/lolloctl.previous" >&2
    exec "$bin/lolloctl" _after-upgrade
    ;;
esac

# With a tunnel open, docker runs as a child, so the EXIT trap can close it.
if [[ -n $tunnel_pid ]]; then
  rc=0
  docker "${args[@]}" "$IMAGE" "$@" || rc=$?
  exit "$rc"
fi
exec docker "${args[@]}" "$IMAGE" "$@"
