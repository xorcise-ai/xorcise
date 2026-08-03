import pytest

from xorcise.core.contracts.connect import ConnectTarget, LaunchProfile, MissionPrompt
from xorcise.core.runs.prompt import (
    assemble_mission_prompt,
    build_launch_profile,
    rebase_run_control_host,
    render_prompt_text,
)


def _mission() -> MissionPrompt:
    return assemble_mission_prompt(
        run_id="run-1",
        mission="sqli-login",
        objective="Bypass the login.",
        login_server="https://headscale.local",
        join_key="tskey-abc",
        run_control_url="https://control.local/runs/run-1",
        run_control_key="rk-xyz",
        targets=[ConnectTarget(name="web", host="10.200.1.2", port=80)],
    )


def test_mission_prompt_has_run_control_key_and_no_otel():
    p = _mission()
    assert p.run_control_key == "rk-xyz"
    dumped = p.model_dump()
    assert "otlp" not in dumped and "otel" not in dumped


def test_launch_profile_shape_defaults_empty():
    assert LaunchProfile().env == ()
    assert LaunchProfile(env=(("OTEL_X", "1"),)).env == (("OTEL_X", "1"),)


def test_assemble_carries_core_fields():
    p = _mission()
    assert p.run_id == "run-1"
    assert p.join_key == "tskey-abc"
    assert p.run_control_url.endswith("/runs/run-1")
    assert p.targets[0].host == "10.200.1.2"


def test_render_states_no_intel_when_none_available():
    text = render_prompt_text(_mission())  # intel_available defaults to 0
    assert "No intel is available for this run." in text
    assert "GET  /intel" not in text  # the /intel advert is dropped


def test_render_advertises_intel_count_when_available():
    mission = assemble_mission_prompt(
        run_id="run-1",
        mission="sqli-login",
        objective="Bypass the login.",
        login_server="https://headscale.local",
        join_key="tskey-abc",
        run_control_url="https://control.local/runs/run-1",
        run_control_key="rk-xyz",
        targets=[ConnectTarget(name="web", host="10.200.1.2", port=80)],
        intel_available=2,
    )
    text = render_prompt_text(mission)
    assert "Intel available: 2" in text
    assert "GET /intel requests the next one." in text
    assert "No intel are available for this run." not in text


def test_assemble_carries_artifacts():
    from xorcise.core.contracts.connect import ConnectArtifact

    p = assemble_mission_prompt(
        run_id="run-1",
        mission="sqli-login",
        objective="Bypass the login.",
        login_server="https://headscale.local",
        join_key="tskey-abc",
        run_control_url="http://host.docker.internal:3001/api/runs/run-1",
        run_control_key="rk-xyz",
        targets=[ConnectTarget(name="web", host="10.200.1.2", port=80)],
        artifacts=[
            ConnectArtifact(name="flag", required=True, description="The XORCISE{...} value."),
            ConnectArtifact(name="writeup", required=False, description="Your payloads."),
        ],
    )
    assert p.artifacts[0].name == "flag" and p.artifacts[0].required is True
    assert p.artifacts[1].name == "writeup" and p.artifacts[1].required is False
    # back-compat: omitting artifacts yields an empty tuple
    assert _mission().artifacts == ()


def _mission_with_artifacts() -> MissionPrompt:
    from xorcise.core.contracts.connect import ConnectArtifact

    return assemble_mission_prompt(
        run_id="run-1",
        mission="sqli-login",
        objective="Bypass the login.",
        login_server="https://headscale.local",
        join_key="tskey-abc",
        run_control_url="http://host.docker.internal:3001/api/runs/run-1",
        run_control_key="rk-xyz",
        targets=[ConnectTarget(name="web", host="10.200.1.2", port=80)],
        artifacts=[
            ConnectArtifact(name="flag", required=True, description="The XORCISE{...} value."),
            ConnectArtifact(name="writeup", required=False, description="Your payloads."),
        ],
    )


