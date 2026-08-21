"""Render the one-shot tailnet join script served to the agent (domain module). Pure; no I/O.

An agent (or a BYO harness we don't launch) fetches this from run-control — ``GET
/runs/<id>/join.sh`` — and runs it: ``curl -fsS -H "Authorization: Bearer <key>" <base>/join.sh
| sh``. The script folds the whole tailnet-join dance the agent used to reconstruct by hand into
one command:

  1. on macOS, start a pinned Linux Tailscale sidecar with loopback-only proxy ports; elsewhere,
     install a version-pinned static client if one isn't already present (no root/apt);
  2. trust the run's control-plane CA when the run is air-gapped (embedded in the script — it is
     served over an authed call); a public control plane (no CA) uses system trust;
  3. join, capability-detecting the sandbox:
       * macOS -> Docker userspace sidecar; targets are reached through its loopback SOCKS5 proxy;
       * real root + a writable /dev/net/tun -> kernel mode; targets are reachable DIRECTLY by IP;
       * otherwise -> userspace-networking + a SOCKS5 proxy on 127.0.0.1:1055; targets are reached
         THROUGH the proxy (the script prints how, and writes a sourceable env file);
  4. run ``up`` with a bounded ``--timeout`` (never the old indefinite block) and poll for an IP;
  5. fail loudly with the daemon log tail if no IP arrives.

The join flags that used to be spelled out in the mission prompt (``--accept-routes`` for the
mission CIDR, ``--accept-dns=false`` so the resolver-less tailnet never wipes the agent's own
DNS) live here now, in one place, exercised by every launch mode.
"""

from __future__ import annotations

# Pinned to a client proven against headscale:stable. Bump when the control plane needs a newer
# client; kept explicit (not "latest") so a run is reproducible and not at the mercy of upstream.
TAILSCALE_CLIENT_VERSION = "1.98.9"

