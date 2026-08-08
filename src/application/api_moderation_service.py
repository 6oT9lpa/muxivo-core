"""Application service that orchestrates one moderation API request.

The service coordinates preprocessing, policy lookup, deterministic rules,
optional ML inference, decisioning and dataset collection.  It deliberately
does not execute Discord actions: the bot owns enforcement.
"""

from __future__ import annotations

import asyncio
from time import perf_counter

from src.application.api_conflict_error import ApiConflictError
from src.application.api_not_found_error import ApiNotFoundError
from src.application.api_resource_unavailable_error import ApiResourceUnavailableError
from src.contracts.api.action_result_request_schema import ActionResultRequestSchema
from src.contracts.api.api_ack_schema import ApiAckSchema
from src.contracts.api.effective_policy_response_schema import (
    EffectivePolicyResponseSchema,
)
from src.contracts.api.moderation_feedback_request_schema import (
    ModerationFeedbackRequestSchema,
)
from src.contracts.api.moderation_message_request_schema import (
    ModerationMessageRequestSchema,
)
from src.contracts.api.moderation_message_response_schema import (
    ModerationMessageResponseSchema,
)
from src.contracts.message_preprocess_input_schema import MessagePreprocessInputSchema
from src.contracts.rules.moderation_rule_policy import ModerationRulePolicy
from src.domain.action.action_execution_status import ActionExecutionStatus
from src.domain.api.moderation_event_repository import ModerationEventRepository
from src.domain.dto.dataset.dataset_collection_input import DatasetCollectionInput
from src.domain.media.media_analysis_bundle import MediaAnalysisBundle
from src.domain.media.media_rule_policy import MediaRulePolicy
from src.domain.media.ocr_result import OcrResult
from src.domain.moderation.moderation_action import ModerationAction
from src.domain.moderation.moderation_label import ModerationLabel
from src.domain.policy.policy_type import PolicyType
from src.domain.rules.moderation_signal import ModerationSignal
from src.domain.rules.signal_source import SignalSource
from src.infrastructure.logging import get_logger
from src.modules.dataset.dataset_collector import DatasetCollector
from src.modules.decision.decision_engine import DecisionEngine
from src.modules.policy.policy_resolver import PolicyResolver
from src.modules.phishing.phishing_link_service import PhishingLinkService
from src.modules.preprocessing.text_preprocessor import TextPreprocessor
from src.modules.rules.moderation_rule_engine import ModerationRuleEngine
from src.modules.rules.preprocessing_signal_adapter import PreprocessingSignalAdapter
from src.training.rubert.rubert_moderation_classifier import RuBertModerationClassifier

logger = get_logger(__name__)


