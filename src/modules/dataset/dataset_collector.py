from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from src.domain.dataset.dataset_collector_repository import DatasetCollectorRepository
from src.domain.dataset.dataset_source import DatasetSource
from src.domain.dto.dataset.dataset_collection_input import DatasetCollectionInput
from src.domain.dto.dataset.dataset_collection_record import DatasetCollectionRecord
from src.domain.dto.dataset.dataset_collection_result import DatasetCollectionResult
from src.domain.dto.dataset.training_example import TrainingExample
from src.domain.moderation.moderation_action import ModerationAction
from src.domain.moderation.moderation_label import ModerationLabel
from src.infrastructure.logging.logger import get_logger
from src.modules.dataset.dataset_text_sanitizer import DatasetTextSanitizer

logger = get_logger(__name__)


class DatasetCollector:
    DEFAULT_RETENTION_DAYS = 90
    MAX_RETENTION_DAYS = 365

    def __init__(
        self,
        repository: DatasetCollectorRepository,
        *,
        text_sanitizer: DatasetTextSanitizer | None = None,
        default_retention_days: int = DEFAULT_RETENTION_DAYS,
    ) -> None:
        if default_retention_days <= 0:
            raise ValueError("default_retention_days must be greater than zero")
        self._repository = repository
        self._text_sanitizer = text_sanitizer or DatasetTextSanitizer()
        self._default_retention_days = default_retention_days

    async def collect(self, item: DatasetCollectionInput) -> DatasetCollectionResult:
        context = item.context
        source = item.source or self._resolve_source(item)
        snapshot = self._text_sanitizer.build_snapshot(context, store_raw_text=item.store_raw_text)
        features = self._build_features(item)
        training_example = self._build_training_example(item, source, snapshot.model_text, features)

        record = DatasetCollectionRecord(
            platform=context.platform,
            guild_id=context.guild_id,
            channel_id=context.channel_id,
            user_id=context.user_id,
            message_id=context.message_id,
            event_type=str(context.metadata.get("event_type", "message_create")),
            source=source,
            text=snapshot,
            language=context.language,
            reply_to_message_id=context.reply_to_message_id,
            has_attachments=context.has_attachments,
            attachment_count=context.attachment_count,
            features=features,
            rule_evaluation=item.rule_evaluation,
            decision=item.decision,
            action_result=item.action_result,
            feedback=item.feedback,
            training_example=training_example,
            created_at=context.created_at,
            processed_at=datetime.now(timezone.utc),
            retention_until=self._resolve_retention_until(context),
            correlation_id=item.correlation_id,
            metadata={
                "dataset_text": snapshot.model_dump(mode="json"),
                "context_metadata": self._safe_context_metadata(context.metadata),
            },
        )

        result = await self._repository.save_collection(record)
        logger.info(
            "Dataset collection saved message_id=%s event_id=%s source=%s labels=%s",
            context.message_id,
            result.event_id,
            source.value,
            [label.value for label in item.decision.labels],
        )
        return result

    def _resolve_source(self, item: DatasetCollectionInput) -> DatasetSource:
        metadata_source = item.context.metadata.get("dataset_source") or item.context.metadata.get("source")
        if metadata_source:
            try:
                return DatasetSource(str(metadata_source))
            except ValueError:
                logger.warning("Unknown dataset source ignored source=%s", metadata_source)

        if item.decision.primary_label == ModerationLabel.SAFE and not item.decision.action_required:
            return DatasetSource.REAL_SAFE

        return DatasetSource.REAL_MODERATED

    def _build_features(self, item: DatasetCollectionInput) -> dict[str, Any]:
        context = item.context
        features = context.features.to_dict() if context.features is not None else {}
        features.update(
            {
                "account_age_days": context.account_age_days,
                "member_age_days": context.member_age_days,
                "has_attachments": context.has_attachments,
                "attachment_count": context.attachment_count,
                "url_count": len(context.urls),
                "domain_count": len(context.domains),
                "invite_count": len(context.invites),
            }
        )
        return self._json_safe_mapping(features)

    def _build_training_example(
        self,
        item: DatasetCollectionInput,
        source: DatasetSource,
        model_text: str,
        features: dict[str, Any],
    ) -> TrainingExample:
        feedback = item.feedback
        labels = feedback.labels if feedback and feedback.labels else item.decision.labels
        primary_label = feedback.primary_label if feedback and feedback.primary_label else item.decision.primary_label
        severity = feedback.severity if feedback and feedback.severity is not None else item.decision.severity
        decision_action = (
            feedback.recommended_action
            if feedback and feedback.recommended_action is not None
            else item.decision.decision_action
        )

        return TrainingExample(
            message_id=item.context.message_id,
            model_text=model_text,
            labels=labels,
            primary_label=primary_label,
            severity=severity,
            source=source,
            features=features,
            rule_matches=item.rule_evaluation.matched_rules,
            risk_score=item.rule_evaluation.risk_score,
            decision_action=decision_action,
            feedback_type=feedback.feedback_type if feedback else None,
            policy_version=item.decision.policy_version,
            created_at=item.context.created_at,
            metadata={
                "policy_id": item.decision.policy_id,
                "rule_policy_id": item.rule_evaluation.policy_id,
                "rule_policy_version": item.rule_evaluation.policy_version,
                "action_status": item.action_result.status.value if item.action_result else None,
            },
        )

    def _json_safe_mapping(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {str(key): self._json_safe_mapping(item) for key, item in value.items()}

        if isinstance(value, tuple):
            return [self._json_safe_mapping(item) for item in value]

        if isinstance(value, list):
            return [self._json_safe_mapping(item) for item in value]

        if isinstance(value, datetime):
            return value.isoformat()

        if isinstance(value, (str, int, float, bool)) or value is None:
            return value

        if isinstance(value, ModerationAction):
            return value.value

        if isinstance(value, ModerationLabel):
            return value.value

        return str(value)

    def _safe_context_metadata(self, metadata: dict[str, Any]) -> dict[str, Any]:
        allowed = {"event_type", "feature_version"}
        return {
            key: self._json_safe_mapping(value)
            for key, value in metadata.items()
            if key in allowed
        }

    def _resolve_retention_until(self, context: Any) -> datetime:
        requested = context.metadata.get("retention_until")
        created_at = context.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        maximum = created_at + timedelta(days=self.MAX_RETENTION_DAYS)
        if isinstance(requested, datetime):
            if requested.tzinfo is None:
                requested = requested.replace(tzinfo=timezone.utc)
            return min(requested, maximum)
        return created_at + timedelta(days=self._default_retention_days)
