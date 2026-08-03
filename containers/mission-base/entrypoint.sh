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

# 1. Docker daemon. Linux uses the original isolated DinD runtime. On macOS the runner mounts
# Docker Desktop's socket at this explicit path: nested amd64 containers cannot start through
# Rosetta (the outer amd64 image can), so compose the exact same stack as host-daemon siblings.
if [ -S /var/run/docker-host.sock ]; then
    export DOCKER_HOST=unix:///var/run/docker-host.sock
else
    dockerd-entrypoint.sh dockerd >/var/log/dockerd.log 2>&1 &
    until docker info >/dev/null 2>&1; do sleep 1; done
fi

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

# In host-daemon mode compose children are siblings, not children of this process. Keep the
# run's deterministic outer container alive as the lifecycle handle used by status/teardown.
if [ -S /var/run/docker-host.sock ]; then
    while sleep 3600; do :; done
else
    wait
fi
