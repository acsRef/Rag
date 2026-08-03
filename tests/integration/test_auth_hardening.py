"""限流按真实客户端 IP（X-Forwarded-For）计桶。"""


def test_rate_limit_key_uses_forwarded_for():
    from app.api.auth import _client_ip

    class FakeClient:
        host = "10.0.0.1"

    class FakeRequest:
        client = FakeClient()
        headers = {"x-forwarded-for": "203.0.113.7, 10.0.0.1"}

    assert _client_ip(FakeRequest()) == "203.0.113.7"

    class NoHeader:
        client = FakeClient()
        headers = {}

    assert _client_ip(NoHeader()) == "10.0.0.1"