class ApiModerationService:
    """Run the explainable moderation pipeline and expose API-safe responses."""

    def __init__(
        self,
        preprocessor: TextPreprocessor,
        rule_engine: ModerationRuleEngine,
        decision_engine: DecisionEngine,
        signal_adapter: PreprocessingSignalAdapter,
        dataset_collector: DatasetCollector,
        policy_resolver: PolicyResolver,
        event_repository: ModerationEventRepository,
        inference_semaphore: asyncio.Semaphore,
        rubert_classifier: RuBertModerationClassifier | None,
        phishing_link_service: PhishingLinkService,
    ) -> None:
        self._preprocessor = preprocessor
        self._rule_engine = rule_engine
        self._decision_engine = decision_engine
        self._signal_adapter = signal_adapter
        self._dataset_collector = dataset_collector
        self._policy_resolver = policy_resolver
        self._event_repository = event_repository
        self._inference_semaphore = inference_semaphore
        self._rubert_classifier = rubert_classifier
        self._phishing_link_service = phishing_link_service

    async def moderate(
        self,
        request: ModerationMessageRequestSchema,
        correlation_id: str,
        *,
        persist: bool = True,
    ) -> ModerationMessageResponseSchema:
        """Classify one message and persist every decision in Dataset Collector."""
        response, _ = await self._moderate(
            request, correlation_id, persist=persist, media=None, media_policy=None
        )
        return response

    async def moderate_media(
        self,
        request: ModerationMessageRequestSchema,
        media: MediaAnalysisBundle,
        correlation_id: str,
        *,
        persist: bool = True,
        media_policy: MediaRulePolicy | None = None,
    ) -> tuple[ModerationMessageResponseSchema, dict[str, tuple[str, ...]]]:
        """Run one decision flow with text, OCR and image-derived signals."""
        return await self._moderate(
            request,
            correlation_id,
            persist=persist,
            media=media,
            media_policy=media_policy,
        )

    async def _moderate(
        self,
        request: ModerationMessageRequestSchema,
        correlation_id: str,
        *,
        persist: bool,
        media: MediaAnalysisBundle | None,
        media_policy: MediaRulePolicy | None,
    ) -> tuple[ModerationMessageResponseSchema, dict[str, tuple[str, ...]]]:
        started_at = perf_counter()
        context = await self._preprocessor.process(self._to_preprocess_input(request))
        try:
            rule_policy_resolution = await self._policy_resolver.resolve(
                PolicyType.MODERATION_RULE, context
            )
            decision_policy_resolution = await self._policy_resolver.resolve(
                PolicyType.DECISION, context
            )
        except Exception as exc:
            logger.error(
                "Policy resolution failed correlation_id=%s message_id=%s",
                correlation_id,
                request.message_id,
            )
            raise ApiResourceUnavailableError("Policy is unavailable") from exc

        signals = []
        for match in context.metadata.get("preprocessing_rule_matches", []):
            signals.extend(self._signal_adapter.adapt(match))

        rubert_result = None
        warnings: list[str] = []
        if self._rubert_classifier is None:
            warnings.append("rubert_unavailable")
        else:
            try:
                async with self._inference_semaphore:
                    rubert_result = await asyncio.to_thread(
                        self._rubert_classifier.classify, context.normalized_text
                    )
                signals.extend(
                    self._rubert_classifier.to_signals(
                        rubert_result, rule_policy_resolution.policy
                    )
                )
            except Exception:
                logger.warning(
                    "ruBERT inference fallback correlation_id=%s message_id=%s",
                    correlation_id,
                    request.message_id,
                )
                warnings.append("rubert_unavailable")

        phishing_signals = await self._phishing_link_service.build_signals(
            context,
            signals,
            rule_policy_resolution.policy.phishing,
        )
        signals.extend(phishing_signals)

        media_labels: dict[str, tuple[str, ...]] = {}
        if media is not None:
            media_signals, media_warnings = await self._build_media_signals(
                media,
                rule_policy_resolution.policy,
                correlation_id,
                request,
                media_policy,
            )
            signals.extend(media_signals)
            warnings.extend(media_warnings)
            for attachment in media.attachments:
                media_labels[attachment.attachment.attachment_id] = tuple(
                    dict.fromkeys(
                        signal.label.value
                        for signal in media_signals
                        if signal.evidence.get("attachment_id")
                        == attachment.attachment.attachment_id
                    )
                )

        rule_evaluation = self._rule_engine.evaluate(
            request.message_id,
            signals,
            rule_policy_resolution.policy,
            context,
        )
        decision = self._decision_engine.decide(
            request.message_id,
            rule_evaluation,
            decision_policy_resolution.policy,
        )
        dataset_event_id = 0
        if persist:
            try:
                collection = await self._dataset_collector.collect(
                    DatasetCollectionInput(
                        context=context,
                        rule_evaluation=rule_evaluation,
                        decision=decision,
                        correlation_id=correlation_id,
                    )
                )
                dataset_event_id = collection.event_id
            except Exception as exc:
                logger.error(
                    "Dataset persistence failed correlation_id=%s message_id=%s",
                    correlation_id,
                    request.message_id,
                )
                raise ApiResourceUnavailableError("Database is unavailable") from exc

        response = ModerationMessageResponseSchema(
            correlation_id=correlation_id,
            message_id=request.message_id,
            labels=tuple(label.value for label in decision.labels),
            primary_label=decision.primary_label.value,
            rule_matches=tuple(rule_evaluation.matched_rules),
            rubert_labels=(
                tuple(label.value for label in rubert_result.labels)
                if rubert_result
                else ()
            ),
            rubert_scores=(
                {
                    label.value: round(score, 6)
                    for label, score in rubert_result.scores.items()
                }
                if rubert_result
                else {}
            ),
            rubert_thresholds=(
                {
                    label.value: threshold
                    for label, threshold in rubert_result.thresholds.items()
                }
                if rubert_result
                else {}
            ),
            rubert_top_labels=(
                tuple(str(item["label"]) for item in rubert_result.top_labels)
                if rubert_result
                else ()
            ),
            risk_score=round(decision.risk_score, 4),
            confidence=round(decision.confidence, 6),
            risk_breakdown=tuple(
                item.label.value for item in rule_evaluation.risk_breakdown
            ),
            decision_action=decision.decision_action.value,
            severity=decision.severity,
            reason=decision.reason[:256],
            policy_id=decision.policy_id,
            policy_version=decision.policy_version,
            execution_status=(
                ActionExecutionStatus.PENDING
                if persist
                else ActionExecutionStatus.DRY_RUN
            ).value,
            execution_plan=tuple(
                action.value for action in decision.action_plan.actions
            ),
            dataset_event_id=dataset_event_id,
            latency_ms=round((perf_counter() - started_at) * 1_000),
            warnings=tuple(warnings),
        )
        logger.info(
            "Moderation API completed correlation_id=%s message_id=%s action=%s latency_ms=%s persisted=%s",
            correlation_id,
            request.message_id,
            response.decision_action,
            response.latency_ms,
            persist,
        )
        return response, media_labels

    async def _build_media_signals(
        self,
        media: MediaAnalysisBundle,
        policy: ModerationRulePolicy,
        correlation_id: str,
        message: ModerationMessageRequestSchema | str,
        media_policy: MediaRulePolicy | None = None,
    ) -> tuple[list[ModerationSignal], list[str]]:
        request = (
            message if isinstance(message, ModerationMessageRequestSchema) else None
        )
        message_id = request.message_id if request is not None else message
        signals: list[ModerationSignal] = []
        warnings: list[str] = []
        for attachment in media.attachments:
            warnings.extend(attachment.warnings)
            if attachment.known_hash_match:
                signals.append(
                    ModerationSignal(
                        source=SignalSource.IMAGE,
                        label=ModerationLabel.SCAM,
                        confidence=1.0,
                        severity=5,
                        risk_weight=round(
                            getattr(policy.label_weights, ModerationLabel.SCAM.value)
                        ),
                        evidence={"attachment_id": attachment.attachment.attachment_id},
                        reason="known_scam_hash_match",
                        rule_id="image.known_scam_hash",
                        model_name="known-scam-hash-registry",
                        model_version="1",
                    )
                )
            ocr_result = attachment.ocr_result
            if ocr_result is not None:
                warnings.extend(ocr_result.warnings)
                if ocr_result.text:
                    signals.extend(
                        await self._classify_ocr_text(
                            ocr_result,
                            policy,
                            correlation_id,
                            message_id,
                            request,
                        )
                    )

            image_result = attachment.image_result
            if image_result is None:
                continue
            warnings.extend(image_result.warnings)
            for detection in image_result.detections:
                detector_class = detection.detector_class.strip().casefold()
                class_policy = (
                    media_policy.yolo.yolo.classes.get(detector_class)
                    if media_policy is not None
                    else None
                )
                if media_policy is not None:
                    if class_policy is None or not class_policy.enabled:
                        continue
                    label = ModerationLabel(class_policy.moderation_label)
                    threshold = class_policy.min_confidence
                    severity = class_policy.severity
                else:
                    label = policy.media.image_class_to_label.get(detector_class)
                    if label is None:
                        continue
                    threshold = policy.media.image_class_thresholds.get(
                        detector_class,
                        policy.confidence_thresholds.per_source_min_confidence.get(
                            SignalSource.IMAGE.value, 0.5
                        ),
                    )
                    severity = 4
                if detection.confidence < threshold:
                    continue
                signals.append(
                    ModerationSignal(
                        source=SignalSource.IMAGE,
                        label=label,
                        confidence=detection.confidence,
                        severity=severity,
                        risk_weight=round(getattr(policy.label_weights, label.value)),
                        evidence={
                            "attachment_id": attachment.attachment.attachment_id,
                            "detector_class": detector_class,
                            "bounding_box": detection.bounding_box,
                            "threshold": threshold,
                            "model_name": image_result.model_name,
                            "model_version": image_result.model_version,
                        },
                        reason="image_detector_policy_mapping",
                        rule_id=f"image.{detector_class}",
                        model_name=image_result.model_name,
                        model_version=image_result.model_version,
                    )
                )
        return signals, list(dict.fromkeys(warnings))

    async def _classify_ocr_text(
        self,
        ocr_result: OcrResult,
        policy: ModerationRulePolicy,
        correlation_id: str,
        message_id: str,
        request: ModerationMessageRequestSchema | None = None,
    ) -> list[ModerationSignal]:
        try:
            normalized_text = ocr_result.text
            adapted: list[ModerationSignal] = []
            if request is not None:
                preprocess_input = self._to_preprocess_input(request)
                metadata = dict(preprocess_input.metadata)
                metadata.update(
                    {"source": "OCR", "attachment_id": ocr_result.attachment_id}
                )
                context = await self._preprocessor.process(
                    preprocess_input.model_copy(
                        update={"raw_text": ocr_result.text, "metadata": metadata}
                    )
                )
                normalized_text = context.normalized_text
                for match in context.metadata.get("preprocessing_rule_matches", []):
                    for signal in self._signal_adapter.adapt(match):
                        if signal.label == ModerationLabel.SAFE:
                            continue
                        adapted.append(
                            signal.model_copy(
                                update={
                                    "source": SignalSource.OCR,
                                    "evidence": {
                                        **signal.evidence,
                                        "attachment_id": ocr_result.attachment_id,
                                        "ocr_model_name": ocr_result.model_name,
                                        "ocr_model_version": ocr_result.model_version,
                                    },
                                    "reason": "ocr_text_preprocessing_signal",
                                }
                            )
                        )
            if self._rubert_classifier is None or not normalized_text:
                return adapted
            async with self._inference_semaphore:
                result = await asyncio.to_thread(
                    self._rubert_classifier.classify, normalized_text
                )
            for signal in self._rubert_classifier.to_signals(result, policy):
                if signal.label == ModerationLabel.SAFE:
                    continue
                confidence = min(
                    signal.confidence, ocr_result.confidence or signal.confidence
                )
                adapted.append(
                    signal.model_copy(
                        update={
                            "source": SignalSource.OCR,
                            "confidence": confidence,
                            "evidence": {
                                **signal.evidence,
                                "attachment_id": ocr_result.attachment_id,
                                "ocr_confidence": ocr_result.confidence,
                                "ocr_language": ocr_result.language,
                                "ocr_model_name": ocr_result.model_name,
                                "ocr_model_version": ocr_result.model_version,
                            },
                            "reason": "ocr_text_rubert_classifier",
                            "rule_id": f"ocr.{signal.label.value.casefold()}",
                        }
                    )
                )
            return adapted
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                "OCR text classification fallback correlation_id=%s message_id=%s attachment_id=%s",
                correlation_id,
                message_id,
                ocr_result.attachment_id,
            )
            return []

    async def moderate_batch(
        self,
        requests: (
            list[ModerationMessageRequestSchema]
            | tuple[ModerationMessageRequestSchema, ...]
        ),
        correlation_id_prefix: str,
    ) -> list[ModerationMessageResponseSchema]:
        """Classify messages as a batch while preserving per-message decisions.

        The deterministic preprocessing/policy/decision stages remain
        per-message because they depend on message context and persistence.
        ruBERT inference is batched to avoid one CUDA launch per Discord event.
        """
        if not requests:
            return []

        started_at_by_message = {
            request.message_id: perf_counter() for request in requests
        }
        contexts = await asyncio.gather(
            *(
                self._preprocessor.process(self._to_preprocess_input(request))
                for request in requests
            )
        )
        try:
            rule_policy_resolutions = await asyncio.gather(
                *(
                    self._policy_resolver.resolve(PolicyType.MODERATION_RULE, context)
                    for context in contexts
                )
            )
            decision_policy_resolutions = await asyncio.gather(
                *(
                    self._policy_resolver.resolve(PolicyType.DECISION, context)
                    for context in contexts
                )
            )
        except Exception as exc:
            logger.error(
                "Batch policy resolution failed correlation_id_prefix=%s batch_size=%s",
                correlation_id_prefix,
                len(requests),
            )
            raise ApiResourceUnavailableError("Policy is unavailable") from exc

        signals_by_index: list[list] = []
        warnings_by_index: list[list[str]] = []
        for context in contexts:
            signals = []
            for match in context.metadata.get("preprocessing_rule_matches", []):
                signals.extend(self._signal_adapter.adapt(match))
            signals_by_index.append(signals)
            warnings_by_index.append([])

        rubert_results = [None] * len(requests)
        if self._rubert_classifier is None:
            for warnings in warnings_by_index:
                warnings.append("rubert_unavailable")
        else:
            try:
                async with self._inference_semaphore:
                    batch_results = await asyncio.to_thread(
                        self._rubert_classifier.classify_batch,
                        [context.normalized_text for context in contexts],
                    )
                rubert_results = list(batch_results)
                for index, rubert_result in enumerate(rubert_results):
                    signals_by_index[index].extend(
                        self._rubert_classifier.to_signals(
                            rubert_result, rule_policy_resolutions[index].policy
                        )
                    )
            except Exception:
                logger.warning(
                    "Batch ruBERT inference fallback correlation_id_prefix=%s batch_size=%s",
                    correlation_id_prefix,
                    len(requests),
                )
                for warnings in warnings_by_index:
                    warnings.append("rubert_unavailable")

        responses: list[ModerationMessageResponseSchema] = []
        for index, request in enumerate(requests):
            context = contexts[index]
            signals = signals_by_index[index]
            rule_policy_resolution = rule_policy_resolutions[index]
            decision_policy_resolution = decision_policy_resolutions[index]
            rubert_result = rubert_results[index]

            phishing_signals = await self._phishing_link_service.build_signals(
                context,
                signals,
                rule_policy_resolution.policy.phishing,
            )
            signals.extend(phishing_signals)

            rule_evaluation = self._rule_engine.evaluate(
                request.message_id,
                signals,
                rule_policy_resolution.policy,
                context,
            )
            decision = self._decision_engine.decide(
                request.message_id,
                rule_evaluation,
                decision_policy_resolution.policy,
            )
            try:
                collection = await self._dataset_collector.collect(
                    DatasetCollectionInput(
                        context=context,
                        rule_evaluation=rule_evaluation,
                        decision=decision,
                    )
                )
                await self._event_repository.save_request_lineage(
                    collection.event_id, f"{correlation_id_prefix}-{index}"
                )
            except Exception as exc:
                logger.error(
                    "Batch dataset persistence failed correlation_id_prefix=%s message_id=%s",
                    correlation_id_prefix,
                    request.message_id,
                )
                raise ApiResourceUnavailableError("Database is unavailable") from exc

            response = ModerationMessageResponseSchema(
                correlation_id=f"{correlation_id_prefix}-{index}",
                message_id=request.message_id,
                labels=tuple(label.value for label in decision.labels),
                primary_label=decision.primary_label.value,
                rule_matches=tuple(rule_evaluation.matched_rules),
                rubert_labels=(
                    tuple(label.value for label in rubert_result.labels)
                    if rubert_result
                    else ()
                ),
                rubert_scores=(
                    {
                        label.value: round(score, 6)
                        for label, score in rubert_result.scores.items()
                    }
                    if rubert_result
                    else {}
                ),
                rubert_thresholds=(
                    {
                        label.value: threshold
                        for label, threshold in rubert_result.thresholds.items()
                    }
                    if rubert_result
                    else {}
                ),
                rubert_top_labels=(
                    tuple(str(item["label"]) for item in rubert_result.top_labels)
                    if rubert_result
                    else ()
                ),
                risk_score=round(decision.risk_score, 4),
                confidence=round(decision.confidence, 6),
                risk_breakdown=tuple(
                    item.label.value for item in rule_evaluation.risk_breakdown
                ),
                decision_action=decision.decision_action.value,
                severity=decision.severity,
                reason=decision.reason[:256],
                policy_id=decision.policy_id,
                policy_version=decision.policy_version,
                execution_status=ActionExecutionStatus.PENDING.value,
                execution_plan=tuple(
                    action.value for action in decision.action_plan.actions
                ),
                dataset_event_id=collection.event_id,
                latency_ms=round(
                    (perf_counter() - started_at_by_message[request.message_id]) * 1_000
                ),
                warnings=tuple(warnings_by_index[index]),
            )
            responses.append(response)

        logger.info(
            "Moderation batch completed correlation_id_prefix=%s batch_size=%s",
            correlation_id_prefix,
            len(responses),
        )
        return responses

    async def submit_feedback(
        self,
        request: ModerationFeedbackRequestSchema,
        correlation_id: str,
    ) -> ApiAckSchema:
        event = await self._get_event(
            request.event_id, request.message_id, request.guild_id
        )
        if request.guild_id is not None and request.guild_id != event.guild_id:
            raise ApiNotFoundError("Moderation event was not found")
        if (
            request.original_action is not None
            and request.original_action != event.decision_action
        ):
            raise ApiConflictError("Original action does not match the issued decision")
        try:
            created = await self._event_repository.save_feedback(
                event,
                request.feedback_type,
                request.labels,
                request.primary_label,
                request.severity,
                request.recommended_action,
                request.moderator_id,
                request.annotation_source,
                request.notes,
                request.idempotency_key,
                correlation_id,
            )
        except Exception as exc:
            raise ApiResourceUnavailableError("Database is unavailable") from exc
        return ApiAckSchema(
            correlation_id=correlation_id,
            event_id=event.event_id,
            status="accepted" if created else "duplicate",
        )

    async def submit_action_result(
        self,
        request: ActionResultRequestSchema,
        correlation_id: str,
    ) -> ApiAckSchema:
        event = await self._get_event(request.event_id, request.message_id, None)
        action = request.action
        status = request.status
        if action not in self._allowed_execution_actions(event.decision_action):
            raise ApiConflictError("Action does not match the issued decision")
        error = self._safe_platform_error(
            request.platform_error_code, request.platform_error_message
        )
        try:
            await self._event_repository.save_action_result(
                event,
                action,
                status,
                request.dry_run,
                error,
                request.timestamp,
                correlation_id,
            )
        except Exception as exc:
            raise ApiResourceUnavailableError("Database is unavailable") from exc
        return ApiAckSchema(
            correlation_id=correlation_id, event_id=event.event_id, status="accepted"
        )

    async def effective_policies(
        self,
        platform: str,
        guild_id: str | None,
        channel_id: str | None,
        correlation_id: str,
    ) -> EffectivePolicyResponseSchema:
        context = {
            "platform": platform,
            "guild_id": guild_id,
            "channel_id": channel_id,
            "metadata": {},
        }
        try:
            results = await asyncio.gather(
                *(
                    self._policy_resolver.resolve(policy_type, context)
                    for policy_type in PolicyType
                )
            )
        except Exception as exc:
            raise ApiResourceUnavailableError("Policy is unavailable") from exc
        return EffectivePolicyResponseSchema(
            correlation_id=correlation_id,
            policies=tuple(
                {
                    "type": policy_type.value,
                    "id": result.policy_id,
                    "version": result.version,
                    "enabled": True,
                    "source": result.source.value,
                }
                for policy_type, result in zip(PolicyType, results, strict=True)
            ),
        )

    async def initialize_policy_status(self) -> str:
        resolution = await self._policy_resolver.resolve(
            PolicyType.DECISION, {"metadata": {}}
        )
        return resolution.version

    async def _get_event(
        self,
        event_id: int | None,
        message_id: str | None,
        guild_id: str | None,
    ):
        try:
            event = await self._event_repository.find_event(
                event_id, message_id, guild_id
            )
        except Exception as exc:
            raise ApiResourceUnavailableError("Database is unavailable") from exc
        if event is None:
            raise ApiNotFoundError("Moderation event was not found")
        return event

    def _to_preprocess_input(
        self, request: ModerationMessageRequestSchema
    ) -> MessagePreprocessInputSchema:
        payload = request.model_dump(exclude={"event_type", "user_context"})
        metadata = dict(payload.get("metadata", {}))
        metadata["event_type"] = request.event_type
        if request.user_context is not None:
            metadata["user_moderation_context"] = request.user_context.model_dump(
                mode="json"
            )
        payload["metadata"] = metadata
        return MessagePreprocessInputSchema(**payload)

    def _safe_platform_error(self, code: str | None, message: str | None) -> str | None:
        if code is None:
            return None
        return code if message is None else f"{code}: {message}"

    def _allowed_execution_actions(
        self, decision_action: ModerationAction
    ) -> tuple[ModerationAction, ...]:
        bundles = {
            ModerationAction.DELETE_WARN: (ModerationAction.WARN,),
            # Discord may safely fall back to WARN when a member cannot be
            # restricted (role hierarchy or permission constraints).  Preserve
            # that terminal outcome instead of rejecting it as a conflict.
            ModerationAction.TIMEOUT: (
                ModerationAction.DELETE,
                ModerationAction.TIMEOUT,
                ModerationAction.WARN,
            ),
            ModerationAction.KICK: (
                ModerationAction.DELETE,
                ModerationAction.KICK,
                ModerationAction.WARN,
            ),
            ModerationAction.BAN: (
                ModerationAction.DELETE,
                ModerationAction.BAN,
                ModerationAction.WARN,
            ),
        }
        return bundles.get(decision_action, (decision_action,))
