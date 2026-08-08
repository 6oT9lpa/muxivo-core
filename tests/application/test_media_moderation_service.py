from datetime import datetime, timezone
from io import BytesIO

import pytest
from PIL import Image

from src.application.media_moderation_service import MediaModerationService
from src.contracts.api.media_moderation_request_schema import MediaModerationRequestSchema
from src.contracts.api.moderation_message_response_schema import ModerationMessageResponseSchema
from src.domain.media.downloaded_media import DownloadedMedia
from src.domain.media.image_detection_result import ImageDetectionResult
from src.domain.media.media_runtime_config import MediaRuntimeConfig
from src.domain.media.ocr_result import OcrResult
from src.infrastructure.media.pillow_media_hasher import PillowMediaHasher
from src.infrastructure.media.pillow_media_validator import PillowMediaValidator


def _png() -> bytes:
    output = BytesIO()
    Image.new("RGB", (8, 8), (10, 20, 30)).save(output, format="PNG")
    return output.getvalue()


class _Downloader:
    async def download(self, attachment):
        return DownloadedMedia(
            attachment_id=attachment.attachment_id,
            content=_png(),
            declared_content_type=attachment.content_type,
            sanitized_host="cdn.discordapp.com",
            download_latency_ms=1,
        )

    async def close(self) -> None:
        return None


class _Ocr:
    enabled = True
    ready = True

    def __init__(self) -> None:
        self.calls = 0

    async def analyze(self, input_image):
        self.calls += 1
        return OcrResult(
            attachment_id=input_image.attachment_id,
            text="casino fake win",
            redacted_text="casino fake win",
            language="en",
            confidence=0.9,
            model_name="ocr",
            model_version="1",
            processing_time_ms=2,
        )

    async def close(self) -> None:
        return None


class _Image:
    enabled = False
    ready = False

    async def analyze(self, input_image):
        return ImageDetectionResult(
            attachment_id=input_image.attachment_id,
            model_name="disabled",
            model_version="disabled",
            processing_time_ms=0,
            warnings=("image_provider_disabled",),
        )

    async def close(self) -> None:
        return None


class _Moderation:
    def __init__(self) -> None:
        self.calls = 0

    async def moderate_media(self, request, bundle, correlation_id):
        self.calls += 1
        labels = {analysis.attachment.attachment_id: ("SCAM",) for analysis in bundle.attachments}
        return (
            ModerationMessageResponseSchema(
                correlation_id=correlation_id,
                message_id=request.message_id,
                labels=("SCAM",),
                primary_label="SCAM",
                rule_matches=(),
                rubert_labels=(),
                risk_score=70,
                risk_breakdown=("SCAM",),
                decision_action="DELETE_WARN",
                severity=4,
                reason="media",
                policy_id="policy",
                policy_version="1",
                execution_status="PENDING",
                execution_plan=("DELETE", "WARN"),
                dataset_event_id=10,
                latency_ms=5,
            ),
            labels,
        )


class _Repository:
    def __init__(self) -> None:
        self.records = []

    async def save(self, record) -> None:
        self.records.append(record)


def _request() -> MediaModerationRequestSchema:
    timestamp = datetime.now(timezone.utc).isoformat()
    attachment = {
        "download_url": "https://cdn.discordapp.com/a.png",
        "content_type": "image/png",
        "file_size": len(_png()),
    }
    return MediaModerationRequestSchema.model_validate(
        {
            "message": {
                "guild_id": "1",
                "channel_id": "2",
                "user_id": "3",
                "message_id": "4",
                "raw_text": "text",
                "created_at": timestamp,
                "has_attachments": True,
                "attachment_count": 2,
            },
            "attachments": (
                {**attachment, "attachment_id": "a"},
                {**attachment, "attachment_id": "b"},
            ),
        }
    )


@pytest.mark.asyncio
async def test_media_service_persists_fallback_for_missing_declared_mime() -> None:
    moderation = _Moderation()
    attachments = _Repository()
    service = MediaModerationService(
        moderation_service=moderation,
        downloader=_Downloader(),
        validator=PillowMediaValidator(
            allowed_content_types=("image/png",), max_width=100, max_height=100, max_pixels=10_000
        ),
        hasher=PillowMediaHasher(), ocr_provider=_Ocr(), image_provider=_Image(),
        attachment_repository=attachments, analysis_repository=_Repository(),
        runtime_config=MediaRuntimeConfig(enabled=True, required=False, max_attachments=4, max_file_size_bytes=1_000_000, max_total_size_bytes=2_000_000, max_width=100, max_height=100, max_pixels=10_000, retention_hours=24, hash_cache_ttl_hours=24, input_version="v1", ocr_required=False, image_required=False),
    )
    request = _request().model_copy(update={"attachments": (_request().attachments[0].model_copy(update={"content_type": None}), _request().attachments[1])})
    await service.moderate(request, "correlation")
    assert attachments.records[0].declared_mime == "application/octet-stream"


@pytest.mark.asyncio
async def test_media_service_produces_one_decision_and_deduplicates_exact_image() -> None:
    moderation = _Moderation()
    attachments = _Repository()
    results = _Repository()
    ocr = _Ocr()
    service = MediaModerationService(
        moderation_service=moderation,
        downloader=_Downloader(),
        validator=PillowMediaValidator(
            allowed_content_types=("image/png",),
            max_width=100,
            max_height=100,
            max_pixels=10_000,
        ),
        hasher=PillowMediaHasher(),
        ocr_provider=ocr,
        image_provider=_Image(),
        attachment_repository=attachments,
        analysis_repository=results,
        runtime_config=MediaRuntimeConfig(
            enabled=True,
            required=False,
            max_attachments=4,
            max_file_size_bytes=1_000_000,
            max_total_size_bytes=2_000_000,
            max_width=100,
            max_height=100,
            max_pixels=10_000,
            retention_hours=24,
            hash_cache_ttl_hours=24,
            input_version="v1",
            ocr_required=False,
            image_required=False,
        ),
    )

    response = await service.moderate(_request(), "correlation")

    assert moderation.calls == 1
    assert ocr.calls == 1
    assert response.decision_action == "DELETE_WARN"
    assert response.attachments[0].status.value == "analyzed"
    assert response.attachments[1].status.value == "duplicate"
    assert len(attachments.records) == 2
    assert all(record.redacted_ocr_text == "casino fake win" for record in attachments.records)
