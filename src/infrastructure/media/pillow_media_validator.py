import asyncio
import warnings
from io import BytesIO
from time import perf_counter

from PIL import Image, UnidentifiedImageError

from src.application.media_error import UnsupportedMediaError, MediaValidationError
from src.application.ports.media.media_validator import MediaValidator
from src.domain.media.downloaded_media import DownloadedMedia
from src.domain.media.validated_media import ValidatedMedia
from src.infrastructure.logging import get_logger
from src.infrastructure.media.pillow_gif_frame_extractor import PillowGifFrameExtractor

logger = get_logger(__name__)


class PillowMediaValidator(MediaValidator):
    _MIME_BY_FORMAT = {"GIF": "image/gif", "JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}

    def __init__(
        self,
        *,
        allowed_content_types: tuple[str, ...],
        max_width: int,
        max_height: int,
        max_pixels: int,
        gif_frame_extractor: PillowGifFrameExtractor | None = None,
    ) -> None:
        self._allowed_content_types = frozenset(allowed_content_types)
        self._max_width = max_width
        self._max_height = max_height
        self._max_pixels = max_pixels
        self._gif_frame_extractor = gif_frame_extractor or PillowGifFrameExtractor()

    async def validate(self, downloaded: DownloadedMedia) -> ValidatedMedia:
        return await asyncio.to_thread(self._validate_sync, downloaded)

    def _validate_sync(self, downloaded: DownloadedMedia) -> ValidatedMedia:
        started_at = perf_counter()
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(BytesIO(downloaded.content)) as image:
                    detected_mime = self._MIME_BY_FORMAT.get(image.format or "")
                    if detected_mime is None or detected_mime not in self._allowed_content_types:
                        raise UnsupportedMediaError("decoded image format is not supported")
                    analysis_image = (
                        self._gif_frame_extractor.extract(image)
                        if detected_mime == "image/gif"
                        else image
                    )
                    width, height = analysis_image.size
                    if width > self._max_width or height > self._max_height or width * height > self._max_pixels:
                        raise MediaValidationError("decoded image dimensions exceed limits")
                    analysis_image.load()
                    normalized_bytes = self._strip_metadata(analysis_image, detected_mime)
                    fingerprint_luma = analysis_image.convert("L").resize((32, 32), Image.Resampling.LANCZOS).tobytes()
        except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            raise MediaValidationError("image decompression bomb rejected") from exc
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            raise MediaValidationError("image is malformed") from exc

        latency_ms = round((perf_counter() - started_at) * 1_000)
        logger.info(
            "Media validation completed attachment_id=%s declared_mime=%s detected_mime=%s width=%s height=%s latency_ms=%s",
            downloaded.attachment_id,
            downloaded.declared_content_type or "absent",
            detected_mime,
            width,
            height,
            latency_ms,
        )
        return ValidatedMedia(
            attachment_id=downloaded.attachment_id,
            analysis_bytes=normalized_bytes,
            detected_mime=detected_mime,
            file_size=len(downloaded.content),
            width=width,
            height=height,
            fingerprint_luma=fingerprint_luma,
            validation_latency_ms=latency_ms,
        )

    @staticmethod
    def _strip_metadata(image: Image.Image, detected_mime: str) -> bytes:
        output = BytesIO()
        if detected_mime == "image/jpeg":
            image.convert("RGB").save(output, format="JPEG", quality=95, optimize=True)
        elif detected_mime in {"image/png", "image/gif"}:
            image.convert("RGBA" if "A" in image.getbands() else "RGB").save(output, format="PNG", optimize=True)
        else:
            image.convert("RGBA" if "A" in image.getbands() else "RGB").save(output, format="WEBP", lossless=True)
        return output.getvalue()
