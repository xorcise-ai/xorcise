import subprocess

import pytest

from xorcise.core.runs.join import TAILSCALE_CLIENT_VERSION, render_join_script

pytestmark = pytest.mark.unit

_CA_PEM = "-----BEGIN CERTIFICATE-----\nMIIBfake\n-----END CERTIFICATE-----\n"


def _script(reap_cap_seconds: int = 86400, **over: str) -> str:
    kw = dict(
        login_server="https://172.17.0.1:443",
        join_key="hskey-auth-SECRET",
        ca_cert=_CA_PEM,
        run_control_base="http://127.0.0.1:3001/api/runs/run-1",
        run_control_key="rk-SECRET",
    )
    kw.update(over)
    return render_join_script(reap_cap_seconds=reap_cap_seconds, **kw)


def test_interpolates_bundle_values():
    s = _script()
    assert "https://172.17.0.1:443" in s  # login server
    assert "hskey-auth-SECRET" in s  # authkey baked in (served over an authed endpoint)
    assert "BEGIN CERTIFICATE" in s  # CA embedded so the daemon can trust the control plane
    assert s.startswith("#!")  # runnable via `curl ... | sh`


def test_carries_the_join_invariants_that_moved_out_of_the_prompt():
    # These flags used to live in the prompt recipe; they now live in the one script.
    s = _script()
    assert "--accept-routes" in s  # reach the mission CIDR via the router
    assert "--accept-dns=false" in s  # never wipe the agent's own resolver
    assert "SSL_CERT_FILE" in s  # trust the embedded CA (air-gapped TLS)
    assert "--timeout" in s  # `up` must not block indefinitely (the old 120s hang)


def test_non_root_userspace_path_with_socks_proxy():
    # The common sandbox is non-root: userspace-networking + a SOCKS5 proxy, no root/TUN needed.
    s = _script()
    assert "userspace-networking" in s
    assert 'socks5-server="$PROXY_ADDR"' in s  # a SOCKS5 proxy is opened (port is now dynamic)
    assert "id -u" in s  # capability detection branch
    # It must tell the caller how to reach targets through the proxy.
    assert "--socks5-hostname $PROXY_ADDR:" in s


def test_userspace_ports_are_dynamic_not_the_fixed_1055_1056():
    # hardcoded 1055/1056 made a leaked prior-run daemon block the next run. The ports are
    # now chosen at join time on the NATIVE path. The sidecar uses fixed CONTAINER ports but asks
    # Docker for dynamic host-loopback ports, so scope this assertion to the native retry block.
    s = _script()
    native = s[s.index("port_busy()") : s.index("# 4) Join.")]
    assert "1055" not in native
    assert "1056" not in native
    assert 'socks5-server="$PROXY_ADDR"' in native  # opens a proxy on a chosen native port


def test_userspace_retries_to_find_a_free_port():
    # pick a free (SOCKS, HTTP) pair with a randomised start and retry — a port free at
    # check time can be taken at bind time (TOCTOU), and a leftover daemon may still hold one.
    s = _script()
    assert "/dev/urandom" in s  # randomised starting port so concurrent runs rarely collide
    assert "port_busy" in s  # pre-check a candidate before spending a spawn
    assert "no free proxy port" in s  # loud failure only after exhausting the retries
    # a failed attempt reaps its own half-started daemon (unique socket) before trying new ports
    assert 'pgrep -f -- "--socket=$SOCK"' in s


def test_port_retry_verifies_daemon_liveness_not_login_dependent_status():
    # Regression: a freshly-spawned userspace daemon is "Logged out" until `up`, and
    # `tailscale status` EXITS NON-ZERO in that state. Gating the port-retry success on `status`
    # rejected every healthy daemon -> "no free proxy port in 8 tries" on EVERY run. The success
    # gate must use a login-independent liveness check (the daemon process is still alive).
    s = _script()
    assert '[ -S "$SOCK" ] && pgrep -f -- "--socket=$SOCK" >/dev/null 2>&1' in s
    # the login-dependent `tailscale status` gate must be gone from the retry loop
    assert '--socket="$SOCK" status' not in s


