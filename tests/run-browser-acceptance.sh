#!/usr/bin/env bash
# The browser's acceptance suite (browser/acceptance/): both browser images and
# pipelock with the repo's own config, wired like compose/browser.yml, against
# hostile fixture pages, on internal Docker networks only, so it needs Docker
# (Linux with unprivileged user namespaces, like browser/engine/tests/run.sh)
# but not the internet once the images are built.
#
#   default  internal: lollo-browser, lollo-pipelock, the agent's MCP client,
#            stand-ins for the vault (lollo-vault) and the BFF (bff,
#            app-server) that log every connection, and a DNS server
#   browser  internal: the engine, lollo-browser and lollo-pipelock only
#   net      internal, on a 198.18.x.0/24 subnet (benchmarking addresses,
#            never routed on the internet), which pipelock counts as public:
#            the fixture web server, under every fixture site's name
#
# Pipelock runs its shipped config unchanged, with no trusted_domains, and
# resolves names through Docker's DNS, then the DNS server (rb.test rebinds
# from the fixture's address to the vault stand-in's). The engine runs with
# compose/browser.yml's hardening and seccomp profile; the one addition is a
# managed policy file, mounted next to the image's, whose CACertificates
# trusts the suite's own test CA, so the fixture sites are real HTTPS. A
# tcpdump shares the engine's network namespace for the whole run, and a
# listener shares pipelock's to catch anything sent straight to its address.
#
# Checks: no page request reaches a stack service, a private, loopback or
# link-local address, the engine's own ports or a rebinding name; WebRTC and
# QUIC send nothing by UDP; typed secrets and one-time codes appear in no tool
# result, log line or audit entry, and screenshots mask their fields; the site
# check refuses look-alikes; tasks share no cookies or storage; the engine
# cannot call lollo-browser or reach anything but pipelock; pipelock signs
# receipts for the browser's requests (and the requests per page load are
# printed). Containers, networks and volumes are named
# lollo-browser-acc-<pid>-* and removed on exit; KEEP=1 keeps them.
#   tests/run-browser-acceptance.sh
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
HELPER=lollo-browser-acceptance:helper
ENGINE=lollo-browser-engine:acceptance
SERVICE=lollo-browser:acceptance
PIPELOCK=lollo-pipelock:acceptance
id=lollo-browser-acc-$$
POLICY_DIR=/etc/opt/chrome_for_testing/policies/managed