def _mission_with_attachments() -> MissionPrompt:
    from xorcise.core.contracts.connect import ConnectAttachment

    return assemble_mission_prompt(
        run_id="run-1",
        mission="sqli-login",
        objective="Bypass the login.",
        login_server="https://headscale.local",
        join_key="tskey-abc",
        run_control_url="http://host.docker.internal:3001/api/runs/run-1",
        run_control_key="rk-xyz",
        targets=[ConnectTarget(name="web", host="10.200.1.2", port=80)],
        attachments=[
            ConnectAttachment(name="notes.txt", media_type="text/plain"),
            ConnectAttachment(name="capture.pcap"),
        ],
    )


def test_assemble_carries_attachments():
    # The manifest's companion files are surfaced so a prompt-only agent knows what to
    # download and can mint the signed link by name.
    p = _mission_with_attachments()
    assert p.attachments[0].name == "notes.txt" and p.attachments[0].media_type == "text/plain"
    assert p.attachments[1].name == "capture.pcap" and p.attachments[1].media_type is None
    # back-compat: omitting attachments yields an empty tuple
    assert _mission().attachments == ()


def test_render_lists_attachments_and_download_recipe():
    # The prompt names each companion file and gives the signed-link recipe (GET
    # /attachments/<name> with the Bearer returns a short-lived signed url, fetched with the
    # per-run X-Run-Key header). Bytes never ride the run-control channel.
    text = render_prompt_text(_mission_with_attachments())
    assert "notes.txt" in text and "text/plain" in text  # named + typed
    assert "capture.pcap" in text  # untyped file still listed
    assert "/attachments/<name>" in text  # the download endpoint
    assert "signed url" in text  # step 1 yields a short-lived signed link
    assert "X-Run-Key" in text  # the download header name (route requires it; not the Bearer)
    # the per-run key is shown ONCE (the Bearer line) — the attachment recipe references it by
    # name ("your run key"), it does NOT re-print the secret value.
    assert text.count("rk-xyz") == 1


def test_render_omits_attachment_section_when_no_attachments():
    # A mission with no companion files must not grow an attachments recipe (and keeps the
    # X-Run-Key out of the prompt, as the existing recipe promises).
    text = render_prompt_text(_mission_with_artifacts())
    assert "/attachments/" not in text
    assert "Companion files" not in text
    assert "X-Run-Key" not in text


def test_render_join_recipe_is_one_command_and_inlines_no_secret():
    # Join ergonomics: the recipe is a single `curl <base>/join.sh | sh`; run-control serves the
    # self-contained script. The one-time key is never inlined (it rides the authed call).
    text = render_prompt_text(_mission())
    assert "/join.sh" in text  # fetch-and-run the served join script
    assert "| sh" in text  # piped to a shell — one command
    assert "host has NO direct route to target IPs" in text
    assert "nc -X 5 -x <host:port>" in text
    assert "tskey-abc" not in text  # authkey value never inlined
    assert "Authorization: Bearer rk-xyz" in text  # the (single) bearer, in the run-control step
    assert "10.200.1.2:80" in text
    assert "otlp" not in text.lower()  # no telemetry endpoint in the mission section


def test_render_runcontrol_uses_bearer_not_xrunkey():
    text = render_prompt_text(_mission_with_artifacts())
    assert "Authorization: Bearer rk-xyz" in text
    assert "X-Run-Key" not in text


def test_render_runcontrol_gives_full_base_url():
    text = render_prompt_text(_mission_with_artifacts())
    assert "http://host.docker.internal:3001/api/runs/run-1" in text


def test_render_runcontrol_lists_endpoint_verbs():
    # /intel is advertised only when the run may disclose intel (per-run intel control), so render a
    # intel-enabled mission here to assert the full endpoint set.
    mission = _mission_with_artifacts().model_copy(update={"intel_available": 1})
    text = render_prompt_text(mission)
    assert "POST" in text and "/artifacts" in text
    assert "/complete" in text
    assert "/mission" in text and "/intel" in text
    # the submission body format is shown
    assert '"name"' in text and '"content"' in text


def test_render_runcontrol_lists_expected_artifacts():
    text = render_prompt_text(_mission_with_artifacts())
    assert "flag" in text and "writeup" in text


def test_render_runcontrol_never_mentions_removed_flag_endpoint():
    # /flag was removed; the flag is submitted as the artifact named "flag".
    text = render_prompt_text(_mission_with_artifacts())
    assert "runs/run-1/flag" not in text
    assert "/flag" not in text


