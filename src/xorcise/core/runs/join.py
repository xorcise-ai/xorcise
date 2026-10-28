"""Render the one-shot tailnet join script served to the agent (domain module). Pure; no I/O.

An agent (or a BYO harness we don't launch) fetches this from run-control — ``GET
/runs/<id>/join.sh`` — and runs it, passing the bearer to curl on stdin so it never sits on an
argv: ``printf 'header = "Authorization: Bearer %s"\n' "$XORCISE_RUN_BEARER" | curl -fsS -K -
<base>/join.sh | sh``. The script folds the whole tailnet-join dance the agent used to
reconstruct by hand into one command:

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

# Pinned to a client proven against the pinned control plane (headscale.provision.
# HEADSCALE_IMAGE_PIN; Headscale v0.29.x needs a client of v1.80.0 or later). Bump when the
# control plane needs a newer client; kept explicit (not "latest") so a run is reproducible and
# not at the mercy of upstream.
TAILSCALE_CLIENT_VERSION = "1.98.9"

# `%%NAME%%` placeholders are substituted below rather than via an f-string/`.format()` — the shell
# body is dense with `${...}` and `$(...)` that would fight brace interpolation.
_TEMPLATE = r"""#!/bin/sh
# XORCISE tailnet join — fetched from run-control and run as one command. Self-contained.
set -eu
# Every file this script writes is private to this user: the work dir holds the one-time join key
# while `up` reads it, and the daemon state/log after.
umask 077

# SECRETS NEVER GO ON A COMMAND LINE. Any local user can read every process argv through `ps` or
# /proc/<pid>/cmdline, and on a Linux host that includes processes inside the Docker sidecar. So:
# the join key reaches `tailscale up` as `--auth-key=file:`; the bearer reaches curl as `-K -`
# config on stdin from the `printf` builtin (a builtin never execs, so never has an argv); and the
# self-reaper holds only WATCH_KEY, a liveness-only token run-control accepts on nothing but
# `/alive` — it is the one value that may touch an argv (BusyBox wget in the sidecar), because
# leaking it discloses only whether the run is still going.
LOGIN_SERVER="%%LOGIN_SERVER%%"
AUTHKEY="%%JOIN_KEY%%"
VER="%%VERSION%%"
RC_BASE="%%RC_BASE%%"
RC_KEY="%%RC_KEY%%"
WATCH_KEY="%%WATCH_KEY%%"
# Always a fresh mktemp dir (mode 0700, unpredictable name). The old fixed /tmp/xorcise-ts fallback
# was a path another local user could pre-create or plant a symlink at before this script wrote a
# key into it. XORCISE_TS_DIR picks the PARENT, never the dir itself, for the same reason.
#
# The parent must be one where no other user can rename our dir out from under us and plant their
# own at its path: owned by this user or root, and either writable by nobody else or sticky (like
# /tmp, where only an entry's owner may rename it).
ts_parent_is_safe() {
  # `$1/.` is the directory itself even when $1 is a symlink: `ls -ldn` on a link would report
  # the link's own lrwxrwxrwx mode and owner instead.
  set -- $(ls -ldn "$1/.")
  [ "$3" = "$(id -u)" ] || [ "$3" = 0 ] || return 1
  case "$1" in
    ?????w* | ????????w*) case "$1" in *t | *T | *t? | *T?) return 0 ;; *) return 1 ;; esac ;;
  esac
}
TS_PARENT="${XORCISE_TS_DIR:-${TMPDIR:-/tmp}}"
mkdir -p "$TS_PARENT"
if ! ts_parent_is_safe "$TS_PARENT"; then
  echo "xorcise: refusing to keep the join key under $TS_PARENT: another user could replace" >&2
  echo "  what is created there. Point XORCISE_TS_DIR at a directory only you can write to." >&2
  exit 1
fi
WORK=$(mktemp -d "$TS_PARENT/xorcise-ts.XXXXXX")
# Removed on every exit: `up` deletes it as soon as it returns, this covers an abort before that.
# A shell killed by a signal skips its EXIT trap, so the usual ones become an exit that runs it.
trap 'rm -f "$WORK/authkey"' EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

CA="$WORK/ca.pem"
%%CA_BLOCK%%
# The CA is public, and the sidecar mounts it for a container user that is not this one.
[ -s "$CA" ] && chmod 644 "$CA"
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
# Which client the native path runs (section 2). Checked here, before the mode is chosen, so a bad
# value fails the same way whether this host ends up on the sidecar or on the native path.
TS_CLIENT="${XORCISE_TAILSCALE_CLIENT:-auto}"
case "$TS_CLIENT" in
  auto | fetched) ;;
  *)
    echo "xorcise: invalid XORCISE_TAILSCALE_CLIENT=$TS_CLIENT (want auto or fetched)" >&2
    exit 1
    ;;
