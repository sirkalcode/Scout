"""429, 529 and connection failures retry with backoff. Mistakes in the request do not."""

from collections.abc import Callable

import pytest
from scout_testkit import fake_claude as claude

from ..conftest import Harness


@pytest.mark.parametrize(
    "failure",
    [
        claude.rate_limited(),
        claude.overloaded(),
        claude.api_error(500, "api_error", "boom"),
        claude.connection_error(),
    ],
    ids=["429", "529", "500", "connection"],
)
def test_transient_failures_are_retried(
    harness: Callable[..., Harness], failure: claude.Step
) -> None:
    h = harness(failure, claude.end_turn("recovered"))

    response = h.post()

    assert response.status_code == 200
    assert response.json()["content"][0]["text"] == "recovered"
    assert len(h.fake.requests) == 2
    assert len(h.sleeps) == 1


def test_backoff_grows(harness: Callable[..., Harness]) -> None:
    h = harness(claude.overloaded(), claude.overloaded(), claude.end_turn("ok"))

    assert h.post().status_code == 200
    assert h.sleeps[1] > h.sleeps[0] > 0


def test_retry_after_is_honored(harness: Callable[..., Harness]) -> None:
    h = harness(claude.rate_limited(retry_after=7), claude.end_turn("ok"))

    h.post()

    assert h.sleeps == [7.0]


def test_long_retry_after_goes_back_to_the_caller(harness: Callable[..., Harness]) -> None:
    h = harness(claude.rate_limited(retry_after=600), claude.end_turn("not reached"))

    response = h.post()

    assert response.status_code == 429
    assert response.headers["retry-after"] == "600"
    assert len(h.fake.requests) == 1
    assert h.sleeps == []


def test_gives_up_after_three_attempts_and_returns_the_last_error(
    harness: Callable[..., Harness],
) -> None:
    h = harness(claude.overloaded(), claude.overloaded(), claude.overloaded())

    response = h.post()

    assert response.status_code == 529
    assert response.json()["error"]["type"] == "overloaded_error"
    assert len(h.fake.requests) == 3
    assert h.ledger.records == []


def test_repeated_connection_failure_returns_a_clean_502(
    harness: Callable[..., Harness],
) -> None:
    h = harness(claude.connection_error(), claude.connection_error(), claude.connection_error())

    response = h.post()

    assert response.status_code == 502
    assert response.json()["error"]["type"] == "api_error"


@pytest.mark.parametrize("status", [400, 401, 403, 404, 413])
def test_client_errors_are_not_retried(harness: Callable[..., Harness], status: int) -> None:
    h = harness(claude.api_error(status, "invalid_request_error", "no"))

    assert h.post().status_code == status
    assert len(h.fake.requests) == 1


def test_reservation_is_released_after_giving_up(harness: Callable[..., Harness]) -> None:
    # The limit only just covers one request's worst case, so a leaked reservation blocks the next.
    h = harness(
        claude.overloaded(),
        claude.overloaded(),
        claude.overloaded(),
        claude.end_turn("ok"),
        limit="0.002",
    )

    assert h.post().status_code == 529
    assert h.post().status_code == 200
