"""Black-box checks that the Compose stack starts, connects and exposes only the gateway."""

import json
import os
import subprocess
from collections.abc import Iterator
from pathlib import Path

import httpx2
import pytest

ROOT = Path(__file__).resolve().parents[2]
GATEWAY_URL = os.environ.get("SCOUT_GATEWAY_URL", "http://127.0.0.1:18000")
# compose.test.yaml forces fake mode, so the smoke test can never spend money.
COMPOSE_FILES = os.environ.get("SCOUT_COMPOSE_FILES", "compose.yaml:compose.test.yaml").split(":")


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

    assert services == {
        "gateway": "healthy",
        "llm-gateway": "healthy",
        "orchestrator": "healthy",
        "postgres": "healthy",
    }


@pytest.mark.usefixtures("stack")
def test_only_the_gateway_is_published_to_the_host() -> None:
    # Structured data, because the text from `docker compose port` varies by Docker version.
    listed = compose("ps", "--format", "json").stdout.splitlines()
    published = {
        item["Service"]: {
            (port["URL"], port["PublishedPort"])
            for port in item["Publishers"]
            if port["PublishedPort"]
        }
        for item in map(json.loads, listed)
    }

    assert published == {
        "gateway": {("127.0.0.1", 18000)},
        "llm-gateway": set(),
        "orchestrator": set(),
        "postgres": set(),
    }


@pytest.mark.usefixtures("stack")
def test_chat_anthropic_call_returns_text_and_cost_through_the_gateway() -> None:
    result = compose(
        "exec",
        "-T",
        "orchestrator",
        "/app/services/orchestrator/.venv/bin/python",
        "-m",
        "orchestrator.live_check",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["mode"] == "fake"
    assert report["invoke"]["text"]
    assert report["stream"]["text"]
    assert report["ledger"]["requests_added"] == 2
    assert float(report["ledger"]["cost_added_usd"]) > 0


@pytest.mark.usefixtures("stack")
def test_spend_survives_a_gateway_restart() -> None:
    read = (
        "import os, urllib.request as u; "
        "r = u.Request('http://127.0.0.1:8000/v1/spend/today', headers="
        "{'authorization': 'Bearer ' + os.environ['LLM_GATEWAY_TOKEN']}); "
        "print(u.urlopen(r, timeout=3).read().decode())"
    )
    spend = ("exec", "-T", "llm-gateway", "python", "-c", read)
    before = json.loads(compose(*spend).stdout)
    assert before["requests"] > 0, "needs the previous test's calls"

    compose("restart", "llm-gateway")
    compose("up", "--wait", "--wait-timeout", "60", "llm-gateway")

    assert json.loads(compose(*spend).stdout)["requests"] == before["requests"]


@pytest.mark.usefixtures("stack")
def test_hand_written_agent_runs_through_the_gateway() -> None:
    result = compose(
        "exec",
        "-T",
        "orchestrator",
        "/app/services/orchestrator/.venv/bin/python",
        "-m",
        "orchestrator.hand_loop.demo",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "stop_reason: end_turn" in result.stdout
    assert "mode: fake" in result.stdout
