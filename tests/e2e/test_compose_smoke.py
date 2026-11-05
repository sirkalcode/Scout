"""Black-box checks that the Compose stack starts, connects and exposes only the gateway."""

import json
import os
import subprocess
from collections.abc import Iterator
from pathlib import Path

import httpx2
import pytest

ROOT = Path(__file__).resolve().parents[2]
GATEWAY_URL = os.environ.get("SCOUT_GATEWAY_URL", "http://127.0.0.1:8000")
COMPOSE_FILES = os.environ.get("SCOUT_COMPOSE_FILES", "compose.yaml").split(":")


def compose(*args: str) -> subprocess.CompletedProcess[str]:
    files = [flag for name in COMPOSE_FILES for flag in ("-f", name)]
    return subprocess.run(
        ["docker", "compose", *files, *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture(scope="module")
def stack() -> Iterator[None]:
    started = compose("up", "--build", "--detach", "--wait", "--wait-timeout", "180")
    try:
        assert started.returncode == 0, started.stderr + compose("logs").stdout
        yield
    finally:
        compose("down", "--volumes", "--remove-orphans")


def get(path: str) -> httpx2.Response:
    return httpx2.get(f"{GATEWAY_URL}{path}", timeout=5, trust_env=False)


@pytest.mark.usefixtures("stack")
def test_gateway_is_alive() -> None:
    response = get("/healthz")

    assert response.status_code == 200
    assert response.json()["service"] == "gateway"


@pytest.mark.usefixtures("stack")
def test_gateway_reaches_the_orchestrator_over_the_compose_network() -> None:
    response = get("/readyz")

    assert response.status_code == 200
    assert response.json()["dependencies"] == {"orchestrator": "ok"}


@pytest.mark.usefixtures("stack")
def test_every_service_reports_healthy() -> None:
    listed = compose("ps", "--format", "json").stdout.splitlines()
    services = {item["Service"]: item["Health"] for item in map(json.loads, listed)}

    assert services == {"gateway": "healthy", "orchestrator": "healthy"}


@pytest.mark.usefixtures("stack")
def test_only_the_gateway_is_published_to_the_host() -> None:
    assert compose("port", "gateway", "8000").returncode == 0
    assert compose("port", "orchestrator", "8000").stdout.strip() in {"", ":0"}
