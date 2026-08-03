"""The Headscale CLI seam — the ONLY code that shells out to headscale.

Mirrors the DockerDriver precedent (runner/docker): an ABC + an in-memory stub for
unit tests + a real `docker exec` adapter. The JSON-parsing helpers are split out as
pure functions so they unit-test against captured fixtures with no live Docker.
Lifted from the networking PoC (runner/network_controller.py).
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence


class HeadscaleError(RuntimeError):
    pass


def parse_user_id(users_json: Sequence[Mapping[str, object]], username: str) -> int | None:
    for u in users_json:
        if u.get("name") == username:
            return int(str(u["id"]))
    return None


def parse_nodes_for_user(nodes_json: Sequence[Mapping[str, object]], username: str) -> list[int]:
    out: list[int] = []
    for n in nodes_json:
        user = n.get("user", {})
        name = user.get("name", "") if isinstance(user, dict) else str(user)
        if name == username:
            out.append(int(str(n["id"])))
    return out


def parse_node_online_for_user(nodes_json: Sequence[object], username: str) -> bool:
    """True iff `username` owns >=1 node in the `nodes list` JSON reporting `online: true`.

    Never raises: a malformed entry (not a dict, missing "online"/"user") just doesn't count
    towards "online" rather than blowing up the caller."""
    for n in nodes_json:
        if not isinstance(n, Mapping):
            continue
        user = n.get("user", {})
        name = user.get("name", "") if isinstance(user, Mapping) else str(user)
        if name == username and bool(n.get("online", False)):
            return True
    return False


def parse_node_online_by_name(nodes_json: Sequence[object], name: str) -> bool:
    """True iff the node called `name` reports `online: true` in the `nodes list` JSON.

    By NAME, not by user: the per-run subnet router joins as the shared orchestrator user, so the
    per-user probe cannot single it out. Never raises — a malformed entry or an absent node simply
    isn't online, so a readiness poll degrades to "not yet" instead of blowing up."""
    for n in nodes_json:
        if not isinstance(n, Mapping):
            continue
        if n.get("name") == name or n.get("given_name") == name:
            return bool(n.get("online", False))
    return False


def parse_node_id_by_name(nodes_json: Sequence[Mapping[str, object]], name: str) -> int | None:
    """Resolve a node's id by its (given) name — used to delete the per-run router node, which is
    owned by the shared orchestrator user and so is not addressable via parse_nodes_for_user."""
    for n in nodes_json:
        if n.get("name") == name or n.get("given_name") == name:
            return int(str(n["id"]))
    return None


class HeadscaleCli(ABC):
    @abstractmethod
    def version(self) -> str:
        """Cheap liveness probe — returns the control-plane version string, raises
        HeadscaleError if it cannot be reached. Lets callers fail loud with a remediation."""
        ...

    @abstractmethod
    def create_user(self, username: str) -> None: ...

    @abstractmethod
    def delete_user(self, username: str) -> None: ...

    @abstractmethod
    def create_preauth_key(
        self,
        user: str,
        *,
        reusable: bool = False,
        expiration: str = "1h",
        tags: Sequence[str] = (),
    ) -> str: ...

    @abstractmethod
    def apply_acl_policy(self, policy_text: str) -> None: ...

    @abstractmethod
    def delete_nodes_for_user(self, username: str) -> int: ...

    @abstractmethod
    def delete_node_by_name(self, name: str) -> bool:
        """Delete a single node by its (given) name. Returns True if one was deleted."""
        ...

    @abstractmethod
    def node_online(self, user: str) -> bool:
        """True iff `user` has >=1 registered node reporting online:true. False (never raises)
        if the user has no nodes, or the underlying query is missing/malformed — lets the
        terrain infra plane confirm the agent has actually joined the tailnet."""
        ...

    def node_online_by_name(self, name: str) -> bool:
        """True iff the node called `name` is online. False (never raises) when it is absent or the
        query fails — lets the readiness gate confirm the per-run ROUTER joined the tailnet, which
        the per-user probe cannot (the router joins as the shared orchestrator user). Non-abstract
        so existing HeadscaleCli implementations keep satisfying the ABC."""
        return False


class StubHeadscaleCli(HeadscaleCli):
    """In-memory, no Docker. Records calls for assertions."""

    def __init__(self) -> None:
        self.users_created: list[str] = []
        self.users_deleted: list[str] = []
        self.keys_minted: list[str] = []
        self.preauth_calls: list[tuple[str, tuple[str, ...]]] = []  # (user, tags) per mint
        self.policies_applied: list[str] = []
        self.nodes_deleted_by_name: list[str] = []
        self.online_users: set[str] = set()  # settable by tests; no live Docker
        self.online_nodes: set[str] = set()  # node NAMES reporting online (e.g. the run's router)
        self._counter = 0

    def version(self) -> str:
        return "stub"

    def create_user(self, username: str) -> None:
        if username not in self.users_created:
            self.users_created.append(username)

    def delete_user(self, username: str) -> None:
        self.users_deleted.append(username)

    def create_preauth_key(
        self,
        user: str,
        *,
        reusable: bool = False,
        expiration: str = "1h",
        tags: Sequence[str] = (),
    ) -> str:
        self._counter += 1
        key = f"stub-key-{user}-{self._counter}"
        self.keys_minted.append(key)
        self.preauth_calls.append((user, tuple(tags)))
        return key

    def apply_acl_policy(self, policy_text: str) -> None:
        self.policies_applied.append(policy_text)

    def delete_nodes_for_user(self, username: str) -> int:
        return 0

    def delete_node_by_name(self, name: str) -> bool:
        self.nodes_deleted_by_name.append(name)
        return True

    def node_online(self, user: str) -> bool:
        return user in self.online_users

    def node_online_by_name(self, name: str) -> bool:
        return name in self.online_nodes