# `%%NAME%%` placeholders are substituted below rather than via an f-string/`.format()` — the shell
# body is dense with `${...}` and `$(...)` that would fight brace interpolation.
_TEMPLATE = r"""#!/bin/sh
# XORCISE tailnet join — fetched from run-control and run as one command. Self-contained.
set -eu

LOGIN_SERVER="%%LOGIN_SERVER%%"
AUTHKEY="%%JOIN_KEY%%"
VER="%%VERSION%%"
RC_BASE="%%RC_BASE%%"
RC_KEY="%%RC_KEY%%"
WORK="${XORCISE_TS_DIR:-$(mktemp -d 2>/dev/null || echo /tmp/xorcise-ts)}"
mkdir -p "$WORK"

CA="$WORK/ca.pem"
%%CA_BLOCK%%
# Trust the embedded CA only when the run shipped one (air-gapped); else use system trust.
if [ -s "$CA" ]; then SSLENV="env SSL_CERT_FILE=$CA"; else SSLENV=""; fi

# Native tailscaled and its self-reaper must OUTLIVE this shell — under a headless harness
# (`claude -p`) every tool call is a fresh short-lived session, so the daemon has to detach from it.
# `setsid` does that cleanly but is util-linux-only: macOS/BSD ship no `setsid`, and there
# `setsid tailscaled …` fails to exec. Fall back to `nohup` (ignore SIGHUP) portably. The Docker
# sidecar does not use this host detachment: its reaper runs inside the container and therefore
# shares the sidecar's Docker-managed lifetime.
if command -v setsid >/dev/null 2>&1; then DETACH="setsid"; else DETACH="nohup"; fi

# 1) Pick how tailscaled runs. Preference order: kernel (a real TUN — faithful host semantics)
# -> Docker sidecar (userspace, contained) -> userspace on the host (last resort).
#
# macOS always takes the sidecar: Go's Darwin verifier does not honor SSL_CERT_FILE, so the
# embedded private Headscale CA cannot authenticate the local control plane from a native macOS
# tailscaled. Linux does honor it, so Linux gets the real choice.
#
# The proxy publishes on host LOOPBACK only; the auth key goes to `docker exec` over stdin, never
# into container args/env/image metadata. `XORCISE_TAILSCALE_MODE=sidecar|native` is an explicit
# diagnostic override that skips the capability detection entirely.
TS_MODE="${XORCISE_TAILSCALE_MODE:-auto}"
# One probe, cached: `docker info` on an unreachable daemon blocks for its full timeout, and this
# used to be asked twice on every join (once to detect, once to guard).
HAVE_DOCKER=no
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then HAVE_DOCKER=yes; fi
# Set when `auto` chose the sidecar, so a sidecar that cannot start can fall back to native
# instead of failing the join. An EXPLICIT XORCISE_TAILSCALE_MODE=sidecar never falls back — an
# operator naming the mode is diagnosing something and wants the failure.
TS_FALLBACK=no
case "$TS_MODE" in
  auto)
    if [ "$(uname -s)" = "Darwin" ]; then
      TS_MODE=sidecar
    elif [ "$(id -u)" = "0" ] && [ -w /dev/net/tun ]; then
      # Kernel mode: a real TUN, so the host stack applies. Loopback stays private, targets are
      # reachable directly by IP, and no proxy exists to expose. The faithful option; preferred.
      TS_MODE=native
    elif [ "$HAVE_DOCKER" = yes ]; then
      # No TUN, but Docker: run userspace-networking in a CONTAINER rather than on the host.
      # In userspace mode netstack delivers inbound tailnet connections to 127.0.0.1 — whichever
      # 127.0.0.1 tailscaled happens to own. On the host that publishes EVERY host loopback
      # service to the mission network; inside a container it is a near-empty namespace.
      TS_MODE=sidecar
      TS_FALLBACK=yes
    else
      # Last resort: userspace on the host. Works anywhere, but see the warning it prints.
      TS_MODE=native
    fi
    ;;
  sidecar | native) ;;
  *)
    echo "xorcise: invalid XORCISE_TAILSCALE_MODE=$TS_MODE (want auto, sidecar, or native)" >&2
    exit 1
    ;;
esac

if [ "$TS_MODE" = sidecar ] && [ "$HAVE_DOCKER" != yes ]; then
  echo "xorcise: Docker is required for the tailnet sidecar." >&2
  echo "  Start Docker (Desktop, or the daemon), then re-run the join command." >&2
  exit 1
fi

if [ "$TS_MODE" = sidecar ]; then

  RUN_TOKEN=$(printf '%s' "${RC_BASE##*/}" | tr -cd 'A-Za-z0-9_.-' | cut -c1-48)
  [ -n "$RUN_TOKEN" ] || RUN_TOKEN=$(cksum < /dev/null | awk '{print $1}')
  SIDECAR="xorcise-ts-$RUN_TOKEN-$$"
  SIDECAR_IMAGE="tailscale/tailscale:v$VER"
  SIDECAR_SOCK="/tmp/xorcise-tailscaled.sock"
  REAP_CAP=$(($(date +%s 2>/dev/null || echo 0) + \
    ${XORCISE_REAP_MAX:-%%REAP_CAP_SECONDS%%}))

  # A run-control URL on macOS loopback must cross the Docker Desktop host boundary when polled
  # from inside the sidecar. Keep non-loopback URLs unchanged.
  case "$RC_BASE" in
    http://127.0.0.1:* | https://127.0.0.1:*)
      REAPER_BASE=$(printf '%s' "$RC_BASE" | sed 's#://127\.0\.0\.1:#://host.docker.internal:#')
      ;;
    http://localhost:* | https://localhost:*)
      REAPER_BASE=$(printf '%s' "$RC_BASE" | sed 's#://localhost:#://host.docker.internal:#')
      ;;
    *) REAPER_BASE="$RC_BASE" ;;
  esac

  set -- docker run -d --rm --name "$SIDECAR" \
    --label "xorcise.run-id=${RC_BASE##*/}" \
    --label "xorcise.run_id=${RC_BASE##*/}" --label "xorcise.managed=true" \
    --cap-drop=ALL --security-opt=no-new-privileges --pids-limit=128 --memory=256m \
    -p 127.0.0.1::1055/tcp -p 127.0.0.1::1056/tcp
  # Docker Desktop resolves host.docker.internal for free; a Linux daemon does not, and the
  # in-container run watcher polls run-control through exactly that name. Map it to the host
  # gateway there (Docker 20.10+) or the watcher never sees the run go terminal and the sidecar
  # leaks until its hard cap.
  if [ "$(uname -s)" != "Darwin" ]; then
    set -- "$@" --add-host=host.docker.internal:host-gateway
  fi
  if [ -s "$CA" ]; then
    set -- "$@" -v "$CA:/xorcise/ca.pem:ro" -e SSL_CERT_FILE=/xorcise/ca.pem
  fi
  # PID 1 supervises both tailscaled and the run watcher. The bearer is injected afterwards over
  # docker-exec stdin into an ephemeral, mode-0600 container file, so it never appears in image,
  # environment, mount, or command metadata. When run-control returns terminal 409 (or the cap is
  # reached), the watcher signals PID 1; PID 1 logs out, stops tailscaled, and exits. Docker --rm
  # then removes the sidecar without relying on a host process surviving the agent harness.
  set -- "$@" --entrypoint sh "$SIDECAR_IMAGE" -c '
    sock=$1
    shift
    # Bind the proxies to this container OWN bridge address, not 0.0.0.0. In userspace-networking
    # mode netstack delivers inbound tailnet connections to 127.0.0.1 INSIDE this namespace, so a
    # wildcard bind publishes the agent SOCKS/HTTP proxy ON the tailnet — reachable by the mission
    # once agent ingress is allowed, which would hand mission code a relay carrying the agent
    # identity. The docker published-port proxy dials the container address, so binding it keeps
    # the host side working while removing the tailnet side. (No apostrophes: this whole script is
    # a single-quoted argument to sh -c.)
    bip=$(ip -4 -o addr show 2>/dev/null |
      sed -n "s|.*inet \([0-9.][0-9.]*\)/.*|\1|p" | grep -v "^127\." | head -1)
    if [ -z "$bip" ]; then
      echo "xorcise: no non-loopback address to bind the tailnet proxies to; refusing to" >&2
      echo "  start. Falling back to a wildcard bind here would publish this SOCKS5/HTTP" >&2
      echo "  proxy ON the tailnet, handing mission code a relay with the agent identity." >&2
      exit 1
    fi
    tailscaled --socks5-server="$bip":1055 --outbound-http-proxy-listen="$bip":1056 "$@" &
    tailscaled_pid=$!

    stop_sidecar() {
      tailscale --socket="$sock" down >/dev/null 2>&1 || true
      kill "$tailscaled_pid" >/dev/null 2>&1 || true
    }
    trap stop_sidecar TERM INT

    (
      config=/tmp/xorcise-reaper.conf
      while [ ! -s "$config" ]; do
        kill -0 "$tailscaled_pid" >/dev/null 2>&1 || exit 0
        sleep 1
      done
      {
        IFS= read -r key
        IFS= read -r base
        IFS= read -r cap
      } <"$config"
      rm -f "$config"

      while kill -0 "$tailscaled_pid" >/dev/null 2>&1; do
        sleep 15
        response=$(wget -S -O /dev/null \
          --header="Authorization: Bearer $key" "$base/mission" 2>&1 || true)
        printf "%s\n" "$response" | grep -Eq "HTTP/[0-9.]+ 409" && break
        [ "$(date +%s 2>/dev/null || echo 0)" -ge "$cap" ] && break
      done
      kill -TERM 1
    ) &
    reaper_pid=$!

    wait "$tailscaled_pid"
    status=$?
    kill "$reaper_pid" >/dev/null 2>&1 || true
    wait "$reaper_pid" >/dev/null 2>&1 || true
    exit "$status"
  ' _ "$SIDECAR_SOCK" \
    --tun=userspace-networking \
    --state=/tmp/xorcise-tailscaled.state --socket="$SIDECAR_SOCK"
  if ! "$@" >/dev/null; then
    if [ "$TS_FALLBACK" = yes ]; then
      # The sidecar image comes from Docker Hub; the native client comes from run-control, which
      # caches it for exactly this case. So an air-gapped host — the one the tarball path exists
      # to serve — is precisely where the sidecar cannot start, and `auto` preferring the sidecar
      # would otherwise have made the join impossible there rather than merely less contained.
      # Say what is being given up: host userspace mode puts netstack on THIS host's 127.0.0.1.
      echo "xorcise: tailnet sidecar could not start (image $SIDECAR_IMAGE)." >&2
      echo "  Falling back to host userspace networking. Inbound tailnet connections will be" >&2
      echo "  delivered to this host's loopback rather than a container's — set" >&2
      echo "  XORCISE_TAILSCALE_MODE=sidecar to make this a hard failure instead." >&2
      TS_MODE=native
    else
      echo "xorcise: tailnet sidecar failed to start (image $SIDECAR_IMAGE)." >&2
      exit 1
    fi
  fi
fi

if [ "$TS_MODE" = sidecar ]; then

  i=0
  while [ "$i" -lt 15 ]; do
    docker exec "$SIDECAR" test -S "$SIDECAR_SOCK" >/dev/null 2>&1 && break
    if ! docker container inspect "$SIDECAR" >/dev/null 2>&1; then break; fi
    sleep 1
    i=$((i + 1))
  done
  if ! docker exec "$SIDECAR" test -S "$SIDECAR_SOCK" >/dev/null 2>&1; then
    echo "xorcise: tailnet sidecar daemon did not become ready — log tail:" >&2
    docker logs --tail 20 "$SIDECAR" >&2 || true
    docker rm -f "$SIDECAR" >/dev/null 2>&1 || true
    exit 1
  fi

  if ! {
    printf '%s\n' "$RC_KEY"
    printf '%s\n' "$REAPER_BASE"
    printf '%s\n' "$REAP_CAP"
  } | docker exec -i "$SIDECAR" sh -c \
    'umask 077; cat > /tmp/xorcise-reaper.conf'; then
    echo "xorcise: failed to arm the tailnet sidecar reaper." >&2
    docker rm -f "$SIDECAR" >/dev/null 2>&1 || true
    exit 1
  fi

  # The one-time key is read from stdin inside the container. LOGIN_SERVER + hostname are
  # non-secret positional args; neither the key nor CA content appears in `docker inspect`.
  printf '%s\n' "$AUTHKEY" | docker exec -i "$SIDECAR" sh -c '
    IFS= read -r key
    tailscale --socket="$1" up \
      --login-server="$2" --authkey="$key" \
      --accept-routes --accept-dns=false --hostname="$3" --timeout=30s || true
  ' _ "$SIDECAR_SOCK" "$LOGIN_SERVER" "xorcise-agent-$RUN_TOKEN"

  IP=""
  i=0
  while [ "$i" -lt 15 ]; do
    IP=$(docker exec "$SIDECAR" tailscale --socket="$SIDECAR_SOCK" ip -4 2>/dev/null \
      | head -1 || true)
    [ -n "$IP" ] && break
    sleep 2
    i=$((i + 1))
  done
  if [ -z "$IP" ]; then
    echo "xorcise: tailnet join FAILED (sidecar received no IP) — log tail:" >&2
    docker logs --tail 20 "$SIDECAR" >&2 || true
    docker rm -f "$SIDECAR" >/dev/null 2>&1 || true
    exit 1
  fi

  SOCKS=$(docker port "$SIDECAR" 1055/tcp 2>/dev/null \
    | sed -n 's/^127\.0\.0\.1://p' | head -1)
  HTTP=$(docker port "$SIDECAR" 1056/tcp 2>/dev/null \
    | sed -n 's/^127\.0\.0\.1://p' | head -1)
  if [ -z "$SOCKS" ] || [ -z "$HTTP" ]; then
    echo "xorcise: tailnet sidecar joined but its loopback proxy ports were not published." >&2
    docker rm -f "$SIDECAR" >/dev/null 2>&1 || true
    exit 1
  fi

  ENVF="$WORK/ts-env.sh"
  {
    echo "export XORCISE_SOCKS5=127.0.0.1:$SOCKS"
    echo "export XORCISE_HTTP_PROXY=http://127.0.0.1:$HTTP"
  } >"$ENVF"
  echo "xorcise: joined tailnet as $IP (Docker sidecar mode)."
  echo "xorcise: the macOS host has no tailnet route; EVERY target connection must use the proxy."
  echo "xorcise: HTTP example:"
  echo "  curl --socks5-hostname 127.0.0.1:$SOCKS http://<target-ip>:<port>/"
  echo "xorcise: raw TCP example (macOS nc):"
  echo "  nc -X 5 -x 127.0.0.1:$SOCKS <target-ip> <port>"
  echo "xorcise: your tailnet node IS the container $SIDECAR."
  echo "xorcise: to ACCEPT connections (a callback API, a listener), your server must run INSIDE"
  echo "  that container network namespace — a port bound on this host is NOT reachable:"
  echo "    docker run -d --network container:$SIDECAR <image> <your-server-command>"
  echo "  any port works; the mission tells you which address to register."
  echo "xorcise: proxy details were saved to $ENVF."
  echo "xorcise: do not set a global proxy; Claude API traffic must stay on the normal network."

  exit 0
fi

# 2) Native Linux/BSD path: locate the client, or fetch the pinned static binary (no root/apt).
TS="$(command -v tailscale || true)"
TSD="$(command -v tailscaled || true)"
if [ -z "$TS" ] || [ -z "$TSD" ]; then
  # macOS has no fetchable static userspace client: the only macOS build is the GUI app / system
  # extension (no standalone tailscaled CLI). A client already on PATH is used above and works in
  # userspace mode; when absent we can't install one from here, so guide the operator to the
  # userspace-capable Homebrew CLI instead of fetching a Linux binary that can't exec on Darwin.
  if [ "$(uname -s)" = "Darwin" ]; then
    echo "xorcise: tailscale not found on this macOS host, and no static macOS client can be" >&2
    echo "  auto-installed. Install the CLI, then re-run:  brew install tailscale" >&2
    exit 1
  fi
  case "$(uname -m)" in
    x86_64) A=amd64 ;;
    aarch64 | arm64) A=arm64 ;;
    *) A=amd64 ;;
  esac
  # Prefer the run-control-served client (air-gapped: the server caches it, no public egress
  # needed); fall back to the public CDN when run-control can't serve it.
  curl -fsSL -H "Authorization: Bearer $RC_KEY" \
    "$RC_BASE/tailscale.tgz?arch=$A" -o "$WORK/ts.tgz" ||
    curl -fsSL "https://pkgs.tailscale.com/stable/tailscale_${VER}_${A}.tgz" -o "$WORK/ts.tgz"
  tar xzf "$WORK/ts.tgz" -C "$WORK"
  TS="$WORK/tailscale_${VER}_${A}/tailscale"
  TSD="$WORK/tailscale_${VER}_${A}/tailscaled"
fi

SOCK="$WORK/tailscaled.sock"
STATE="$WORK/tailscaled.state"
LOG="$WORK/tailscaled.log"

# 3) Capability-detect. Real root with a writable TUN -> kernel mode (targets direct by IP);
#    otherwise userspace-networking + a SOCKS5 proxy (targets through the proxy).
if [ "$(id -u)" = "0" ] && [ -w /dev/net/tun ]; then
  MODE=kernel
  $DETACH $SSLENV "$TSD" --state="$STATE" --socket="$SOCK" >"$LOG" 2>&1 &
  # Wait for the daemon control socket.
  i=0
  while [ ! -S "$SOCK" ] && [ "$i" -lt 15 ]; do sleep 1; i=$((i + 1)); done
else
  MODE=userspace
  # Pick a free (SOCKS5, HTTP-proxy) port pair and retry. The ports are NOT fixed any
  # more: a leftover userspace daemon from a prior host run used to hold the old well-known ports
  # and block this join. A randomised start makes concurrent runs unlikely to pick the same pair;
  # retry settles any clash. `port_busy` is a cheap pre-check (skipped if `ss` is absent); the real
  # safety is spawn-and-verify — a port taken between check and bind makes the daemon exit and the
  # control socket never appear, so we reap that half-started attempt (by its unique socket) and try
  # new ports.
  # Which loopback address the proxies bind to. In userspace mode netstack delivers INBOUND
  # tailnet connections to 127.0.0.1, so a proxy bound there is reachable from the mission network
  # once agent ingress is allowed — an open relay carrying the agent's tailnet identity. 127.0.0.2
  # is equally loopback-only to this host but is NOT netstack's forward target, so it disappears
  # from the tailnet while staying usable locally.
  # Darwin has no 127.0.0.2 without an explicit lo0 alias, and this path is a diagnostic override
  # there (auto picks the sidecar), so keep 127.0.0.1 rather than fail to bind.
  if [ "$(uname -s)" = "Darwin" ]; then PROXY_ADDR=127.0.0.1; else PROXY_ADDR=127.0.0.2; fi
  port_busy() { ss -ltnH "sport = :$1" 2>/dev/null | grep -q .; }
  rand_port() {
    r=$(od -An -N2 -tu2 /dev/urandom 2>/dev/null | tr -d ' ')
    [ -n "$r" ] || r=$$
    echo $((20000 + r % 40000))
  }
  SOCKS=""
  HTTP=""
  a=0
  while [ "$a" -lt 8 ]; do
    a=$((a + 1))
    sp=$(rand_port)
    hp=$((sp + 1))
    if port_busy "$sp" || port_busy "$hp"; then continue; fi
    $DETACH $SSLENV "$TSD" --tun=userspace-networking \
      --socks5-server="$PROXY_ADDR":"$sp" --outbound-http-proxy-listen="$PROXY_ADDR":"$hp" \
      --state="$STATE" --socket="$SOCK" >"$LOG" 2>&1 &
    i=0
    while [ ! -S "$SOCK" ] && [ "$i" -lt 8 ]; do sleep 1; i=$((i + 1)); done
    # Success = the daemon is actually alive on this socket. Check the PROCESS, NOT `tailscale
    # status`: a freshly-spawned daemon is "Logged out" until `up`, and `status` exits non-zero in
    # that state, so gating on it rejected every healthy daemon. A port clash makes tailscaled exit,
    # so pgrep finds nothing (even if the socket file lingers) and we retry a new port.
    if [ -S "$SOCK" ] && pgrep -f -- "--socket=$SOCK" >/dev/null 2>&1; then
      SOCKS="$sp"
      HTTP="$hp"
      break
    fi
    # Lost the bind race / port taken -> the daemon exited. Reap this attempt (exact by its unique
    # socket) and reset state before retrying new ports.
    for p in $(pgrep -f -- "--socket=$SOCK" 2>/dev/null); do kill -9 "$p" 2>/dev/null || true; done
    rm -f "$SOCK" "$STATE" 2>/dev/null || true
  done
  if [ -z "$SOCKS" ]; then
    echo "xorcise: tailnet join FAILED (no free proxy port in $a tries) — daemon log tail:" >&2
    tail -20 "$LOG" >&2 || true
    exit 1
  fi
fi

# 4) Join. --timeout so `up` can never block indefinitely (the old 120s hang); poll separately.
$SSLENV "$TS" --socket="$SOCK" up \
  --login-server="$LOGIN_SERVER" --authkey="$AUTHKEY" \
  --accept-routes --accept-dns=false --hostname=xorcise-agent --timeout=30s || true

# 5) Poll for the assigned tailnet IP.
IP=""
i=0
while [ "$i" -lt 15 ]; do
  IP="$($SSLENV "$TS" --socket="$SOCK" ip -4 2>/dev/null | head -1 || true)"
  [ -n "$IP" ] && break
  sleep 2
  i=$((i + 1))
done

if [ -z "$IP" ]; then
  echo "xorcise: tailnet join FAILED (no IP) — daemon log tail:" >&2
  tail -20 "$LOG" >&2 || true
  exit 1
fi

# 6) Report how to reach the targets (userspace: on the port the retry loop actually chose).
if [ "$MODE" = userspace ]; then
  ENVF="$WORK/ts-env.sh"
  {
    # The mode-independent handle, written in EVERY mode that has a proxy (the sidecar writes the
    # same two) so an agent keying off XORCISE_SOCKS5 is portable across modes. The standard
    # ALL_PROXY/HTTP(S)_PROXY names below are the userspace-only convenience for `set -a; . env`;
    # the sidecar deliberately omits them, since a global proxy on that host would route the
    # agent's own Claude API traffic through the tailnet.
    echo "export XORCISE_SOCKS5=$PROXY_ADDR:$SOCKS"
    echo "export XORCISE_HTTP_PROXY=http://$PROXY_ADDR:$HTTP"
    echo "export ALL_PROXY=socks5://$PROXY_ADDR:$SOCKS"
    echo "export HTTPS_PROXY=http://$PROXY_ADDR:$HTTP"
    echo "export HTTP_PROXY=http://$PROXY_ADDR:$HTTP"
  } >"$ENVF"
  echo "xorcise: joined tailnet as $IP (userspace mode, on this host)."
  echo "xorcise: reach targets THROUGH the SOCKS5 proxy at $PROXY_ADDR:$SOCKS, e.g.:"
  echo "  curl --socks5-hostname $PROXY_ADDR:$SOCKS http://<target-ip>:<port>/"
  echo "  or export the proxy for all tools:  set -a; . \"$ENVF\"; set +a"
  echo "xorcise: to ACCEPT connections, bind a port on this host — it IS reachable at $IP."
  echo "xorcise: NOTE this mode runs tailscaled in the HOST namespace, so every service already"
  echo "  listening on 127.0.0.1 is reachable from the mission network too. Prefer a root sandbox"
  echo "  (kernel mode) or Docker (sidecar mode) on a host with services you care about."
else
  echo "xorcise: joined tailnet as $IP (kernel mode) — reach targets directly by IP."
  echo "xorcise: to ACCEPT connections, bind a port on this host — it IS reachable at $IP."
  echo "xorcise: loopback stays private here; bind 0.0.0.0 for the mission to reach you."
fi

# 7) Self-reaper. A detached watcher tears THIS run's daemon down when the run ends, so a
# host run never leaks its userspace tailscaled (a leftover daemon is what broke the NEXT run's
# join). It is EXACT — it matches only this run's daemon by its unique control socket ($SOCK), so
# nothing is persisted to the host and it can never touch a concurrent run's or the operator's own
# tailscaled.
# Trigger: run-control reports the run terminal (HTTP 409 on any authed call — the authoritative
# signal, covering the agent's `complete` AND the budget timeout / operator kill). Plus a hard cap
# so the watcher can never leak. The cap is bound to the run budget (%%REAP_CAP_SECONDS%%, +margin)
# not a blanket day, so a daemon leaked by a hard-killed box (the only case 409 can't reach)
# self-reaps in ~run-lifetime. We deliberately DO NOT trip on join.sh's own shell session exiting:
# under a headless harness (`claude -p`) every tool call is a fresh short-lived session, so that
# check tore the daemon down mid-run seconds after the join returned. Dynamic ports (above) mean a
# briefly-leaked daemon no longer blocks a later run, so 409 + cap is sufficient.
REAP_CAP=$(($(date +%s 2>/dev/null || echo 0) + ${XORCISE_REAP_MAX:-%%REAP_CAP_SECONDS%%}))
$DETACH sh -c '
  sock=$1; base=$2; key=$3; ts=$4; cap=$5
  while :; do
    sleep 15
    code=$(curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $key" \
      "$base/mission" 2>/dev/null || echo 000)
    [ "$code" = "409" ] && break
    [ "$(date +%s 2>/dev/null || echo 0)" -ge "$cap" ] && break
  done
  "$ts" --socket="$sock" down 2>/dev/null || true
  for p in $(pgrep -f -- "--socket=$sock" 2>/dev/null); do
    kill "$p" 2>/dev/null || true
    sleep 1
    kill -9 "$p" 2>/dev/null || true
  done
  w=$(dirname "$sock")
  case "$w" in /tmp/*) rm -rf "$w" 2>/dev/null || true ;; esac
 ' _ "$SOCK" "$RC_BASE" "$RC_KEY" "$TS" "$REAP_CAP" </dev/null >/dev/null 2>&1 &
"""

