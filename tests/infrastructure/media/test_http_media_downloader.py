import httpx
import pytest

from src.application.media_error import MediaDownloadTimeoutError, MediaSecurityError, MediaValidationError
from src.domain.media.media_attachment import MediaAttachment
from src.infrastructure.media.http_media_downloader import HttpMediaDownloader


def _attachment() -> MediaAttachment:
    return MediaAttachment(
        attachment_id="1",
        download_url="https://cdn.discordapp.com/image.png",
        content_type="image/png",
        file_size=4,
    )


@pytest.mark.asyncio
async def test_downloader_rejects_private_dns_resolution() -> None:
    async def resolver(_host: str, _port: int) -> set[str]:
        return {"127.0.0.1"}

    downloader = HttpMediaDownloader(
        allowed_hosts=("cdn.discordapp.com",),
        max_file_size_bytes=10,
        timeout_seconds=1,
        max_redirects=1,
        client=httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"x"))),
        resolver=resolver,
    )
    with pytest.raises(MediaSecurityError):
        await downloader.download(_attachment())
    await downloader._client.aclose()


@pytest.mark.asyncio
async def test_downloader_rechecks_dns_after_redirect() -> None:
    resolutions = iter(({"1.1.1.1"}, {"169.254.169.254"}))

    async def resolver(_host: str, _port: int) -> set[str]:
        return next(resolutions)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "/redirected.png"}, request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    downloader = HttpMediaDownloader(
        allowed_hosts=("cdn.discordapp.com",),
        max_file_size_bytes=10,
        timeout_seconds=1,
        max_redirects=1,
        client=client,
        resolver=resolver,
    )
    with pytest.raises(MediaSecurityError):
        await downloader.download(_attachment())
    await client.aclose()


@pytest.mark.asyncio
async def test_downloader_enforces_stream_size_without_trusting_length() -> None:
    async def resolver(_host: str, _port: int) -> set[str]:
        return {"1.1.1.1"}

    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"12345", request=request)))
    downloader = HttpMediaDownloader(
        allowed_hosts=("cdn.discordapp.com",),
        max_file_size_bytes=4,
        timeout_seconds=1,
        max_redirects=0,
        client=client,
        resolver=resolver,
    )
    with pytest.raises(MediaValidationError):
        await downloader.download(_attachment())
    await client.aclose()


@pytest.mark.asyncio
async def test_downloader_maps_http_timeout_to_safe_error() -> None:
    async def resolver(_host: str, _port: int) -> set[str]:
        return {"1.1.1.1"}

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    downloader = HttpMediaDownloader(
        allowed_hosts=("cdn.discordapp.com",),
        max_file_size_bytes=4,
        timeout_seconds=1,
        max_redirects=0,
        client=client,
        resolver=resolver,
    )
    with pytest.raises(MediaDownloadTimeoutError):
        await downloader.download(_attachment())
    await client.aclose()


@pytest.mark.asyncio
async def test_downloader_accepts_a_dedicated_socks5_proxy() -> None:
    downloader = HttpMediaDownloader(
        allowed_hosts=("cdn.discordapp.com",),
        max_file_size_bytes=4,
        timeout_seconds=1,
        max_redirects=0,
        proxy_url="socks5://127.0.0.1:1080",
    )

    await downloader.close()
