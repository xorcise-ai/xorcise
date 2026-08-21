# Releasing mission-base

mission-base is released from this repository to `ghcr.io/xorcise-ai/mission-base` by
pushing a `mission-base-v<MAJOR>.<MINOR>.<PATCH>` tag. The release workflow verifies the
tag against the Dockerfile's own version label, builds every supported platform, publishes
ONE multi-platform index under the version tag, and re-verifies what it published.

That is where this repository's job ends. **Releases publish; deployments discover.**
Nothing here notifies or promotes into any consumer — deployments watch the registry for
new versions and adopt a release through their own review process. This is deliberate:
it means this repository holds no deployment URL, no credential, and no secret, and no
automated hand ever rests on a consumer's adoption lever.

## What the version number means

The version belongs to this project, not to any mission. Missions carry their own
versions; the two never interact except through the compatibility MAJOR.

**MAJOR** — the compatibility gate. Bump it when a change breaks the contract between
mission-base and either the missions fused onto it or the client that runs them: the
in-image layout (`/mission`, the entrypoint's compose handling), the container runtime
it embeds, or any behaviour a fused mission observes at boot. Clients pin to the MAJOR;
a new MAJOR means every published mission must eventually be re-fused and older clients
refuse it — so a MAJOR bump is a project event, not a Tuesday.

**MINOR** — new capability, compatible. A new tool in the image, a new optional
environment knob, a runtime upgrade that changes no observable behaviour for existing
missions.

**PATCH** — fixes only. Security patches to packages inside the image, entrypoint bug
fixes, size or speed improvements. Nothing a mission or client could notice except
things working.

**No bump at all** for changes that do not alter the shipped image: CI plumbing,
comments, docs (including this file), test changes. A version whose bytes are identical
to the previous one is a claim of difference that isn't there — if the image didn't
change, the version doesn't either.

## The mechanics

1. Update `containers/mission-base/Dockerfile`'s `ai.xorcise.base.version` MAJOR label
   if (and only if) this release bumps the MAJOR — the workflow refuses a tag whose
   MAJOR disagrees with the label.
2. Tag: `git tag mission-base-v2.5.0 && git push origin mission-base-v2.5.0`.
3. The workflow builds, publishes and verifies. Watch the run; a red `inspect` means
   the published artifact does not match what this file promised.

A published version is immutable. A mistake ships as the next PATCH, never as a
re-push of the same tag.