containers=() networks=() volumes=()
# shellcheck disable=SC2329 # cleanup is called by the trap
cleanup() {
  if [[ ${KEEP:-} == 1 ]]; then
    echo "KEEP=1: left $id-* running" >&2
    return 0
  fi
  ((${#containers[@]})) && docker rm -f "${containers[@]}" >/dev/null 2>&1
  ((${#networks[@]})) && docker network rm "${networks[@]}" >/dev/null 2>&1
  ((${#volumes[@]})) && docker volume rm "${volumes[@]}" >/dev/null 2>&1
  return 0
}
trap cleanup EXIT

failed=0
ok() { printf 'ok    %s\n' "$1"; }
fail() {
  printf 'FAIL  %s\n' "$1"
  failed=1
}
# none NAME OUTPUT: ok when the output is empty, else FAIL with it.
none() {
  local name=$1 out=$2
  if [[ -z $out ]]; then ok "$name"; else fail "$name: ${out:0:900}"; fi
}
ip_of() { docker inspect -f "{{(index .NetworkSettings.Networks \"$2\").IPAddress}}" "$1"; }
helper() { docker run --rm "$@"; }

echo "building images..."
docker build -q -t "$ENGINE" "$ROOT/browser/engine" >/dev/null
docker build -q --build-context "lollo-common=$ROOT/lollo-common" -t "$SERVICE" "$ROOT/browser/service" >/dev/null
docker build -q -t "$PIPELOCK" "$ROOT/pipelock" >/dev/null
docker build -q -t "$HELPER" "$ROOT/browser/acceptance" >/dev/null

# Secrets reach containers as `-e NAME`, never in an argument.
rand() { head -c "$1" /dev/urandom | base64 | LC_ALL=C tr -dc 'A-Za-z0-9' | head -c "$2"; }
BROWSER_ENGINE_SECRET=$(rand 48 40)
ACC_SECRET="Pw-$(rand 24 16)!"
ACC_CARD="4$(head -c 64 /dev/urandom | od -An -tu1 | LC_ALL=C tr -dc '0-9' | head -c 15)"
ACC_TOTP_KEY=$(head -c 400 /dev/urandom | LC_ALL=C tr -dc 'A-Z2-7' | head -c 32)
export BROWSER_ENGINE_SECRET ACC_SECRET ACC_CARD ACC_TOTP_KEY

docker network create --internal "$id-default" >/dev/null && networks+=("$id-default")
docker network create --internal "$id-browser" >/dev/null && networks+=("$id-browser")
for _ in $(seq 20); do
  subnet=198.18.$((RANDOM % 256)).0/24
  docker network create --internal --subnet "$subnet" "$id-net" >/dev/null 2>&1 && networks+=("$id-net") && break
done
[[ ${networks[*]} == *"$id-net"* ]] || { echo "could not create the fixture network" >&2; exit 1; }
for v in certs log pipelock data out; do
  docker volume create "$id-$v" >/dev/null && volumes+=("$id-$v")
done

helper -v "$id-certs:/certs" "$HELPER" python /acc/fixture.py certs
helper -v "$id-log:/log" "$HELPER" sh -c 'touch /log/hits.log && chmod 644 /log/hits.log'

# run NAME DOCKER-RUN-ARGS... : a detached container, removed on exit.
run() {
  local name=$id-$1
  shift
  containers+=("$name")
  docker run -d --name "$name" "$@" >/dev/null
}
run vault --network "$id-default" --network-alias lollo-vault -v "$id-log:/log" \
  "$HELPER" python /acc/fixture.py witness lollo-vault 80,443,8000 3478
run bff --network "$id-default" --network-alias bff --network-alias app-server -v "$id-log:/log" \
  "$HELPER" python /acc/fixture.py witness bff 80,443,3000,8080 3478
VAULT_IP=$(ip_of "$id-vault" "$id-default")
BFF_IP=$(ip_of "$id-bff" "$id-default")
names=(shop.acme-shop.test login.acme-shop.test login.acme-sh0p.test acme-shop.test.evil.test
  cdn.static-cdn.test img.static-cdn.test)
aliases=()
for n in "${names[@]}"; do aliases+=(--network-alias "$n"); done
run web --network "$id-net" "${aliases[@]}" -e VAULT_IP="$VAULT_IP" -e BFF_IP="$BFF_IP" \
  -v "$id-certs:/certs:ro" -v "$id-log:/log" "$HELPER" python /acc/fixture.py web
WEB_IP=$(ip_of "$id-web" "$id-net")
run dns --network "$id-default" -v "$id-log:/log" "$HELPER" python /acc/fixture.py dns "$WEB_IP" "$VAULT_IP"
DNS_IP=$(ip_of "$id-dns" "$id-default")

# Pipelock as compose/pipelock.yml and browser.yml wire it: the stack's
# network and `browser`, under the name lollo-pipelock; here also `net`.
containers+=("$id-pipelock")
docker create --name "$id-pipelock" --network "$id-default" --network-alias lollo-pipelock --dns "$DNS_IP" \
  -v "$id-pipelock:/data" "$PIPELOCK" >/dev/null
docker network connect --alias lollo-pipelock "$id-browser" "$id-pipelock"
docker network connect "$id-net" "$id-pipelock"
docker start "$id-pipelock" >/dev/null
PIPELOCK_IP=$(ip_of "$id-pipelock" "$id-browser")
run pipelock-witness --network "container:$id-pipelock" -v "$id-log:/log" \
  "$HELPER" python /acc/fixture.py witness pipelock-netns 80,443,3000,5900,8000 443,3478

# The engine as compose/browser.yml runs it, plus the test CA's policy file.
containers+=("$id-engine")
docker create --name "$id-engine" --network "$id-browser" --network-alias lollo-browser-engine \
  --read-only --tmpfs /tmp --shm-size 512m --cap-drop ALL --security-opt no-new-privileges:true \
  --security-opt "seccomp=$ROOT/browser/engine/seccomp.json" --memory 2g --pids-limit 512 \
  -e BROWSER_ENGINE_SECRET -e BROWSER_PROXY=http://lollo-pipelock:8888 -e TZ=UTC \
  --mount "type=volume,src=$id-certs,dst=$POLICY_DIR/acceptance-ca.json,volume-subpath=policy.json,readonly" \
  "$ENGINE" >/dev/null
docker start "$id-engine" >/dev/null
# tcpdump needs CHOWN, SETUID and SETGID to hand its file to its own user.
run capture --network "container:$id-engine" --cap-drop ALL --cap-add NET_RAW --cap-add NET_ADMIN \
  --cap-add CHOWN --cap-add SETUID --cap-add SETGID \
  -v "$id-log:/log" "$HELPER" tcpdump -i any -nn -s 0 -U -Z root -w /log/engine.pcap
ENGINE_IP=$(ip_of "$id-engine" "$id-browser")

health=starting
for _ in $(seq 90); do
  health=$(docker inspect -f '{{if .State.Running}}{{.State.Health.Status}}{{else}}exited{{end}}' "$id-engine")
  [[ $health == starting ]] || break
  sleep 1
done
if [[ $health != healthy ]]; then
  echo "the engine is not healthy ($health):" >&2
  docker logs "$id-engine" 2>&1 | tail -30 >&2
  exit 1
fi

containers+=("$id-service")
docker create --name "$id-service" --network "$id-default" --network-alias lollo-browser \
  --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges:true --memory 512m --pids-limit 128 \
  -v "$id-data:/data" \
  -e BROWSER_ENGINE_URL=ws://lollo-browser-engine:3000 -e BROWSER_ENGINE_SECRET \
  -e BROWSER_VNC=lollo-browser-engine:5900 -e BROWSER_VAPID_PUBLIC_KEY=acceptance-public \
  -e BROWSER_VAPID_PRIVATE_KEY=acceptance-private -e PUSH_VAPID_CONTACT_EMAIL=alfred@gmail.com \
  -e CF_ACCESS_TEAM_DOMAIN=team1 -e CF_ACCESS_AUD=aud2 -e ALLOWED_USERS=alfred@gmail.com \
  -e PUBLIC_ORIGIN=https://alfred-lollo.example.com -e TZ=UTC \
  "$SERVICE" >/dev/null
docker network connect --alias lollo-browser "$id-browser" "$id-service"
docker start "$id-service" >/dev/null

client() {
  docker run --rm --network "$id-default" "$HELPER" python -c "$1"
}
up=
for _ in $(seq 60); do
  up=$(client 'import urllib.request as u; print(u.urlopen("http://lollo-browser:8000/healthz", timeout=3).read().decode())' 2>/dev/null || true)
  [[ $up == ok ]] && break
  sleep 1
done
if [[ $up != ok ]]; then
  echo "lollo-browser did not connect to the engine (${up:-no answer}):" >&2
  docker logs "$id-service" 2>&1 | tail -30 >&2
  exit 1
fi
echo "stack up: engine $ENGINE_IP, pipelock $PIPELOCK_IP (browser net), fixture $WEB_IP, vault stand-in $VAULT_IP"

# The agent's side: the MCP client on the stack network (browser/acceptance/client.py).
containers+=("$id-client")
rc=0
docker run --name "$id-client" --network "$id-default" -e ACC_SECRET -e ACC_CARD -e ACC_TOTP_KEY \
  -v "$id-out:/out" -v "$id-log:/log:ro" "$HELPER" python /acc/client.py || rc=$?
((rc == 0)) || failed=1

# The capture ends here: what follows makes the engine connect on purpose.
sleep 3
docker stop "$id-capture" >/dev/null

# 6. A compromised engine: anything it runs itself reaches only pipelock and
# gets nothing from lollo-browser.
engine_probe=$(docker exec -e VAULT_IP="$VAULT_IP" -e WEB_IP="$WEB_IP" "$id-engine" node --input-type=module -e '
const t = (ms) => AbortSignal.timeout(ms);
async function get(url, init = {}) {
  try {
    const r = await fetch(url, {...init, signal: t(4000)});
    return `${r.status} ${(await r.text()).slice(0, 60).replace(/\s+/g, " ")}`;
  } catch (e) { return `unreachable (${e.cause?.code ?? e.name})`; }
}
function ws(url) {
  return new Promise((done) => {
    const s = new WebSocket(url);
    s.onopen = () => { s.close(); done("open"); };
    s.onerror = () => done("refused");
    setTimeout(() => done("timeout"), 4000);
  });
}
const mcp = {method: "POST", headers: {"content-type": "application/json", accept: "application/json, text/event-stream",
  "mcp-protocol-version": "2025-06-18"}, body: JSON.stringify({jsonrpc: "2.0", id: 1, method: "tools/list"})};
console.log("service /mcp:", await get("http://lollo-browser:8000/mcp", mcp));
console.log("service /browser/:", await get("http://lollo-browser:8000/browser/"));
console.log("service /healthz:", await get("http://lollo-browser:8000/healthz"));
console.log("service /browser/live:", await ws("ws://lollo-browser:8000/browser/live"));
console.log("direct lollo-vault:", await get("http://lollo-vault:8000/acc-probe-engine-direct"));
console.log("direct vault address:", await get(`http://${process.env.VAULT_IP}:8000/acc-probe-engine-direct`));
console.log("direct fixture:", await get(`https://shop.acme-shop.test/acc-probe-engine-direct`));
console.log("direct fixture address:", await get(`http://${process.env.WEB_IP}/acc-probe-engine-direct`));
' 2>&1 || true)
printf '%s\n' "$engine_probe" | sed 's/^/info  engine: /'
for route in /mcp /browser/ /healthz; do
  line=$(grep "^service $route:" <<<"$engine_probe" || true)
  if [[ $line == *": 403 "*'"refused"'* ]]; then ok "engine: lollo-browser refuses $route from the engine's network"
  else fail "engine: $route from the engine: ${line:-no answer}"; fi
done
if grep -q '^service /browser/live: refused' <<<"$engine_probe"; then
  ok "engine: lollo-browser refuses the live view's WebSocket from the engine's network"
else fail "engine: /browser/live from the engine: $(grep live <<<"$engine_probe")"; fi
direct=$(grep '^direct' <<<"$engine_probe" | grep -v ': unreachable' || true)
none "engine: outside Chromium, the engine reaches no stack service and nothing on the fixture network" "$direct"

# 1 and 2: what the targets and the engine's packets show.
none "unreachable: the vault and BFF stand-ins got no connection" \
  "$(docker logs "$id-vault" 2>&1 | grep WITNESS-HIT; docker logs "$id-bff" 2>&1 | grep WITNESS-HIT)"
none "unreachable: nothing reached pipelock's own address but its proxy port (UDP included)" \
  "$(docker logs "$id-pipelock-witness" 2>&1 | grep WITNESS-HIT)"
# rb.test's first answer is the fixture's public address: a probe reaching the
# fixture under that name is the public phase, not a leak.
none "unreachable: the fixture got no probe meant for a stack or private address" \
  "$(docker logs "$id-web" 2>&1 | grep 'acc-probe' | grep -v '^HIT http rb\.test ' || true)"
info_dns=$(docker logs "$id-dns" 2>&1 | grep '^DNS rb.test' | head -4 | tr '\n' ' ')
echo "info  rebinding DNS answers: $info_dns"
# pcap NAME FILTER: the engine's captured packets matching FILTER, as text.
pcap() {
  helper -v "$id-log:/log:ro" "$HELPER" tcpdump -r /log/engine.pcap -nn "${@:2}" 2>/dev/null | head -20
}
total=$(helper -v "$id-log:/log:ro" "$HELPER" sh -c 'tcpdump -r /log/engine.pcap -nn 2>/dev/null | wc -l')
echo "info  packets captured in the engine's network namespace: $total"
if ((total > 100)); then ok "capture: the engine's packets were captured"; else fail "capture: only $total packets captured"; fi
# count FILTER: how many captured packets match FILTER.
# shellcheck disable=SC2016 # $1 expands in the helper's shell
count() { helper -v "$id-log:/log:ro" "$HELPER" sh -c 'tcpdump -r /log/engine.pcap -nn "$1" 2>/dev/null | wc -l' sh "$1"; }
syns=$(count "src host $ENGINE_IP and dst port 8888 and tcp[tcpflags] & tcp-syn != 0 and tcp[tcpflags] & tcp-ack == 0")
dnsq=$(count "udp and dst host 127.0.0.11")
echo "info  capture: $syns connections to pipelock's proxy port, $dnsq DNS queries to Docker's resolver"
if ((syns > 10 && dnsq > 0)); then ok "capture: control: the filters see the engine's connections to pipelock and its DNS queries"
else fail "capture: control: $syns connections to pipelock, $dnsq DNS queries"; fi
none "capture: the engine opened no TCP connection to anything but pipelock's proxy port" \
  "$(pcap x "src host $ENGINE_IP and tcp[tcpflags] & tcp-syn != 0 and tcp[tcpflags] & tcp-ack == 0 and not (dst host $PIPELOCK_IP and dst port 8888)")"
none "capture: the engine sent no UDP but DNS queries to Docker's resolver (no WebRTC, no QUIC)" \
  "$(pcap x "udp and not host 127.0.0.11")"
# The kernel's own neighbour discovery and multicast listener reports go to
# IPv6 multicast addresses (ff00::/8); nothing else may use IPv6.
none "capture: no IPv6 traffic left the engine but the kernel's multicast neighbour discovery" \
  "$(pcap x "ip6 and not host ::1 and not dst net ff00::/8")"
none "capture: no connection to the engine's VNC port over loopback" \
  "$(pcap x "dst host 127.0.0.1 and dst port 5900")"
none "capture: no probe reached the engine's own ports over loopback" \
  "$(pcap x -A "host 127.0.0.1 or host ::1" | grep -a 'acc-probe' || true)"

# 3. The typed values in logs and the audit log.
values=$(helper -v "$id-out:/out:ro" "$HELPER" cat /out/values 2>/dev/null || true)
if [[ -z $values ]]; then
  fail "secrets: the client wrote no values to look for"
else
  audit=$(helper -v "$id-data:/data:ro" "$HELPER" sh -c 'cat /data/audit.jsonl* 2>/dev/null')
  if [[ $audit == *secret_typed* && $audit == *code_typed* ]]; then ok "secrets: the audit log records the typing"
  else fail "secrets: no secret_typed or code_typed entry in the audit log"; fi
  for source in service engine pipelock; do
    logs=$(docker logs "$id-$source" 2>&1)
    hit=$(grep -aiF -f <(printf '%s\n' "$values") <<<"$logs" | head -3 || true)
    none "secrets: no typed value, TOTP key or one-time code in lollo-$source's log" "$hit"
  done
  hit=$(grep -aiF -f <(printf '%s\n' "$values") <<<"$audit" | head -3 || true)
  none "secrets: no typed value, TOTP key or one-time code in the audit log" "$hit"
fi
# 7. Pipelock writes receipts in batches: stop it (which seals the chain) and
# read them all, then verify the chain as `lolloctl receipts` would.
docker stop "$id-pipelock-witness" "$id-pipelock" >/dev/null
rc=0
helper -v "$id-pipelock:/pipelock:ro" -v "$id-out:/out:ro" "$HELPER" python /acc/receipts.py || rc=$?
((rc == 0)) || failed=1
if docker run --rm --network none -v "$id-pipelock:/data:ro" --entrypoint pipelock "$PIPELOCK" verify-receipt \
  --whole-recorder --chain /data/evidence --key /data/keys/receipt-signing.json.pub >/dev/null 2>&1; then
  ok "receipts: the whole chain verifies"
else fail "receipts: the chain does not verify"; fi

if grep -qF "$BROWSER_ENGINE_SECRET" <(docker logs "$id-service" 2>&1; docker logs "$id-engine" 2>&1); then
  fail "the engine secret appears in a log"
else ok "the engine secret appears in no log"; fi

if ((failed)); then
  echo "lollo-browser log (last 40 lines):"
  docker logs "$id-service" 2>&1 | tail -40
fi
exit "$failed"
