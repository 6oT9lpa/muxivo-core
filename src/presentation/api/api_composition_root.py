import asyncio
from pathlib import Path

from src.application.api_moderation_service import ApiModerationService
from src.application.effective_media_policy_resolver import EffectiveMediaPolicyResolver
from src.application.media_moderation_service import MediaModerationService
from src.application.moderation_request_queue import ModerationRequestQueue
from src.domain.media.media_runtime_config import MediaRuntimeConfig
from src.domain.media.ocr_runtime_config import OcrRuntimeConfig
from src.domain.media.yolo_runtime_config import YoloRuntimeConfig
from src.infrastructure.api.api_settings import ApiSettings
from src.infrastructure.api.internal_api_key_validator import InternalApiKeyValidator
from src.infrastructure.api.local_rate_limiter import LocalRateLimiter
from src.infrastructure.phishing.google_safe_browsing_url_reputation_provider import (
    GoogleSafeBrowsingUrlReputationProvider,
)
from src.infrastructure.phishing.rdap_domain_age_provider import RdapDomainAgeProvider
from src.infrastructure.database.connection import DatabaseConnection
from src.infrastructure.repository.postgresql_dataset_collector_repository import PostgresqlDatasetCollectorRepository
from src.infrastructure.repository.postgresql_moderation_event_repository import PostgresqlModerationEventRepository
from src.infrastructure.repository.postgresql_policy_repository import PostgresqlPolicyRepository
from src.infrastructure.logging import get_logger
from src.infrastructure.media.disabled_image_detection_provider import DisabledImageDetectionProvider
from src.infrastructure.media.disabled_ocr_provider import DisabledOcrProvider
from src.infrastructure.media.http_media_downloader import HttpMediaDownloader
from src.infrastructure.media.ocr_text_processor import OcrTextProcessor
from src.infrastructure.media.ocr_policy_result_processor import OcrPolicyResultProcessor
from src.infrastructure.media.paddle_ocr_provider import PaddleOcrProvider
from src.infrastructure.media.onnx_yolo_detection_provider import OnnxYoloDetectionProvider
from src.infrastructure.media.pillow_media_hasher import PillowMediaHasher
from src.infrastructure.media.json_known_scam_hash_matcher import JsonKnownScamHashMatcher
from src.infrastructure.media.pillow_media_validator import PillowMediaValidator
from src.infrastructure.media.yaml_media_policy_defaults_provider import YamlMediaPolicyDefaultsProvider
from src.infrastructure.repository.postgresql_media_analysis_result_repository import PostgresqlMediaAnalysisResultRepository
from src.infrastructure.repository.postgresql_media_attachment_repository import PostgresqlMediaAttachmentRepository
from src.infrastructure.repository.postgresql_media_policy_repository import PostgresqlMediaPolicyRepository
from src.modules.dataset.dataset_collector import DatasetCollector
from src.modules.decision.decision_engine import DecisionEngine
from src.modules.policy.policy_resolver import PolicyResolver
from src.modules.phishing.phishing_link_service import PhishingLinkService
from src.modules.preprocessing.text_preprocessor import TextPreprocessor
from src.modules.rules.moderation_rule_engine import ModerationRuleEngine
from src.modules.rules.preprocessing_signal_adapter import PreprocessingSignalAdapter
from src.presentation.api.api_container import ApiContainer
from src.training.rubert.rubert_moderation_classifier import RuBertModerationClassifier

logger = get_logger(__name__)