def test_prints_the_chosen_proxy_port():
    # The agent can't assume 1055 any more, so the script must surface the port it actually chose,
    # both in the human hint and in the sourceable env file.
    s = _script()
    assert "--socks5-hostname $PROXY_ADDR:$SOCKS" in s
    assert "socks5://$PROXY_ADDR:$SOCKS" in s  # ts-env.sh ALL_PROXY uses the chosen port
    assert "http://$PROXY_ADDR:$HTTP" in s  # ts-env.sh HTTP(S)_PROXY uses the chosen port


def test_self_reaper_tears_down_this_runs_daemon_on_termination():
    # a detached watcher reaps THIS run's daemon (exact by its unique socket) when the run
    # ends, so a host run never leaks its userspace tailscaled. No pidfile is written.
    s = _script()
    assert "$DETACH sh -c" in s  # a detached watcher process (setsid or, on macOS, nohup)
    assert "/mission" in s  # polls run-control for terminal state
    assert '"409"' in s  # 409 = mission over (covers agent `complete` AND the budget timeout)
    assert "XORCISE_REAP_MAX" in s  # a hard cap so the watcher itself can never leak
    assert "down" in s  # logs the node out of the control plane before killing the daemon


def test_reaper_does_not_key_on_the_transient_shell_session():
    # Regression (headless deadlock): the reaper used to break on `! kill -0 "$AGENT_SID"`, where
    # AGENT_SID is join.sh's OWN shell session. Under a headless harness (`claude -p`) every Bash
    # tool call is a fresh short-lived session, so that session ends the moment the join call
    # returns and the reaper tore the daemon down mid-run (~15s later), before the run was terminal.
    # 409 (run terminal) is the authoritative signal; the transient-session trigger must be gone.
    s = _script()
    assert "AGENT_SID" not in s  # no capture of join.sh's own session id
    assert "ps -o sid" not in s  # nothing derives the session id any more
    assert '"409"' in s  # 409 remains the authoritative reap trigger
    # The sidecar may check its own tailscaled child; it must not check a host/session PID.
    for line in s.splitlines():
        if "kill -0" in line:
            assert '"$tailscaled_pid"' in line


def test_reaper_cap_defaults_to_the_supplied_run_budget():
    # The hard-cap backstop (the ONLY case the removed session trigger cheaply covered: the box is
    # hard-killed before any 409) is now bound to the run's budget rather than a blanket 24h, so a
    # leaked daemon self-reaps in ~run-lifetime. XORCISE_REAP_MAX still overrides at runtime.
    s = _script(reap_cap_seconds=1800)
    assert "${XORCISE_REAP_MAX:-1800}" in s  # baked default is the supplied cap
    assert "86400" not in s  # the old blanket 24h default is gone when a cap is supplied


def test_reaper_reaps_by_unique_socket_not_a_pidfile():
    # The exact daemon is found by its per-run socket path (already in the process args), so no
    # pidfile artifact is left on the host — matching the "leave no artifacts" contract.
    s = _script()
    assert "pidfile" not in s.lower()
    assert 'pgrep -f -- "--socket=$sock"' in s  # reaper matches only this run's daemon


def test_kernel_mode_branch_for_root_sandboxes():
    # A root sandbox with a TUN device joins in kernel mode → targets reachable directly by IP.
    s = _script()
    assert "/dev/net/tun" in s
    assert "directly by IP" in s


def test_installs_version_pinned_binary_when_missing():
    s = _script()
    assert f'VER="{TAILSCALE_CLIENT_VERSION}"' in s  # version pinned, not "latest"
    assert "tailscale_${VER}_" in s  # tarball name built from the pinned version
    assert "pkgs.tailscale.com" in s
    assert "command -v tailscale" in s  # skip the download when already present


