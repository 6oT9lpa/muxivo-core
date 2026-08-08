import pytest
from pydantic import ValidationError

from src.infrastructure.api.api_settings import ApiSettings


def test_media_proxy_url_accepts_http_and_https_proxy_endpoints() -> None:
    settings = ApiSettings(
        internal_api_key="test-key-value-1234",
        media_proxy_url="https://proxy.example:8443",
    )

    assert settings.media_proxy_url == "https://proxy.example:8443"


@pytest.mark.parametrize(
    "proxy_url",
    ("socks5://proxy.example:1080", "https://proxy.example/path", "https://proxy.example?target=x"),
)
def test_media_proxy_url_rejects_non_proxy_urls(proxy_url: str) -> None:
    with pytest.raises(ValidationError, match="media_proxy_url"):
        ApiSettings(internal_api_key="test-key-value-1234", media_proxy_url=proxy_url)