class ApiCompositionRoot:
    def __init__(self, database_url: str, settings: ApiSettings) -> None:
        self._database_url = database_url
        self._settings = settings

    def build(self) -> ApiContainer:
        database = DatabaseConnection(self._database_url)
        media_policy_resolver = EffectiveMediaPolicyResolver(
            PostgresqlMediaPolicyRepository(database),
            YamlMediaPolicyDefaultsProvider(
                ocr_path=Path(self._settings.media_ocr_policy_path),
                yolo_path=Path(self._settings.media_yolo_policy_path),
            ),
        )
        policy_repository = PostgresqlPolicyRepository(database)
        policy_resolver = PolicyResolver(policy_repository)
        inference_semaphore = asyncio.Semaphore(self._settings.api_inference_concurrency)
        classifier = self._load_classifier()
        phishing_link_service = self._build_phishing_link_service()
        service = ApiModerationService(
            preprocessor=TextPreprocessor(),
            rule_engine=ModerationRuleEngine(),
            decision_engine=DecisionEngine(),
            signal_adapter=PreprocessingSignalAdapter(),
            dataset_collector=DatasetCollector(PostgresqlDatasetCollectorRepository(database)),
            policy_resolver=policy_resolver,
            event_repository=PostgresqlModerationEventRepository(database),
            inference_semaphore=inference_semaphore,
            rubert_classifier=classifier,
            phishing_link_service=phishing_link_service,
        )
        moderation_queue = ModerationRequestQueue(service, self._settings.api_queue_workers, self._settings.api_queue_size)
        ocr_text_processor = OcrTextProcessor(self._settings.ocr_max_text_length)
        ocr_provider = self._build_ocr_provider(ocr_text_processor)
        image_provider = self._build_image_provider()
        media_service = MediaModerationService(
            moderation_service=service,
            downloader=HttpMediaDownloader(
                allowed_hosts=self._settings.media_allowed_download_hosts,
                max_file_size_bytes=self._settings.media_max_file_size_bytes,
                timeout_seconds=self._settings.media_download_timeout_seconds,
                max_redirects=self._settings.media_max_redirects,
                proxy_url=self._settings.media_proxy_url,
            ),
            validator=PillowMediaValidator(
                allowed_content_types=self._settings.media_allowed_content_types,
                max_width=self._settings.media_max_width,
                max_height=self._settings.media_max_height,
                max_pixels=self._settings.media_max_pixels,
            ),
            hasher=PillowMediaHasher(),
            ocr_provider=ocr_provider,
            image_provider=image_provider,
            attachment_repository=PostgresqlMediaAttachmentRepository(database),
            analysis_repository=PostgresqlMediaAnalysisResultRepository(database),
            runtime_config=MediaRuntimeConfig(
                enabled=self._settings.media_enabled,
                required=self._settings.media_required,
                max_attachments=self._settings.media_max_attachments,
                max_file_size_bytes=self._settings.media_max_file_size_bytes,
                max_total_size_bytes=self._settings.media_max_total_size_bytes,
                max_width=self._settings.media_max_width,
                max_height=self._settings.media_max_height,
                max_pixels=self._settings.media_max_pixels,
                retention_hours=self._settings.media_retention_hours,
                hash_cache_ttl_hours=self._settings.media_hash_cache_ttl,
                input_version=self._settings.media_input_version,
                ocr_required=self._settings.ocr_required,
                image_required=self._settings.yolo_required,
            ),
            media_policy_resolver=media_policy_resolver,
            ocr_policy_processor=OcrPolicyResultProcessor(ocr_text_processor),
            known_scam_hash_matcher=(
                JsonKnownScamHashMatcher.from_file(
                    Path(self._settings.media_known_scam_hash_registry_path),
                    max_phash_distance=self._settings.media_known_scam_phash_distance,
                )
                if self._settings.media_known_scam_hash_registry_path
                else None
            ),
        )
        container = ApiContainer(
            service=service,
            database=database,
            key_validator=InternalApiKeyValidator(self._settings.internal_api_key or ""),
            rate_limiter=LocalRateLimiter(self._settings.api_rate_limit, self._settings.api_rate_window_seconds),
            inference_semaphore=inference_semaphore,
            moderation_queue=moderation_queue,
            media_service=media_service,
            media_policy_resolver=media_policy_resolver,
        )
        container.rubert_enabled = self._settings.api_rubert_enabled
        container.rubert_required = self._settings.api_rubert_required
        container.rubert_ready = classifier is not None
        container.model_id = classifier.model_dir.name if classifier else None
        container.media_enabled = self._settings.media_enabled
        container.media_required = self._settings.media_required
        container.media_ready = True
        container.ocr_enabled = self._settings.ocr_enabled
        container.ocr_required = self._settings.ocr_required
        container.ocr_ready = ocr_provider.ready
        container.image_enabled = self._settings.yolo_enabled
        container.image_required = self._settings.yolo_required
        container.image_ready = image_provider.ready
        return container

    def _build_ocr_provider(self, text_processor: OcrTextProcessor):
        if not self._settings.ocr_enabled:
            logger.info("OCR is disabled")
            return DisabledOcrProvider()
        return PaddleOcrProvider(
            runtime_config=OcrRuntimeConfig(
                detection_model_dir=Path(self._settings.ocr_detection_model_dir or ""),
                recognition_model_dir=Path(self._settings.ocr_recognition_model_dir or ""),
                device="cpu",
                cpu_threads=self._settings.ocr_cpu_threads,
                enable_mkldnn=self._settings.ocr_enable_mkldnn,
                inference_concurrency=self._settings.ocr_inference_concurrency,
                timeout_seconds=self._settings.ocr_timeout_seconds,
                model_checksum=self._settings.ocr_model_checksum or "",
            ),
            semaphore=asyncio.Semaphore(self._settings.ocr_inference_concurrency),
            text_processor=text_processor,
        )

    def _build_image_provider(self):
        if not self._settings.yolo_enabled:
            logger.info("YOLO image detection is disabled")
            return DisabledImageDetectionProvider()
        return OnnxYoloDetectionProvider(
            runtime_config=YoloRuntimeConfig(
                model_dir=Path(self._settings.yolo_model_dir or ""),
                device=self._settings.yolo_device,
                inference_concurrency=self._settings.yolo_inference_concurrency,
                timeout_seconds=self._settings.yolo_timeout_seconds,
                confidence_threshold=self._settings.yolo_confidence_threshold,
                iou_threshold=self._settings.yolo_iou_threshold,
                max_detections=self._settings.yolo_max_detections,
            ),
            semaphore=asyncio.Semaphore(self._settings.yolo_inference_concurrency),
        )

    def _build_phishing_link_service(self) -> PhishingLinkService:
        if not self._settings.phishing_enabled:
            logger.info("Phishing URL checking is disabled")
            return PhishingLinkService(domain_age_provider=None, reputation_provider=None)

        domain_age_provider = (
            RdapDomainAgeProvider(self._settings.phishing_request_timeout_seconds)
            if self._settings.phishing_rdap_enabled
            else None
        )
        reputation_provider = (
            GoogleSafeBrowsingUrlReputationProvider(
                self._settings.phishing_google_safe_browsing_api_key,
                self._settings.phishing_request_timeout_seconds,
            )
            if self._settings.phishing_google_safe_browsing_api_key
            else None
        )
        logger.info(
            "Phishing URL checking configured domain_age_provider=%s reputation_provider=%s",
            domain_age_provider is not None,
            reputation_provider is not None,
        )
        return PhishingLinkService(
            domain_age_provider=domain_age_provider,
            reputation_provider=reputation_provider,
        )

    def _load_classifier(self) -> RuBertModerationClassifier | None:
        if not self._settings.api_rubert_enabled:
            return None
        try:
            model_dir = Path(self._settings.api_rubert_model_dir)
            classifier = RuBertModerationClassifier(model_dir=model_dir)
            logger.info("Local ruBERT loaded model_dir=%s device=%s", classifier.model_dir, classifier.device)
            return classifier
        except Exception as exc:
            logger.warning(
                "Local ruBERT is unavailable; moderation will use rule-based fallback model_dir=%s error=%s",
                self._settings.api_rubert_model_dir,
                exc,
            )
            return None
