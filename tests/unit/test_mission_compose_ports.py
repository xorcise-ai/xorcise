import pytest

from xorcise.core.runner.docker.driver import _published_port_services


def test_finds_short_and_long_syntax_host_publications() -> None:
    compose = """
services:
  web:
    ports: ["5000:5000"]
  harness:
    ports:
      - target: 5000
        published: "5055"
  database:
    expose: [5432]
"""

    assert _published_port_services(compose) == ("web", "harness")


def test_rejects_compose_without_a_services_mapping() -> None:
    with pytest.raises(RuntimeError, match="services mapping"):
        _published_port_services("name: not-a-mission-stack\n")
