#!/bin/sh
# Fused per-mission image entrypoint. Boots the inner Docker daemon,
# loads the baked stack (no inner pull), renders the per-run compose net-override, and brings
# up the mission PLUS a Tailscale router as a SEPARATE inner container (its own clean netns —
# NOT tailscaled in this outer netns, which collides with the inner dockerd). Env (set by the
# runner at deploy):
#   XORCISE_PROJECT            compose project name (== run id)
#   XORCISE_LOGIN_SERVER       Headscale URL (router logs in to this)
#   XORCISE_AUTHKEY            per-run pre-auth key (minted by the fence)
#   XORCISE_ROUTES             comma-separated CIDR(s) the router advertises
#   XORCISE_NET_OVERRIDE_B64   base64'd compose override (mission nets + the router service)
# The override references the secrets as ${XORCISE_*}; compose interpolates them from this env
# at `up` time, so the auth key never lands on disk inside the override file.
set -eu

# 1. Inner Docker daemon. The mission stack ALWAYS runs on it — there is no host-daemon
# "sibling" mode any more. That mode composed the stack on the operator's own daemon, so parallel
# runs collided on missions' fixed container_names and published host ports, teardown leaked
# un-labelled containers, and bind mounts resolved against the wrong filesystem. The runner no
# longer mounts the host socket; if one is somehow present it is ignored.
#
# docker:*-dind ships ENV DOCKER_HOST=tcp://docker:2375 for the compose "dind sidecar" pattern.
# `dockerd-entrypoint.sh dockerd` does NOT consult it (that branch only runs when called with no
# args), so the daemon listens on the default unix socket while this shell's client dials a host
# named `docker` that does not exist — the wait below would then never succeed. Clearing it is
# what makes this work at all.
unset DOCKER_HOST
dockerd-entrypoint.sh dockerd >/var/log/dockerd.log 2>&1 &
# Bounded: an unbounded wait turned any daemon that could never come up into a silent hang for
# the whole run, with no diagnosis anywhere. 120s is far above a normal boot (~1-10s).
waited=0
until docker info >/dev/null 2>&1; do
    waited=$((waited + 1))
    if [ "$waited" -gt 120 ]; then
        echo "xorcise: inner dockerd did not come up within 120s" >&2
        tail -50 /var/log/dockerd.log >&2 || true
        exit 1
    fi
    sleep 1
done

# 2. preload the baked inner stack (mission services + the Tailscale router image)
[ -f /mission/images.tar ] && docker load -i /mission/images.tar

# 3. render the per-run net-override (mission subnets + the router inner container)
printf '%s' "$XORCISE_NET_OVERRIDE_B64" | base64 -d > /mission/net-override.yml

# 3b. air-gapped: if a Headscale TLS CA was delivered, write it where the override
#     mounts it into the router (so the router trusts the self-signed control cert).
if [ -n "${XORCISE_HEADSCALE_CA_B64:-}" ]; then
    printf '%s' "$XORCISE_HEADSCALE_CA_B64" | base64 -d > /mission/headscale-ca.pem
fi

# 4. bring up the mission + the router (compose interpolates ${XORCISE_AUTHKEY} etc. from env)
docker compose -p "${XORCISE_PROJECT:-mission}" \
    -f /mission/docker-compose.yml -f /mission/net-override.yml up -d

# Block on the inner dockerd so this container stays alive as the run's lifecycle handle (the
# one status/teardown address by name == run id). The mission containers are its children, so
# they die with it — which is exactly why teardown no longer has to chase siblings.
wait