def test_forced_native_darwin_without_client_keeps_the_brew_hint():
    # Native mode remains a diagnostic override. macOS has no fetchable static userspace client,
    # so forced-native with nothing on PATH must retain the actionable Homebrew hint.
    s = _script()
    locate = s.index('TS="$(command -v tailscale || true)"')
    arch = s.index('case "$(uname -m)"', locate)
    darwin_block = s[locate:arch]
    assert '"$(uname -s)" = "Darwin"' in darwin_block
    assert "brew install tailscale" in darwin_block  # actionable, userspace-capable install hint
    assert "exit 1" in darwin_block  # fail fast — never fall through to the Linux fetch


def test_auto_mode_selects_the_sidecar_on_darwin_before_native_client_discovery():
    # The normal macOS path must never reach the Darwin-native client/CA verifier: auto chooses the
    # Linux sidecar before the native command lookup and Linux tarball fallback.
    s = _script()
    darwin = s.index('if [ "$(uname -s)" = "Darwin" ]; then')
    assert s.index("TS_MODE=sidecar", darwin) - darwin < 80  # the Darwin arm, not a later branch
    assert s.index("TS_MODE=sidecar") < s.index('TS="$(command -v tailscale || true)"')
    assert s.index("TS_MODE=sidecar") < s.index("pkgs.tailscale.com")


def test_sidecar_binds_dynamic_proxy_ports_to_host_loopback_only():
    s = _script()
    assert "-p 127.0.0.1::1055/tcp" in s
    assert "-p 127.0.0.1::1056/tcp" in s
    assert "-p 0.0.0.0:" not in s
    assert 'docker port "$SIDECAR" 1055/tcp' in s
    assert "sed -n 's/^127\\.0\\.0\\.1://p'" in s
    assert "the macOS host has no tailnet route" in s
    assert "nc -X 5 -x 127.0.0.1:$SOCKS <target-ip> <port>" in s


def test_sidecar_uses_pinned_image_and_mounts_ca_read_only():
    s = _script()
    assert 'SIDECAR_IMAGE="tailscale/tailscale:v$VER"' in s
    assert '-v "$CA:/xorcise/ca.pem:ro"' in s
    assert "SSL_CERT_FILE=/xorcise/ca.pem" in s
    assert "--cap-drop=ALL" in s
    assert "--security-opt=no-new-privileges" in s


def test_sidecar_key_is_sent_over_stdin_not_docker_args_or_environment():
    s = _script()
    sidecar = s[s.index('if [ "$TS_MODE" = sidecar ]') : s.index("# 2) Native Linux")]
    assert 'printf \'%s\\n\' "$AUTHKEY" | docker exec -i "$SIDECAR"' in sidecar
    assert '--authkey="$key"' in sidecar
    assert "TS_AUTHKEY" not in sidecar
    assert "-e AUTHKEY" not in sidecar
    assert '--authkey="$AUTHKEY"' not in sidecar


def test_sidecar_reaper_removes_only_this_runs_container():
    s = _script()
    assert '--label "xorcise.run-id=${RC_BASE##*/}"' in s
    assert '--label "xorcise.run_id=${RC_BASE##*/}"' in s
    assert '--label "xorcise.managed=true"' in s
    sidecar = s[s.index('if [ "$TS_MODE" = sidecar ]') : s.index("# 2) Native Linux")]
    assert "kill -TERM 1" in sidecar  # the in-container watcher terminates its own PID 1
    assert "trap stop_sidecar TERM INT" in sidecar
    assert "docker rm -f" in sidecar  # setup failures still clean up immediately
    assert "$DETACH sh -c" not in sidecar  # no host watcher for the harness to kill


def test_sidecar_reaper_secret_is_injected_over_stdin_not_container_metadata():
    s = _script()
    sidecar = s[s.index('if [ "$TS_MODE" = sidecar ]') : s.index("# 2) Native Linux")]
    assert '| docker exec -i "$SIDECAR" sh -c' in sidecar
    assert "cat > /tmp/xorcise-reaper.conf" in sidecar
    assert 'rm -f "$config"' in sidecar
    assert "-e RC_KEY" not in sidecar
    assert '-v "$WORK' not in sidecar


