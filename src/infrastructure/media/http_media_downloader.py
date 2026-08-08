from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from time import perf_counter
from urllib.parse import urljoin, urlsplit

import httpx

from src.application.media_error import (
    MediaDownloadTimeoutError,
    MediaDownloadUnavailableError,
    MediaSecurityError,
    MediaValidationError,
)
from src.application.ports.media.media_downloader import MediaDownloader
from src.domain.media.downloaded_media import DownloadedMedia
from src.domain.media.media_attachment import MediaAttachment
from src.infrastructure.logging import get_logger

logger = get_logger(__name__)
AddressResolver = Callable[[str, int], Awaitable[set[str]]]


class HttpMediaDownloader(MediaDownloader):
    _REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})

    def __init__(
        self,
        *,
        allowed_hosts: tuple[str, ...],
        max_file_size_bytes: int,
        timeout_seconds: float,
        max_redirects: int,
        proxy_url: str | None = None,
        client: httpx.AsyncClient | None = None,
        resolver: AddressResolver | None = None,
    ) -> None:
        self._allowed_hosts = frozenset(host.casefold().rstrip(".") for host in allowed_hosts)
        self._max_file_size_bytes = max_file_size_bytes
        self._timeout_seconds = timeout_seconds
        self._max_redirects = max_redirects
        self._resolver = resolver or self._resolve_addresses
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 5.0)),
            follow_redirects=False,
            trust_env=False,
            proxy=proxy_url,
        )

    async def download(self, attachment: MediaAttachment) -> DownloadedMedia:
        if attachment.download_url is None:
            raise MediaDownloadUnavailableError("media_reference is not configured")
        started_at = perf_counter()
        current_url = attachment.download_url
        try:
            async with asyncio.timeout(self._timeout_seconds):
                content, host = await self._download_with_redirects(current_url)
        except TimeoutError as exc:
            raise MediaDownloadTimeoutError("media download timed out") from exc
        except httpx.TimeoutException as exc:
            raise MediaDownloadTimeoutError("media download timed out") from exc
        except httpx.HTTPError as exc:
            raise MediaDownloadUnavailableError("media download failed") from exc

        logger.info(
            "Media download completed attachment_id=%s host=%s size=%s latency_ms=%s",
            attachment.attachment_id,
            host,
            len(content),
            round((perf_counter() - started_at) * 1_000),
        )
        return DownloadedMedia(
            attachment_id=attachment.attachment_id,
            content=content,
            declared_content_type=attachment.content_type,
            sanitized_host=host,
            download_latency_ms=round((perf_counter() - started_at) * 1_000),
        )

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _download_with_redirects(self, initial_url: str) -> tuple[bytes, str]:
        current_url = initial_url
        for redirect_count in range(self._max_redirects + 1):
            host = await self._validate_url(current_url)
            async with self._client.stream("GET", current_url) as response:
                if response.status_code in self._REDIRECT_STATUSES:
                    if redirect_count >= self._max_redirects:
                        raise MediaSecurityError("media redirect limit exceeded")
                    location = response.headers.get("location")
                    if not location:
                        raise MediaDownloadUnavailableError("media redirect has no location")
                    current_url = urljoin(current_url, location)
                    continue
                if response.status_code in {403, 404, 410}:
                    raise MediaDownloadUnavailableError("media URL is unavailable or expired")
                if response.status_code >= 400:
                    raise MediaDownloadUnavailableError(f"media host returned HTTP {response.status_code}")

                declared_length = response.headers.get("content-length")
                if declared_length and declared_length.isdecimal() and int(declared_length) > self._max_file_size_bytes:
                    raise MediaValidationError("media body exceeds size limit")
                content = await self._read_bounded(response)
                return content, host
        raise MediaSecurityError("media redirect limit exceeded")

    async def _read_bounded(self, response: httpx.Response) -> bytes:
        chunks: list[bytes] = []
        total_size = 0
        async for chunk in response.aiter_bytes():
            total_size += len(chunk)
            if total_size > self._max_file_size_bytes:
                raise MediaValidationError("media stream exceeds size limit")
            chunks.append(chunk)
        if not chunks:
            raise MediaValidationError("media body is empty")
        return b"".join(chunks)

    async def _validate_url(self, url: str) -> str:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").casefold().rstrip(".")
        if parsed.scheme.casefold() != "https" or not host or parsed.username or parsed.password:
            raise MediaSecurityError("media URL must be an HTTPS URL without userinfo")
        if host not in self._allowed_hosts:
            raise MediaSecurityError("media URL host is not allowed")
        addresses = await self._resolver(host, parsed.port or 443)
        if not addresses:
            raise MediaSecurityError("media URL host did not resolve")
        for address in addresses:
            parsed_address = ipaddress.ip_address(address)
            if not parsed_address.is_global:
                raise MediaSecurityError("media URL resolved to a non-public address")
        return host

    @staticmethod
    async def _resolve_addresses(host: str, port: int) -> set[str]:
        records = await asyncio.to_thread(socket.getaddrinfo, host, port, type=socket.SOCK_STREAM)
        return {str(record[4][0]) for record in records}
