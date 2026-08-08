from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.application.api_moderation_service import ApiModerationService
from src.application.effective_media_policy_resolver import EffectiveMediaPolicyResolver
from src.application.api_resource_unavailable_error import ApiResourceUnavailableError
from src.application.media_error import (
    MediaError,
    MediaPersistenceError,
    MediaSecurityError,
    MediaValidationError,
)
from src.application.ports.media.image_detection_provider import ImageDetectionProvider
from src.application.ports.media.media_analysis_result_repository import (
    MediaAnalysisResultRepository,
)
from src.application.ports.media.media_attachment_repository import (
    MediaAttachmentRepository,
)
from src.application.ports.media.media_downloader import MediaDownloader
from src.application.ports.media.media_hasher import MediaHasher
from src.application.ports.media.known_scam_hash_matcher import KnownScamHashMatcher
from src.application.ports.media.media_validator import MediaValidator
from src.application.ports.media.ocr_provider import OcrProvider
from src.application.ports.media.ocr_policy_processor import OcrPolicyProcessor
from src.contracts.api.media_attachment_summary_schema import (
    MediaAttachmentSummarySchema,
)
from src.contracts.api.media_moderation_request_schema import (
    MediaModerationRequestSchema,
)
from src.contracts.api.media_moderation_response_schema import (
    MediaModerationResponseSchema,
)
from src.domain.media.image_detection_input import ImageDetectionInput
from src.domain.media.image_detection_result import ImageDetectionResult
from src.domain.media.media_analysis_bundle import MediaAnalysisBundle
from src.domain.media.media_analysis_record import MediaAnalysisRecord
from src.domain.media.media_analysis_stage import MediaAnalysisStage
from src.domain.media.media_attachment_analysis import MediaAttachmentAnalysis
from src.domain.media.media_attachment_record import MediaAttachmentRecord
from src.domain.media.media_attachment_status import MediaAttachmentStatus
from src.domain.media.media_runtime_config import MediaRuntimeConfig
from src.domain.media.media_rule_policy import MediaRulePolicy, YoloRuleSettings
from src.domain.media.ocr_input import OcrInput
from src.domain.media.media_attachment import MediaAttachment
from src.domain.media.validated_media import ValidatedMedia
from src.infrastructure.logging import get_logger

logger = get_logger(__name__)