esac
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
  # PID 1 supervises both tailscaled and the run watcher. The watch token is injected afterwards
  # over docker-exec stdin into an ephemeral, mode-0600 container file, so it never appears in
  # image, environment, mount, or command metadata. When run-control returns terminal 409 (or the
  # cap is reached), the watcher signals PID 1; PID 1 logs out, stops tailscaled, and exits.
  # Docker --rm then removes the sidecar without relying on a host process surviving the agent
  # harness.
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

      # $key is the liveness-only watch token, never the bearer: BusyBox wget can take a header
      # only on its argv, which a Linux host shows to every local user.
      while kill -0 "$tailscaled_pid" >/dev/null 2>&1; do
        sleep 15
        response=$(wget -S -O /dev/null \
          --header="Authorization: Bearer $key" "$base/alive" 2>&1 || true)
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
    printf '%s\n' "$WATCH_KEY"
    printf '%s\n' "$REAPER_BASE"
    printf '%s\n' "$REAP_CAP"
  } | docker exec -i "$SIDECAR" sh -c \
    'umask 077; cat > /tmp/xorcise-reaper.conf'; then
    echo "xorcise: failed to arm the tailnet sidecar reaper." >&2
    docker rm -f "$SIDECAR" >/dev/null 2>&1 || true
    exit 1
  fi

  # The one-time key goes over stdin into a mode-0600 container file that `up` reads as
  # `--auth-key=file:`, never as the flag's value: the container's processes are host processes
  # on Linux, so an argv there is as readable as one here. LOGIN_SERVER + hostname are non-secret
  # positional args; neither the key nor CA content appears in `docker inspect`.
  printf '%s' "$AUTHKEY" | docker exec -i "$SIDECAR" sh -c '
    umask 077
    cat > /tmp/xorcise-authkey
    tailscale --socket="$1" up \
      --login-server="$2" --auth-key=file:/tmp/xorcise-authkey \
      --accept-routes --accept-dns=false --hostname="$3" --timeout=30s || true
    rm -f /tmp/xorcise-authkey
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
# The fetched client is unpacked into $WORK and run from there — never installed, so the join
# leaves nothing behind on this system. XORCISE_TAILSCALE_CLIENT=fetched asks for it explicitly.
TS="$(command -v tailscale || true)"
TSD="$(command -v tailscaled || true)"
# The client's own `up --help`, read once: it answers both questions asked of it below — can it
# read the key from a file, and which spelling of the key flag does it know.
TS_HELP=""
if [ "$TS_CLIENT" = fetched ]; then
  TS=""
  TSD=""
elif [ -n "$TS" ] && [ -n "$TSD" ]; then
  TS_HELP=$("$TS" up --help 2>&1 || true)
fi
if [ -n "$TS_HELP" ] && ! printf '%s\n' "$TS_HELP" | grep -q '"file:"'; then
  # A client that cannot read --auth-key from a file would need the key on its argv, where every
  # local user can read it. Stop rather than expose it, and say how to proceed either way.
  echo "xorcise: the tailscale client at $TS is too old to read its auth key from a file." >&2
  echo "  Passing the key as an argument instead would show it to every user on this host." >&2
  if [ "$(uname -s)" = "Darwin" ]; then
    # No static macOS client exists to fetch (see below), so upgrading is the only way forward.
    echo "  Upgrade it (brew upgrade tailscale; the tested version is v$VER), then re-run." >&2
  else
    echo "  Either upgrade it (the pinned, tested version is v$VER), or re-run the join with" >&2
    echo "  XORCISE_TAILSCALE_CLIENT=fetched to run a temporary v$VER client from a scratch" >&2
    echo "  directory instead — nothing is installed on this system." >&2
  fi
  exit 1
fi
if [ -z "$TS" ] || [ -z "$TSD" ]; then
  # macOS has no fetchable static userspace client: the only macOS build is the GUI app / system
  # extension (no standalone tailscaled CLI). A client already on PATH is used above and works in
  # userspace mode; when absent we can't install one from here, so guide the operator to the
  # userspace-capable Homebrew CLI instead of fetching a Linux binary that can't exec on Darwin.
  if [ "$(uname -s)" = "Darwin" ] && [ "$TS_CLIENT" = fetched ]; then
    echo "xorcise: XORCISE_TAILSCALE_CLIENT=fetched is Linux-only — there is no static macOS" >&2
    echo "  client to fetch. Unset it and use the tailscale CLI (brew install tailscale)." >&2
    exit 1
  fi
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
  # needed); fall back to the public CDN when run-control can't serve it. The bearer reaches curl
  # as config on stdin (`-K -`), not as a `-H` argument.
  printf 'header = "Authorization: Bearer %s"\n' "$RC_KEY" |
    curl -fsSL -K - "$RC_BASE/tailscale.tgz?arch=$A" -o "$WORK/ts.tgz" ||
    curl -fsSL "https://pkgs.tailscale.com/stable/tailscale_${VER}_${A}.tgz" -o "$WORK/ts.tgz"
  tar xzf "$WORK/ts.tgz" -C "$WORK"
  TS="$WORK/tailscale_${VER}_${A}/tailscale"
  TSD="$WORK/tailscale_${VER}_${A}/tailscaled"
  TS_HELP=$("$TS" up --help 2>&1 || true)