def test_sidecar_reaper_can_reach_macos_host_loopback():
    s = _script()
    sidecar = s[s.index('if [ "$TS_MODE" = sidecar ]') : s.index("# 2) Native Linux")]
    assert "host.docker.internal" in sidecar
    assert "http://127.0.0.1:* | https://127.0.0.1:*" in sidecar
    assert '*) REAPER_BASE="$RC_BASE"' in sidecar


def test_join_script_is_valid_posix_shell():
    result = subprocess.run(["sh", "-n"], input=_script(), text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_daemon_detach_is_portable_not_bare_setsid():
    # macOS/BSD ship no `setsid` (util-linux-only). A bare `setsid tailscaled …` there fails to
    # exec, the control socket never appears, and the userspace port-retry loop dies with "no free
    # proxy port" even with a working brew-installed client. The daemon + reaper must detach through
    # a $DETACH prefix that resolves to setsid where present and nohup (ignore SIGHUP) elsewhere.
    s = _script()
    assert "command -v setsid" in s  # feature-detect rather than assume it exists
    assert 'DETACH="setsid"' in s and 'DETACH="nohup"' in s  # portable fallback
    # every daemon/reaper launch goes through $DETACH — no bare `setsid` LAUNCHER form survives
    # (`command -v setsid` / `DETACH="setsid"` are the only legitimate mentions)
    assert "$DETACH " in s
    assert "setsid $SSLENV" not in s  # kernel + userspace daemon spawns
    assert "setsid sh -c" not in s  # the self-reaper watcher
    assert 'setsid "' not in s  # any `setsid <quoted-binary>` launcher


def test_linux_fetch_path_unchanged():
    # Regression: the Linux static-tarball fetch (arch cases + pinned tarball) is preserved.
    s = _script()
    assert "x86_64) A=amd64" in s
    assert "aarch64 | arm64) A=arm64" in s
    assert "pkgs.tailscale.com/stable/tailscale_${VER}_${A}.tgz" in s


def test_prefers_run_control_served_binary_then_falls_back_to_cdn():
    # Air-gapped: fetch the client from run-control first (server-cached); CDN only as fallback.
    s = _script()
    assert '"$RC_BASE/tailscale.tgz?arch=$A"' in s  # run-control-served client, by arch
    assert 'RC_BASE="http://127.0.0.1:3001/api/runs/run-1"' in s  # base baked from the request
    assert "|| " in s or "||\n" in s  # falls back to the CDN when run-control can't serve it
    # the CDN URL remains the fallback, after the run-control attempt
    assert s.index("$RC_BASE/tailscale.tgz") < s.index("pkgs.tailscale.com/stable/tailscale_")


def test_polls_for_a_tailnet_ip_and_fails_loudly():
    s = _script()
    assert "ip -4" in s  # polls for the assigned tailnet IP
    assert "FAILED" in s  # loud failure with the daemon log on no-IP


def test_no_ca_public_control_plane_uses_system_trust():
    # A public control plane ships no CA (distributed): no embedded PEM, fall back to system trust.
    s = _script(ca_cert="")
    assert "BEGIN CERTIFICATE" not in s
    assert "system trust" in s
    assert "SSLENV" in s  # the conditional still renders (resolves empty at runtime)


# --- Phase 2: Linux mode selection + host-namespace exposure ------------------------------------


def test_auto_prefers_kernel_then_sidecar_over_userspace_on_the_host():
    """Userspace-on-the-host is the LAST resort: netstack forwards inbound to 127.0.0.1, so it
    publishes every host loopback service to the mission network. Kernel mode keeps host
    semantics; the sidecar contains the same quirk inside a near-empty namespace."""
    s = _script()
    auto = s[s.index("  auto)") : s.index("  sidecar | native) ;;")]
    kernel = auto.index('[ "$(id -u)" = "0" ] && [ -w /dev/net/tun ]')
    assert kernel < auto.index('[ "$HAVE_DOCKER" = yes ]')
    assert auto.count("TS_MODE=native") == 2  # kernel first, host-userspace last
    assert "TS_MODE=sidecar" in auto


def test_native_userspace_binds_the_proxy_off_netstacks_forward_target():
    """netstack delivers inbound tailnet connections to 127.0.0.1. Binding the proxy there would
    hand mission code a relay carrying the agent's tailnet identity; 127.0.0.2 is still
    loopback-only to the host but is not what netstack forwards to."""
    s = _script()
    assert "PROXY_ADDR=127.0.0.2" in s
    assert "PROXY_ADDR=127.0.0.1" in s  # Darwin fallback (no lo0 alias); diagnostic path only
    assert "socks5-server=127.0.0.1:" not in s


def test_host_userspace_warns_that_loopback_is_exposed():
    s = _script()
    assert "listening on 127.0.0.1 is reachable from the mission network" in s


def test_linux_sidecar_maps_host_docker_internal_to_the_gateway():
    """The in-container reaper polls run-control through that name; a Linux daemon does not
    resolve it for free, and without it the sidecar leaks until its hard cap."""
    s = _script()
    assert "--add-host=host.docker.internal:host-gateway" in s


def test_both_native_modes_say_how_to_accept_connections():
    s = _script()
    assert s.count("to ACCEPT connections, bind a port on this host") == 2


def test_the_sidecar_proxy_bind_fails_closed_rather_than_going_wildcard():
    """`[ -n "$bip" ] || bip=0.0.0.0` undid the whole point of the contained bind. In
    userspace-networking mode netstack delivers inbound tailnet connections to 127.0.0.1 INSIDE
    the sidecar, so a wildcard bind publishes the SOCKS5/HTTP proxy ON the tailnet — and with
    agent ingress now open, that hands mission code a working relay carrying the agent's identity.
    The join printed success either way, so nobody would have found out."""
    s = _script()
    assert "bip=0.0.0.0" not in s
    body = s[s.index("bip=$(ip -4 -o addr show") :]
    assert "exit 1" in body[: body.index("tailscaled --socks5-server")]


def test_auto_falls_back_to_native_when_the_sidecar_cannot_start():
    """The sidecar image comes from Docker Hub; the native client comes from run-control, which
    caches it precisely for the air-gapped host. So the offline host is exactly where the sidecar
    cannot start — and `auto` preferring the sidecar with no fall-through made the join impossible
    there, on a path the script itself advertises."""
    s = _script()
    fail = s[s.index('if ! "$@" >/dev/null; then') :]
    fail = fail[: fail.index('if [ "$TS_MODE" = sidecar ]; then')]
    assert 'TS_FALLBACK" = yes' in fail
    assert "TS_MODE=native" in fail


def test_an_explicitly_requested_sidecar_never_falls_back():
    """An operator naming the mode is diagnosing something and wants the failure, not a quiet
    downgrade to the less contained path."""
    s = _script()
    auto = s[s.index("  auto)") : s.index("  sidecar | native) ;;")]
    assert auto.count("TS_FALLBACK=yes") == 1  # set only where `auto` chose the sidecar
    assert "TS_FALLBACK=no" in s[: s.index("  auto)")]


def test_the_docker_daemon_is_probed_once_per_join():
    """`docker info` on an unreachable daemon blocks for its full timeout, and this was asked
    twice on every join — once to detect the mode, once to guard the sidecar branch."""
    assert _script().count("docker info >/dev/null 2>&1") == 1


def test_the_portable_socks_handle_is_written_in_every_proxy_mode():
    """XORCISE_SOCKS5 is the mode-independent handle: the sidecar path always wrote it, and the
    userspace path now does too, so an agent keying off it is portable across modes. This PR made
    `auto` pick the sidecar on non-root Linux where it used to pick userspace, so the two paths'
    env files must expose the same handle or an agent's config silently stops resolving."""
    s = _script()
    # sidecar block
    assert "export XORCISE_SOCKS5=127.0.0.1:$SOCKS" in s
    # userspace block — additive; the standard ALL_PROXY convenience stays
    assert "export XORCISE_SOCKS5=$PROXY_ADDR:$SOCKS" in s
    assert "export ALL_PROXY=socks5://$PROXY_ADDR:$SOCKS" in s
