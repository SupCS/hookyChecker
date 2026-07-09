from starlette.requests import Request

from hooky_checker.api.app import request_public_url


def test_public_url_uses_railway_forwarded_headers() -> None:
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "http",
            "path": "/",
            "query_string": b"",
            "headers": [
                (b"host", b"internal:8000"),
                (b"x-forwarded-proto", b"https"),
                (b"x-forwarded-host", b"hookychecker-production.up.railway.app"),
            ],
            "server": ("internal", 8000),
        }
    )
    assert request_public_url(request) == "https://hookychecker-production.up.railway.app"