def test_render_has_no_telemetry_env():
    # the harness owns telemetry — no OTLP endpoint / exporter env in the agent prompt.
    text = render_prompt_text(_mission())
    assert "OTEL_" not in text
    assert "otlp" not in text.lower()
    assert "Launch profile" not in text
    # but the correlation marker stays (a deliberate keep)
    assert "xorcise.run_id=run-1" in text


_CA_PEM = "-----BEGIN CERTIFICATE-----\nMIIBfake\n-----END CERTIFICATE-----\n"


def _mission_airgapped() -> MissionPrompt:
    return assemble_mission_prompt(
        run_id="run-1",
        mission="sqli-login",
        objective="Bypass the login.",
        login_server="https://headscale.local:8443",
        join_key="tskey-abc",
        run_control_url="https://control.local/runs/run-1",
        run_control_key="rk-xyz",
        targets=[ConnectTarget(name="web", host="10.200.1.2", port=80)],
        ca_cert=_CA_PEM,
        host_alias="headscale.local:172.17.0.1",
    )


def test_assemble_carries_ca_and_host_alias():
    p = _mission_airgapped()
    assert p.ca_cert == _CA_PEM
    assert p.host_alias == "headscale.local:172.17.0.1"
    # default (plain) leaves them empty — back-compat
    assert _mission().ca_cert == "" and _mission().host_alias == ""


def test_join_flags_moved_out_of_the_prompt_into_the_served_script():
    # The join flags (--accept-routes for the mission CIDR / --accept-dns=false so the tailnet
    # never wipes the agent's DNS) now live in the served join.sh, not the prompt.
    # The invariants themselves are asserted in test_join_script.py.
    for mission in (_mission(), _mission_airgapped()):
        text = render_prompt_text(mission)
        assert "--accept-routes" not in text
        assert "--accept-dns=false" not in text
        assert "/join.sh" in text  # the recipe delegates the whole join to the served script


def test_render_target_is_bare_ip_no_name():
    # targets render as bare IP:port (no service name) — the agent reaches them by the
    # tailnet-routed IP; a compose service name would not resolve. No fabricated port either.
    mission = assemble_mission_prompt(
        run_id="run-1",
        mission="c",
        objective="hit the target",
        login_server="https://hs",
        join_key="k",
        run_control_url="https://b/runs/run-1",
        run_control_key="rk",
        targets=[ConnectTarget(name="web", host="10.200.20.10")],
    )
    text = render_prompt_text(mission)
    assert "10.200.20.10" in text
    assert "web  10.200.20.10" not in text  # no service-name column
    assert "10.200.20.10:" not in text  # no fabricated port


def test_render_substitutes_target_ip_placeholder_in_objective():
    # the manifest objective uses <name-target-ip-> (never a hostname); the prompt fills
    # in the routed IP so the agent hits the target by address, not an unresolvable name.
    mission = assemble_mission_prompt(
        run_id="run-1",
        mission="idor",
        objective="You are at http://<web-target-ip->:80. Exploit it.",
        login_server="https://hs",
        join_key="k",
        run_control_url="https://b/runs/run-1",
        run_control_key="rk",
        targets=[ConnectTarget(name="web", host="10.200.16.10", port=80)],
    )
    text = render_prompt_text(mission)
    assert "http://10.200.16.10:80" in text  # placeholder resolved to the IP
    assert "<web-target-ip->" not in text  # no leftover placeholder
    assert "http://web:80" not in text  # no unresolvable hostname


def test_render_airgapped_recipe_inlines_no_ca_or_hosts_edit():
    # Air-gapped: the CA + one-time key ride the served join.sh (authed), never the prompt, and the
    # recipe still never edits /etc/hosts (the login server is by-IP with an IP-SAN cert).
    text = render_prompt_text(_mission_airgapped())
    assert _CA_PEM.strip() not in text  # PEM never inlined
    assert "BEGIN CERTIFICATE" not in text
    assert "/join.sh" in text  # fetch-and-run the served script
    assert "/etc/hosts" not in text  # NO hosts injection anywhere
    # The CA-trust + install mechanics live in the script now, not the prompt.
    assert "SSL_CERT_FILE" not in text
    assert "install.sh" not in text