class DockerExecHeadscaleCli(HeadscaleCli):
    """Real adapter: `docker exec -i <container> headscale <args>` (Headscale v0.28.x).

    Version-specific behaviours: `preauthkeys create -u` wants the numeric user id;
    the distroless image has no shell, so policy is staged via `docker cp` then
    `policy check` -> `policy set` (atomic in database mode).
    """

    def __init__(self, container: str = "headscale") -> None:
        self.container = container

    def version(self) -> str:
        return self._exec("version").strip()

    def _exec(self, *args: str, stdin: str | None = None) -> str:
        cmd = ["docker", "exec", "-i", self.container, "headscale", *args]
        # start_new_session: an operator Ctrl-C aimed at the server must never SIGINT a
        # mid-flight control-plane write — the docker-exec client would die (exit 255)
        # AFTER the daemon-side change already applied, misreporting success as failure.
        proc = subprocess.run(
            cmd, input=stdin, capture_output=True, text=True, start_new_session=True
        )
        if proc.returncode != 0:
            # Label the streams and never present stdout alone as the failure reason —
            # stdout can be a success message from an operation that completed.
            raise HeadscaleError(
                f"headscale {' '.join(args)} failed (exit {proc.returncode}); "
                f"stderr: {proc.stderr.strip() or '<empty>'}; "
                f"stdout: {proc.stdout.strip() or '<empty>'}"
            )
        return proc.stdout

    def _exec_json(self, *args: str) -> list[dict[str, object]]:
        out = self._exec(*args, "-o", "json").strip()
        if not out:
            return []
        # `headscale ... list -o json` prints the literal `null` (not `[]`) for an empty result,
        # which json-decodes to None; coerce any non-list payload to [] so callers can always
        # iterate the result safely.
        data = json.loads(out)
        return data if isinstance(data, list) else []

    def create_user(self, username: str) -> None:
        if parse_user_id(self._exec_json("users", "list"), username) is not None:
            return
        self._exec("users", "create", username)

    def delete_user(self, username: str) -> None:
        if parse_user_id(self._exec_json("users", "list"), username) is None:
            return
        self._exec("users", "destroy", "--name", username, "--force")

    def create_preauth_key(
        self,
        user: str,
        *,
        reusable: bool = False,
        expiration: str = "1h",
        tags: Sequence[str] = (),
    ) -> str:
        uid = parse_user_id(self._exec_json("users", "list"), user)
        if uid is None:
            raise HeadscaleError(f"Cannot mint key: user {user!r} does not exist")
        args = ["preauthkeys", "create", "-u", str(uid), "-e", expiration]
        if reusable:
            args.append("--reusable")
        if tags:
            # ACL-tag the key so the node that joins with it carries the tag (e.g. the router
            # tag the autoApprovers policy approves routes for). Headscale wants repeated --tags.
            for tag in tags:
                args += ["--tags", tag]
        out = self._exec(*args, "-o", "json").strip()
        data = json.loads(out)
        return str(data["key"])

    def apply_acl_policy(self, policy_text: str) -> None:
        path = "/var/lib/headscale/xorcise-acl.hujson"
        with tempfile.NamedTemporaryFile("w", suffix=".hujson", delete=False) as tf:
            tf.write(policy_text)
            host_path = tf.name
        try:
            cp = subprocess.run(
                ["docker", "cp", host_path, f"{self.container}:{path}"],
                capture_output=True,
                text=True,
                start_new_session=True,
            )
            if cp.returncode != 0:
                raise HeadscaleError(f"Failed to stage policy file: {cp.stderr}")
            self._exec("policy", "check", "-f", path)
            self._exec("policy", "set", "-f", path)
        finally:
            os.unlink(host_path)

    def delete_nodes_for_user(self, username: str) -> int:
        node_ids = parse_nodes_for_user(self._exec_json("nodes", "list"), username)
        for nid in node_ids:
            self._exec("nodes", "delete", "-i", str(nid), "--force")
        return len(node_ids)

    def delete_node_by_name(self, name: str) -> bool:
        nid = parse_node_id_by_name(self._exec_json("nodes", "list"), name)
        if nid is None:
            return False
        self._exec("nodes", "delete", "-i", str(nid), "--force")
        return True

    def node_online(self, user: str) -> bool:
        try:
            nodes = self._exec_json("nodes", "list")
        except (HeadscaleError, json.JSONDecodeError):
            return False
        return parse_node_online_for_user(nodes, user)

    def node_online_by_name(self, name: str) -> bool:
        try:
            nodes = self._exec_json("nodes", "list")
        except (HeadscaleError, json.JSONDecodeError):
            return False
        return parse_node_online_by_name(nodes, name)
