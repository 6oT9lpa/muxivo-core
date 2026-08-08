import pytest
from pydantic import ValidationError

from src.infrastructure.api.api_settings import ApiSettings


@pytest.mark.parametrize(
    "proxy_url",
    ("http://proxy.example:8080", "https://proxy.example:8443", "socks5://127.0.0.1:1080"),
)
def test_media_proxy_url_accepts_supported_proxy_endpoints(proxy_url: str) -> None:
    settings = ApiSettings(
        internal_api_key="test-key-value-1234",
        media_proxy_url=proxy_url,
    )

    assert settings.media_proxy_url == proxy_url


@pytest.mark.parametrize(
    "proxy_url",
    ("socks4://proxy.example:1080", "https://proxy.example/path", "https://proxy.example?target=x"),
)
def test_media_proxy_url_rejects_non_proxy_urls(proxy_url: str) -> None:
    with pytest.raises(ValidationError, match="media_proxy_url"):
        ApiSettings(internal_api_key="test-key-value-1234", media_proxy_url=proxy_url)
