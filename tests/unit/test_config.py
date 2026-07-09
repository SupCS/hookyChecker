from hooky_checker.config import Settings


def test_railway_domain_becomes_public_https_url() -> None:
    settings = Settings(
        public_api_url="http://localhost:8000",
        railway_public_domain="hooky-checker.up.railway.app",
    )
    assert settings.effective_public_api_url == "https://hooky-checker.up.railway.app"