_CA_BLOCK = "cat > \"$CA\" <<'XORCISE_CA_EOF'\n{ca}\nXORCISE_CA_EOF"
_NO_CA = ": # no control-plane CA shipped (public cert) — system trust is used"


def render_join_script(
    *,
    login_server: str,
    join_key: str,
    ca_cert: str,
    run_control_base: str = "",
    run_control_key: str = "",
    version: str = "",
    reap_cap_seconds: int = 86400,
) -> str:
    """Render the runnable join script with the run's bundle baked in.

    ``login_server`` is the by-IP control-plane URL (already rewritten for the air-gapped join),
    ``join_key`` the per-run one-time auth key, ``ca_cert`` the control-plane CA PEM (empty for a
    public control plane). ``run_control_base``/``run_control_key`` let the script fetch the pinned
    client from run-control's ``GET /tailscale.tgz`` (air-gapped) before falling back to the public
    CDN — the same authed base the agent used to fetch this script. ``version`` overrides the pinned
    client (defaults to the module pin). ``reap_cap_seconds`` is the self-reaper's hard-cap backstop
    (baked as the ``XORCISE_REAP_MAX`` default); callers pass the run budget + a margin so a daemon
    leaked by a hard-killed box self-reaps in ~run-lifetime instead of a blanket day.
    """
    ca = ca_cert.strip()
    ca_block = _CA_BLOCK.format(ca=ca) if ca else _NO_CA
    return (
        _TEMPLATE.replace("%%LOGIN_SERVER%%", login_server)
        .replace("%%JOIN_KEY%%", join_key)
        .replace("%%VERSION%%", version or TAILSCALE_CLIENT_VERSION)
        .replace("%%RC_BASE%%", run_control_base)
        .replace("%%RC_KEY%%", run_control_key)
        .replace("%%CA_BLOCK%%", ca_block)
        .replace("%%REAP_CAP_SECONDS%%", str(reap_cap_seconds))
    )