class MediaModerationService:
    def __init__(
        self,
        *,
        moderation_service: ApiModerationService,
        downloader: MediaDownloader,
        validator: MediaValidator,
        hasher: MediaHasher,
        ocr_provider: OcrProvider,
        image_provider: ImageDetectionProvider,
        attachment_repository: MediaAttachmentRepository,
        analysis_repository: MediaAnalysisResultRepository,
        runtime_config: MediaRuntimeConfig,
        media_policy_resolver: EffectiveMediaPolicyResolver | None = None,
        ocr_policy_processor: OcrPolicyProcessor | None = None,
        known_scam_hash_matcher: KnownScamHashMatcher | None = None,
    ) -> None:
        self._moderation_service = moderation_service
        self._downloader = downloader
        self._validator = validator
        self._hasher = hasher
        self._ocr_provider = ocr_provider
        self._image_provider = image_provider
        self._attachment_repository = attachment_repository
        self._analysis_repository = analysis_repository
        self._runtime_config = runtime_config
        self._media_policy_resolver = media_policy_resolver
        self._ocr_policy_processor = ocr_policy_processor
        self._known_scam_hash_matcher = known_scam_hash_matcher

    async def moderate(
        self,
        request: MediaModerationRequestSchema,
        correlation_id: str,
    ) -> MediaModerationResponseSchema:
        self._validate_request_limits(request)
        effective_policy = None
        if self._media_policy_resolver is not None:
            try:
                effective_policy = await self._media_policy_resolver.resolve(
                    request.message.platform, request.message.guild_id
                )
            except Exception as exc:
                raise ApiResourceUnavailableError(
                    "Media policy is unavailable"
                ) from exc
        if not self._runtime_config.enabled:
            if self._runtime_config.required:
                raise ApiResourceUnavailableError("Media moderation is unavailable")
            text_response = await self._moderation_service.moderate(
                request.message, correlation_id
            )
            response_payload = text_response.model_dump()
            response_payload.update(
                warnings=tuple(
                    dict.fromkeys((*text_response.warnings, "media_disabled"))
                )[:8],
                attachments=tuple(
                    MediaAttachmentSummarySchema(
                        attachment_id=attachment.attachment_id,
                        status=MediaAttachmentStatus.UNAVAILABLE,
                        warnings=("media_disabled",),
                    )
                    for attachment in request.attachments
                ),
            )
            return MediaModerationResponseSchema.model_validate(response_payload)

        analyses: list[MediaAttachmentAnalysis] = []
        exact_duplicates: dict[str, MediaAttachmentAnalysis] = {}
        actual_total_size = 0
        for attachment_schema in request.attachments:
            analysis, validated = await self._ingest_attachment(
                attachment_schema.to_domain()
            )
            actual_total_size += analysis.actual_file_size or 0
            if actual_total_size > self._runtime_config.max_total_size_bytes:
                analysis = self._failed_analysis(
                    analysis.attachment,
                    MediaAttachmentStatus.REJECTED,
                    MediaValidationError("downloaded media exceeds request size limit"),
                )
                validated = None
            if (
                analysis.hashes is not None
                and analysis.hashes.sha256 in exact_duplicates
            ):
                original = exact_duplicates[analysis.hashes.sha256]
                analysis = analysis.model_copy(
                    update={
                        "status": MediaAttachmentStatus.DUPLICATE,
                        "ocr_result": original.ocr_result,
                        "image_result": original.image_result,
                        "warnings": tuple(
                            dict.fromkeys((*analysis.warnings, "exact_duplicate"))
                        ),
                    }
                )
                logger.info(
                    "Media exact duplicate attachment_id=%s hash_prefix=%s",
                    attachment_schema.attachment_id,
                    analysis.hashes.sha256[:12],
                )
            elif analysis.hashes is not None:
                if validated is not None:
                    analysis = await self._run_providers(
                        analysis,
                        validated,
                        (
                            effective_policy.media
                            if effective_policy is not None
                            else None
                        ),
                    )
                exact_duplicates[analysis.hashes.sha256] = analysis
            analyses.append(analysis)

        if self._runtime_config.required and any(
            analysis.status
            in {MediaAttachmentStatus.REJECTED, MediaAttachmentStatus.UNAVAILABLE}
            for analysis in analyses
        ):
            raise ApiResourceUnavailableError(
                "Required media analysis did not complete"
            )

        bundle = MediaAnalysisBundle(attachments=tuple(analyses))
        if effective_policy is None:
            text_response, labels_by_attachment = (
                await self._moderation_service.moderate_media(
                    request.message, bundle, correlation_id
                )
            )
        else:
            text_response, labels_by_attachment = (
                await self._moderation_service.moderate_media(
                    request.message,
                    bundle,
                    correlation_id,
                    media_policy=effective_policy.media,
                )
            )
        await self._persist_analyses(
            event_id=text_response.dataset_event_id,
            guild_id=request.message.guild_id,
            policy_version=text_response.policy_version,
            analyses=analyses,
            labels_by_attachment=labels_by_attachment,
        )
        summaries = tuple(
            self._to_summary(
                analysis,
                labels_by_attachment.get(analysis.attachment.attachment_id, ()),
            )
            for analysis in analyses
        )
        media_warnings = tuple(
            dict.fromkeys(
                warning for analysis in analyses for warning in analysis.warnings
            )
        )
        response_payload = text_response.model_dump()
        response_payload.update(
            warnings=tuple(dict.fromkeys((*text_response.warnings, *media_warnings)))[
                :8
            ],
            attachments=summaries,
        )
        return MediaModerationResponseSchema.model_validate(response_payload)

    async def close(self) -> None:
        await self._downloader.close()
        await self._ocr_provider.close()
        await self._image_provider.close()

    def _validate_request_limits(self, request: MediaModerationRequestSchema) -> None:
        if len(request.attachments) > self._runtime_config.max_attachments:
            raise MediaValidationError("too many media attachments")

    async def _ingest_attachment(
        self,
        attachment: MediaAttachment,
    ) -> tuple[MediaAttachmentAnalysis, ValidatedMedia | None]:
        try:
            self._validate_attachment_hint(attachment)
            downloaded = await self._downloader.download(attachment)
            validated = await self._validator.validate(downloaded)
            hashes = await self._hasher.calculate(downloaded, validated)
        except MediaSecurityError as exc:
            return (
                self._failed_analysis(attachment, MediaAttachmentStatus.REJECTED, exc),
                None,
            )
        except MediaValidationError as exc:
            return (
                self._failed_analysis(attachment, MediaAttachmentStatus.REJECTED, exc),
                None,
            )
        except MediaError as exc:
            return (
                self._failed_analysis(
                    attachment, MediaAttachmentStatus.UNAVAILABLE, exc
                ),
                None,
            )

        return (
            MediaAttachmentAnalysis(
                attachment=attachment,
                status=MediaAttachmentStatus.ANALYZED,
                detected_mime=validated.detected_mime,
                actual_file_size=validated.file_size,
                hashes=hashes,
                known_hash_match=(
                    self._known_scam_hash_matcher.matches(hashes)
                    if self._known_scam_hash_matcher is not None
                    else False
                ),
                width=validated.width,
                height=validated.height,
                stage_latency_ms={
                    "download": downloaded.download_latency_ms,
                    "validation": validated.validation_latency_ms,
                },
            ),
            validated,
        )

    def _validate_attachment_hint(self, attachment: MediaAttachment) -> None:
        """Contain invalid metadata to this attachment instead of rejecting its message."""
        if attachment.file_size is not None and attachment.file_size > self._runtime_config.max_file_size_bytes:
            raise MediaValidationError("declared media size exceeds per-file limit")
        if attachment.width is not None and attachment.width > self._runtime_config.max_width:
            raise MediaValidationError("declared media width exceeds limit")
        if attachment.height is not None and attachment.height > self._runtime_config.max_height:
            raise MediaValidationError("declared media height exceeds limit")
        if (
            attachment.width is not None
            and attachment.height is not None
            and attachment.width * attachment.height > self._runtime_config.max_pixels
        ):
            raise MediaValidationError("declared media pixels exceed limit")

    async def _run_providers(
        self,
        analysis: MediaAttachmentAnalysis,
        validated: ValidatedMedia,
        media_policy: MediaRulePolicy | None,
    ) -> MediaAttachmentAnalysis:
        attachment = analysis.attachment
        hashes = analysis.hashes
        if hashes is None:
            return analysis
        warnings: list[str] = []
        ocr_result = None
        ocr_policy = media_policy.ocr.ocr if media_policy is not None else None
        yolo_policy = media_policy.yolo.yolo if media_policy is not None else None
        ocr_enabled = (
            ocr_policy.enabled if ocr_policy is not None else self._ocr_provider.enabled
        )
        ocr_required = (
            ocr_policy.required
            if ocr_policy is not None
            else self._runtime_config.ocr_required
        )
        if ocr_enabled and self._ocr_provider.enabled and self._ocr_provider.ready:
            try:
                ocr_result = await self._ocr_provider.analyze(
                    OcrInput(
                        attachment_id=attachment.attachment_id,
                        image_bytes=validated.analysis_bytes,
                        sha256=hashes.sha256,
                        width=validated.width,
                        height=validated.height,
                    )
                )
                if ocr_policy is not None and self._ocr_policy_processor is not None:
                    ocr_result = self._ocr_policy_processor.apply(
                        ocr_result, ocr_policy
                    )
            except MediaError as exc:
                if ocr_required:
                    return self._failed_analysis(
                        attachment, MediaAttachmentStatus.UNAVAILABLE, exc
                    )
                warnings.append(exc.code)
        else:
            warnings.append("ocr_unavailable" if ocr_enabled else "ocr_disabled")

        image_result = None
        yolo_enabled = (
            yolo_policy.enabled
            if yolo_policy is not None
            else self._image_provider.enabled
        )
        yolo_required = (
            yolo_policy.required
            if yolo_policy is not None
            else self._runtime_config.image_required
        )
        if yolo_enabled:
            try:
                image_result = await self._image_provider.analyze(
                    ImageDetectionInput(
                        attachment_id=attachment.attachment_id,
                        image_bytes=validated.analysis_bytes,
                        sha256=hashes.sha256,
                        width=validated.width,
                        height=validated.height,
                    )
                )
            except MediaError as exc:
                if yolo_required:
                    return self._failed_analysis(
                        attachment, MediaAttachmentStatus.UNAVAILABLE, exc
                    )
                warnings.append(exc.code)
        else:
            warnings.append("image_provider_disabled")
        if yolo_required and not self._image_provider.ready:
            return self._failed_analysis(
                attachment,
                MediaAttachmentStatus.UNAVAILABLE,
                MediaError("image provider unavailable"),
            )
        if image_result is not None and yolo_policy is not None:
            image_result = self._apply_yolo_policy(image_result, yolo_policy)
        if image_result is not None:
            warnings.extend(image_result.warnings)
        return analysis.model_copy(
            update={
                "ocr_result": ocr_result,
                "image_result": image_result,
                "warnings": tuple(dict.fromkeys(warnings)),
                "stage_latency_ms": {
                    **analysis.stage_latency_ms,
                    "ocr": (
                        ocr_result.processing_time_ms if ocr_result is not None else 0
                    ),
                    "image": (
                        image_result.processing_time_ms
                        if image_result is not None
                        else 0
                    ),
                },
            },
        )

    @classmethod
    def _apply_yolo_policy(
        cls, result: ImageDetectionResult, policy: YoloRuleSettings
    ) -> ImageDetectionResult:
        candidates = [
            detection
            for detection in result.detections
            if (
                (
                    class_policy := policy.classes.get(
                        detection.detector_class.strip().casefold()
                    )
                )
                is not None
                and class_policy.enabled
                and detection.confidence
                >= max(
                    policy.inference.confidence_threshold, class_policy.min_confidence
                )
            )
        ]
        kept = []
        for candidate in sorted(
            candidates, key=lambda item: item.confidence, reverse=True
        ):
            if all(
                candidate.detector_class != accepted.detector_class
                or candidate.bounding_box is None
                or accepted.bounding_box is None
                or cls._box_iou(candidate.bounding_box, accepted.bounding_box)
                <= policy.inference.iou_threshold
                for accepted in kept
            ):
                kept.append(candidate)
        return result.model_copy(
            update={"detections": tuple(kept[: policy.inference.max_detections])}
        )

    @staticmethod
    def _box_iou(
        left: tuple[float, float, float, float],
        right: tuple[float, float, float, float],
    ) -> float:
        intersection_width = max(0.0, min(left[2], right[2]) - max(left[0], right[0]))
        intersection_height = max(0.0, min(left[3], right[3]) - max(left[1], right[1]))
        intersection = intersection_width * intersection_height
        left_area = max(0.0, left[2] - left[0]) * max(0.0, left[3] - left[1])
        right_area = max(0.0, right[2] - right[0]) * max(0.0, right[3] - right[1])
        union = left_area + right_area - intersection
        return intersection / union if union else 0.0

    @staticmethod
    def _failed_analysis(
        attachment: MediaAttachment,
        status: MediaAttachmentStatus,
        error: MediaError,
    ) -> MediaAttachmentAnalysis:
        logger.warning(
            "Media attachment failed attachment_id=%s status=%s error_type=%s",
            attachment.attachment_id,
            status.value,
            type(error).__name__,
        )
        return MediaAttachmentAnalysis(
            attachment=attachment,
            status=status,
            warnings=(error.code,),
        )

    async def _persist_analyses(
        self,
        *,
        event_id: int,
        guild_id: str,
        policy_version: str,
        analyses: list[MediaAttachmentAnalysis],
        labels_by_attachment: dict[str, tuple[str, ...]],
    ) -> None:
        if event_id <= 0:
            return
        retention_until = datetime.now(timezone.utc) + timedelta(
            hours=self._runtime_config.retention_hours
        )
        try:
            for analysis in analyses:
                await self._attachment_repository.save(
                    MediaAttachmentRecord(
                        event_id=event_id,
                        guild_id=guild_id,
                        attachment_id=analysis.attachment.attachment_id,
                        file_name=analysis.attachment.file_name,
                        # Discord may omit content_type. Persistence keeps a
                        # stable, non-sensitive fallback while detected_mime
                        # remains the authoritative bytes-derived format.
                        declared_mime=(
                            analysis.attachment.content_type
                            or "application/octet-stream"
                        ),
                        detected_mime=analysis.detected_mime,
                        file_size=analysis.actual_file_size
                        or analysis.attachment.file_size
                        or 0,
                        width=analysis.width,
                        height=analysis.height,
                        hashes=analysis.hashes,
                        redacted_ocr_text=(
                            analysis.ocr_result.redacted_text
                            if analysis.ocr_result
                            else None
                        ),
                        ocr_language=(
                            analysis.ocr_result.language
                            if analysis.ocr_result
                            else None
                        ),
                        ocr_confidence=(
                            analysis.ocr_result.confidence
                            if analysis.ocr_result
                            else None
                        ),
                        ocr_text_hash=(
                            analysis.ocr_result.text_hash
                            if analysis.ocr_result
                            else None
                        ),
                        ocr_flags=(
                            analysis.ocr_result.flags if analysis.ocr_result else ()
                        ),
                        known_hash_match=analysis.known_hash_match,
                        retention_until=retention_until,
                    )
                )
                await self._persist_stage_results(
                    event_id, policy_version, analysis, labels_by_attachment
                )
        except Exception as exc:
            logger.error(
                "Media persistence failed event_id=%s error_type=%s",
                event_id,
                type(exc).__name__,
            )
            raise MediaPersistenceError("media persistence failed") from exc

    async def _persist_stage_results(
        self,
        event_id: int,
        policy_version: str,
        analysis: MediaAttachmentAnalysis,
        labels_by_attachment: dict[str, tuple[str, ...]],
    ) -> None:
        attachment_id = analysis.attachment.attachment_id
        if analysis.ocr_result is not None:
            result = analysis.ocr_result
            await self._analysis_repository.save(
                MediaAnalysisRecord(
                    event_id=event_id,
                    attachment_id=attachment_id,
                    stage=MediaAnalysisStage.OCR,
                    model_name=result.model_name,
                    model_version=result.model_version,
                    input_version=self._runtime_config.input_version,
                    policy_version=policy_version,
                    output={
                        "language": result.language,
                        "text_hash": result.text_hash,
                        "flags": list(result.flags),
                        "warnings": list(result.warnings),
                    },
                    labels=labels_by_attachment.get(attachment_id, ()),
                    confidence=result.confidence,
                    latency_ms=result.processing_time_ms,
                )
            )
        if analysis.image_result is not None:
            result = analysis.image_result
            await self._analysis_repository.save(
                MediaAnalysisRecord(
                    event_id=event_id,
                    attachment_id=attachment_id,
                    stage=MediaAnalysisStage.IMAGE,
                    model_name=result.model_name,
                    model_version=result.model_version,
                    input_version=self._runtime_config.input_version,
                    policy_version=policy_version,
                    output={
                        "detections": [
                            detection.model_dump(mode="json")
                            for detection in result.detections
                        ],
                        "warnings": list(result.warnings),
                    },
                    labels=labels_by_attachment.get(attachment_id, ()),
                    confidence=max(
                        (detection.confidence for detection in result.detections),
                        default=None,
                    ),
                    latency_ms=result.processing_time_ms,
                )
            )

    @staticmethod
    def _to_summary(
        analysis: MediaAttachmentAnalysis,
        labels: tuple[str, ...],
    ) -> MediaAttachmentSummarySchema:
        provider_result = analysis.image_result or analysis.ocr_result
        return MediaAttachmentSummarySchema(
            attachment_id=analysis.attachment.attachment_id,
            status=analysis.status,
            detected_mime=analysis.detected_mime,
            sha256=analysis.hashes.sha256 if analysis.hashes else None,
            ocr_language=analysis.ocr_result.language if analysis.ocr_result else None,
            ocr_confidence=(
                analysis.ocr_result.confidence if analysis.ocr_result else None
            ),
            labels=labels,
            warnings=analysis.warnings,
            stage_latency_ms=analysis.stage_latency_ms,
            model_name=provider_result.model_name if provider_result else None,
            model_version=provider_result.model_version if provider_result else None,
        )