def test_render_prompt_omits_ca_material():
    # The prompt carries no CA/daemon mechanics regardless of air-gap — that's all in join.sh.
    for text in (render_prompt_text(_mission()), render_prompt_text(_mission_airgapped())):
        assert "SSL_CERT_FILE" not in text
        assert "BEGIN CERTIFICATE" not in text


def _mission_static() -> MissionPrompt:
    # A static (attachment-only) run: no tailnet (join_key=""), no targets. Only the run-control
    # + attachment surface applies.
    from xorcise.core.contracts.connect import ConnectArtifact, ConnectAttachment

    return assemble_mission_prompt(
        run_id="run-s",
        mission="derelict-manifest",
        objective="Reverse the binary and submit the flag.",
        login_server="",
        join_key="",
        run_control_url="http://host.docker.internal:3001/api/runs/run-s",
        run_control_key="rk-s",
        targets=[],
        artifacts=[ConnectArtifact(name="flag", required=True, description="the FLAG{...}")],
        attachments=[ConnectAttachment(name="attachment.zip", media_type="application/zip")],
    )


def test_render_static_omits_tailnet_and_targets() -> None:
    # static-mission-support: with no join key and no targets, the prompt must NOT tell the agent
    # to join a tailnet or reach any target — there is no runtime environment.
    text = render_prompt_text(_mission_static())
    assert "/join.sh" not in text  # no tailnet join recipe
    assert "| sh" not in text
    assert "Targets (" not in text  # no targets section
    assert "target-ip" not in text


def test_render_static_keeps_artifacts_attachments_and_termination() -> None:
    # The static agent still submits artifacts, downloads attachments, and terminates the run.
    text = render_prompt_text(_mission_static())
    assert "POST" in text and "/artifacts" in text  # submit findings
    assert "/complete" in text  # terminate the run
    assert "/attachments/<name>" in text and "attachment.zip" in text  # download companion file
    assert "xorcise.run_id=run-s" in text  # OTel correlation marker preserved


def test_render_lab_still_has_join_and_targets() -> None:
    # Regression: the lab path is unchanged — join recipe + Targets section both render.
    text = render_prompt_text(_mission())
    assert "/join.sh" in text
    assert "Targets (" in text
    assert "10.200.1.2" in text


@pytest.mark.unit
def test_render_carries_run_id_sentinel_for_otel_correlation() -> None:
    mission = assemble_mission_prompt(
        run_id="run-abc123",
        mission="c1",
        objective="obj",
        login_server="https://headscale.local:8443",
        join_key="k",
        run_control_url="https://server/runs/run-abc123",
        run_control_key="rk",
        targets=[],
    )
    text = render_prompt_text(mission)
    # the collector's prompt-derived fallback greps this exact marker:
    assert "xorcise.run_id=run-abc123" in text


# ---------------------------------------------------------------------------
# build_launch_profile carries the static OTLP endpoint
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_build_launch_profile_with_endpoint_has_three_otel_pairs() -> None:
    lp = build_launch_profile("http://172.17.0.1:4318")
    keys = [k for k, _v in lp.env]
    assert ("OTEL_EXPORTER_OTLP_ENDPOINT", "http://172.17.0.1:4318") in lp.env
    assert ("OTEL_EXPORTER_OTLP_PROTOCOL", "http/protobuf") in lp.env
    assert ("OTEL_TRACES_EXPORTER", "otlp") in lp.env
    assert len(lp.env) == 3
    # no RESOURCE_ATTRIBUTES, no run_id
    assert not any("RESOURCE_ATTRIBUTES" in k for k in keys)
    assert not any("run_id" in k.lower() for k in keys)


@pytest.mark.unit
def test_build_launch_profile_empty_stays_empty() -> None:
    assert build_launch_profile().env == ()
    assert build_launch_profile("").env == ()