fi

# `--auth-key` is the current spelling; clients from before it only know `--authkey`, which later
# ones keep as a hidden alias. Use the one this client documents, so an older client that can read
# a key file still joins instead of rejecting the flag (and `up || true` hiding why).
AUTHKEY_FLAG=--authkey
if printf '%s\n' "$TS_HELP" | grep -q -- '-auth-key'; then AUTHKEY_FLAG=--auth-key; fi

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
# The key is read from a mode-0600 file (the umask above), never passed as the flag value.
printf '%s' "$AUTHKEY" >"$WORK/authkey"
$SSLENV "$TS" --socket="$SOCK" up \
  --login-server="$LOGIN_SERVER" "$AUTHKEY_FLAG=file:$WORK/authkey" \
  --accept-routes --accept-dns=false --hostname=xorcise-agent --timeout=30s || true
rm -f "$WORK/authkey"

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
# It holds only the liveness-only watch token, handed over in its environment (readable by this
# user alone, unlike an argv) and passed on to curl as stdin config, so no argv ever carries it.
# Polling /alive rather than /mission also keeps the reaper from counting as the agent reading
# its brief.
REAP_CAP=$(($(date +%s 2>/dev/null || echo 0) + ${XORCISE_REAP_MAX:-%%REAP_CAP_SECONDS%%}))
XORCISE_WATCH_KEY="$WATCH_KEY" $DETACH sh -c '
  sock=$1; base=$2; ts=$3; cap=$4
  key=$XORCISE_WATCH_KEY
  unset XORCISE_WATCH_KEY
  while :; do
    sleep 15
    code=$(printf "header = \"Authorization: Bearer %s\"\n" "$key" |
      curl -s -o /dev/null -w "%{http_code}" -K - "$base/alive" 2>/dev/null) || code=000
    [ "$code" = "409" ] && break
    [ "$(date +%s 2>/dev/null || echo 0)" -ge "$cap" ] && break
  done
  "$ts" --socket="$sock" down 2>/dev/null || true
  for p in $(pgrep -f -- "--socket=$sock" 2>/dev/null); do
    kill "$p" 2>/dev/null || true
    sleep 1
    kill -9 "$p" 2>/dev/null || true
  done
  # The socket lives in the mktemp work dir this join created (never a dir the operator named),
  # so it is removed wherever the parent is, not only under /tmp.
  w=$(dirname "$sock")
  case "${w##*/}" in xorcise-ts.*) rm -rf "$w" 2>/dev/null || true ;; esac
 ' _ "$SOCK" "$RC_BASE" "$TS" "$REAP_CAP" </dev/null >/dev/null 2>&1 &
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
    watch_token: str = "",
    version: str = "",
    reap_cap_seconds: int = 86400,
) -> str:
    """Render the runnable join script with the run's bundle baked in.

    ``login_server`` is the by-IP control-plane URL (already rewritten for the air-gapped join),
    ``join_key`` the per-run one-time auth key, ``ca_cert`` the control-plane CA PEM (empty for a
    public control plane). ``run_control_base``/``run_control_key`` let the script fetch the pinned
    client from run-control's ``GET /tailscale.tgz`` (air-gapped) before falling back to the public
    CDN — the same authed base the agent used to fetch this script. ``watch_token`` is the run's
    liveness-only credential for ``GET /alive``, the one the self-reaper polls with; it never gets
    the bearer, because the sidecar's reaper can only send a header from an argv. ``version``
    overrides the pinned client (defaults to the module pin). ``reap_cap_seconds`` is the
    self-reaper's hard-cap backstop (baked as the ``XORCISE_REAP_MAX`` default); callers pass the
    run budget + a margin so a daemon leaked by a hard-killed box self-reaps in ~run-lifetime
    instead of a blanket day.
    """
    ca = ca_cert.strip()
    ca_block = _CA_BLOCK.format(ca=ca) if ca else _NO_CA
    return (
        _TEMPLATE.replace("%%LOGIN_SERVER%%", login_server)
        .replace("%%JOIN_KEY%%", join_key)
        .replace("%%VERSION%%", version or TAILSCALE_CLIENT_VERSION)
        .replace("%%RC_BASE%%", run_control_base)
        .replace("%%RC_KEY%%", run_control_key)
        .replace("%%WATCH_KEY%%", watch_token)
        .replace("%%CA_BLOCK%%", ca_block)
        .replace("%%REAP_CAP_SECONDS%%", str(reap_cap_seconds))
    )
