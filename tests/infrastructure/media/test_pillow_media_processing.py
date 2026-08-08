from io import BytesIO

import pytest
from PIL import Image

from src.application.media_error import MediaValidationError
from src.domain.media.downloaded_media import DownloadedMedia
from src.infrastructure.media.pillow_media_hasher import PillowMediaHasher
from src.infrastructure.media.pillow_media_validator import PillowMediaValidator


def _image_bytes(size: tuple[int, int] = (16, 16), image_format: str = "PNG") -> bytes:
    output = BytesIO()
    Image.new("RGB", size, (20, 40, 60)).save(output, format=image_format)
    return output.getvalue()


def _downloaded(content: bytes, content_type: str = "image/png") -> DownloadedMedia:
    return DownloadedMedia(
        attachment_id="1",
        content=content,
        declared_content_type=content_type,
        sanitized_host="cdn.discordapp.com",
        download_latency_ms=1,
    )


def _validator(max_pixels: int = 1_000_000) -> PillowMediaValidator:
    return PillowMediaValidator(
        allowed_content_types=("image/gif", "image/png", "image/jpeg", "image/webp"),
        max_width=1_000,
        max_height=1_000,
        max_pixels=max_pixels,
    )


@pytest.mark.asyncio
async def test_validator_detects_mime_and_hasher_is_deterministic() -> None:
    downloaded = _downloaded(_image_bytes())
    validated = await _validator().validate(downloaded)
    first = await PillowMediaHasher().calculate(downloaded, validated)
    second = await PillowMediaHasher().calculate(downloaded, validated)
    assert validated.detected_mime == "image/png"
    assert first == second
    assert len(first.sha256) == 64
    assert len(first.phash) == len(first.dhash) == len(first.ahash) == 16


@pytest.mark.asyncio
async def test_validator_uses_decoded_bytes_when_declared_mime_is_wrong() -> None:
    validated = await _validator().validate(_downloaded(_image_bytes(), "image/jpeg"))
    assert validated.detected_mime == "image/png"
    with pytest.raises(MediaValidationError):
        await _validator().validate(_downloaded(b"not-an-image"))


@pytest.mark.asyncio
async def test_validator_rejects_oversized_decoded_pixels() -> None:
    with pytest.raises(MediaValidationError):
        await _validator(max_pixels=100).validate(_downloaded(_image_bytes((11, 10))))


@pytest.mark.asyncio
async def test_validator_extracts_first_gif_frame_for_analysis() -> None:
    output = BytesIO()
    frames = [Image.new("RGB", (8, 8), color) for color in ((0, 0, 0), (255, 255, 255))]
    frames[0].save(output, format="GIF", save_all=True, append_images=frames[1:], duration=10)
    validated = await _validator().validate(_downloaded(output.getvalue(), "image/gif"))
    assert validated.detected_mime == "image/gif"
    assert validated.analysis_bytes
