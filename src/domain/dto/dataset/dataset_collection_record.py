from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from src.domain.dataset.dataset_source import DatasetSource
from src.domain.decision.moderation_decision import ModerationDecision
from src.domain.dto.action.action_execution_plan_result import ActionExecutionPlanResult
from src.domain.dto.dataset.dataset_feedback_label import DatasetFeedbackLabel
from src.domain.dto.dataset.dataset_text_snapshot import DatasetTextSnapshot
from src.domain.dto.dataset.training_example import TrainingExample
from src.domain.rules.rule_evaluation_result import RuleEvaluationResult


class DatasetCollectionRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    platform: str
    guild_id: str
    channel_id: str
    user_id: str
    message_id: str
    event_type: str = "message_create"
    source: DatasetSource
    text: DatasetTextSnapshot
    language: str
    reply_to_message_id: str | None = None
    has_attachments: bool = False
    attachment_count: int = Field(default=0, ge=0)
    features: dict[str, Any] = Field(default_factory=dict)
    rule_evaluation: RuleEvaluationResult
    decision: ModerationDecision
    action_result: ActionExecutionPlanResult | None = None
    feedback: DatasetFeedbackLabel | None = None
    training_example: TrainingExample
    created_at: datetime
    processed_at: datetime
    retention_until: datetime | None = None
    correlation_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
