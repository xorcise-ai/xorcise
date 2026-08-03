from xorcise.core.home import clear_transient, purge_home


def test_clear_transient_removes_logs_keeps_durable(tmp_path):
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "x.log").write_text("x")
    (tmp_path / "config.toml").write_text("k=1")
    (tmp_path / "xorcise.db").write_text("db")
    clear_transient(tmp_path)
    assert not (tmp_path / "logs").exists()
    assert (tmp_path / "config.toml").exists()
    assert (tmp_path / "xorcise.db").exists()


def test_purge_home_removes_everything(tmp_path):
    home = tmp_path / ".xorcise"
    home.mkdir()
    (home / "config.toml").write_text("k=1")
    purge_home(home)
    assert not home.exists()


def test_purge_home_missing_is_noop(tmp_path):
    purge_home(tmp_path / "nope")  # must not raise


import stat  # noqa: E402

from xorcise.core.home import scaffold_config  # noqa: E402


def test_scaffold_config_writes_config_and_env_0600(tmp_path):
    scaffold_config(tmp_path)
    assert (tmp_path / "config.toml").exists()
    env = tmp_path / ".env"
    assert env.exists()
    assert stat.S_IMODE(env.stat().st_mode) == 0o600


def test_scaffold_config_does_not_overwrite(tmp_path):
    (tmp_path / "config.toml").write_text('role = "mine"\n')
    (tmp_path / ".env").write_text("XORCISE_MODEL_KEY=keep\n")
    scaffold_config(tmp_path)
    assert (tmp_path / "config.toml").read_text() == 'role = "mine"\n'
    assert "keep" in (tmp_path / ".env").read_text()


from xorcise.core.home import (  # noqa: E402
    read_runtime_ports,
    runtime_ports_file,
    write_runtime_ports,
)


def test_runtime_ports_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))
    assert read_runtime_ports() is None  # absent → fall back to config
    write_runtime_ports({"rest": 3002, "otlp": 4319})
    assert runtime_ports_file() == tmp_path / "runtime-ports.json"
    assert read_runtime_ports() == {"rest": 3002, "otlp": 4319}


def test_read_runtime_ports_unreadable_returns_none(monkeypatch, tmp_path):
    monkeypatch.setenv("XORCISE_HOME", str(tmp_path))
    (tmp_path / "runtime-ports.json").write_text("not json")
    assert read_runtime_ports() is None
    (tmp_path / "runtime-ports.json").write_text('{"rest": "3002"}')  # wrong value type
    assert read_runtime_ports() is None


def test_clear_transient_removes_runtime_ports_record(tmp_path):
    (tmp_path / "runtime-ports.json").write_text('{"rest": 3002}')
    (tmp_path / "config.toml").write_text("k=1")
    clear_transient(tmp_path)
    assert not (tmp_path / "runtime-ports.json").exists()
    assert (tmp_path / "config.toml").exists()
