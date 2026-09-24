import requests
import pytest

from src.network import CircuitOpenError, ResilientJSONSession


class FailedResponse:
    status_code = 503

    def raise_for_status(self):
        response = requests.Response()
        response.status_code = 503
        raise requests.HTTPError(response=response)


class FailedSession:
    def __init__(self):
        self.headers = {}
        self.calls = 0

    def get(self, *args, **kwargs):
        self.calls += 1
        return FailedResponse()


def test_json_transport_retries_then_opens_circuit():
    session = FailedSession()
    client = ResilientJSONSession(
        timeout_seconds=1, max_attempts=2, failure_threshold=1,
        recovery_seconds=60, backoff_seconds=0, session=session,  # type: ignore[arg-type]
    )
    with pytest.raises(RuntimeError, match="after 2 attempts"):
        client.get("https://example.invalid")
    assert session.calls == 2
    with pytest.raises(CircuitOpenError):
        client.get("https://example.invalid")
    assert session.calls == 2