@pytest.mark.unit
def test_prompt_includes_add_host_recipe_when_runcontrol_is_docker_internal():
    # the host-gateway intel is keyed to run-control reachability, not OTel.
    mission = assemble_mission_prompt(
        run_id="run-1",
        mission="c",
        objective="obj",
        login_server="https://hs",
        join_key="k",
        run_control_url="http://host.docker.internal:3001/api/runs/run-1",
        run_control_key="rk",
        targets=[],
    )
    text = render_prompt_text(mission)
    assert "--add-host host.docker.internal:host-gateway" in text


@pytest.mark.unit
def test_prompt_omits_add_host_recipe_when_runcontrol_is_tailnet():
    mission = assemble_mission_prompt(
        run_id="run-1",
        mission="c",
        objective="obj",
        login_server="https://hs",
        join_key="k",
        run_control_url="http://10.0.0.5:3001/api/runs/run-1",
        run_control_key="rk",
        targets=[],
    )
    text = render_prompt_text(mission)
    assert "--add-host" not in text


# ---------------------------------------------------------------------------
# GUI toggle: the persisted prompt bakes ONE run-control host (the
# container-facing advertise_host for a dual/generic harness). When the operator
# flips the launch-mode toggle to "this host", rebase_run_control_host must move
# the run-control Base URL — and the container-only --add-host note — to the
# loopback host, exactly as if the prompt had been rendered for host mode.
# ---------------------------------------------------------------------------


def _rebase_mission(run_control_url: str) -> MissionPrompt:
    from xorcise.core.contracts.connect import ConnectArtifact, ConnectAttachment

    return assemble_mission_prompt(
        run_id="run-1",
        mission="chrono-canary",
        objective="Reach http://<svc-target-ip->:1337 and read the flag.",
        login_server="https://headscale.local",
        join_key="tskey-abc",
        run_control_url=run_control_url,
        run_control_key="rk-xyz",
        targets=[ConnectTarget(name="svc", host="10.200.1.10", port=1337)],
        artifacts=[ConnectArtifact(name="flag", required=True, description="the FLAG{...}")],
        attachments=[ConnectAttachment(name="attachment.zip", media_type="application/zip")],
    )


@pytest.mark.unit
def test_rebase_container_to_host_equals_native_host_render() -> None:
    # The invariant that makes read-time rebasing safe: swapping the baked container host for the
    # loopback host must produce byte-for-byte what render_prompt_text would emit if the mission had
    # been assembled with the host-mode run-control URL to begin with. If the renderer ever grows a
    # second mode-dependent line, this equivalence breaks and we learn immediately.
    container = render_prompt_text(
        _rebase_mission("http://host.docker.internal:3001/api/runs/run-1")
    )
    native_host = render_prompt_text(_rebase_mission("http://127.0.0.1:3001/api/runs/run-1"))
    rebased = rebase_run_control_host(
        container, from_host="host.docker.internal", to_host="127.0.0.1"
    )
    assert rebased == native_host


@pytest.mark.unit
def test_rebase_moves_base_url_and_drops_container_note() -> None:
    container = render_prompt_text(
        _rebase_mission("http://host.docker.internal:3001/api/runs/run-1")
    )
    rebased = rebase_run_control_host(
        container, from_host="host.docker.internal", to_host="127.0.0.1"
    )
    assert "http://127.0.0.1:3001/api/runs/run-1" in rebased
    assert "host.docker.internal" not in rebased  # base URL host + the --add-host note both gone
    assert "--add-host" not in rebased


@pytest.mark.unit
def test_rebase_noop_when_hosts_equal() -> None:
    container = render_prompt_text(
        _rebase_mission("http://host.docker.internal:3001/api/runs/run-1")
    )
    assert (
        rebase_run_control_host(
            container, from_host="host.docker.internal", to_host="host.docker.internal"
        )
        == container
    )


@pytest.mark.unit
def test_rebase_noop_when_from_host_absent() -> None:
    # A prompt already baked for the loopback host (a host-only harness) is untouched when asked to
    # rebase away a host it doesn't contain.
    host_prompt = render_prompt_text(_rebase_mission("http://127.0.0.1:3001/api/runs/run-1"))
    assert (
        rebase_run_control_host(host_prompt, from_host="host.docker.internal", to_host="10.0.0.5")
        == host_prompt
    )
