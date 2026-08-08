import pytest

from src.application.api_moderation_service import ApiModerationService
from src.domain.moderation.moderation_action import ModerationAction


@pytest.mark.parametrize(
    "decision_action",
    (ModerationAction.TIMEOUT, ModerationAction.KICK, ModerationAction.BAN),
)
def test_member_restriction_decisions_accept_safe_warning_fallback(
    decision_action: ModerationAction,
) -> None:
    service = object.__new__(ApiModerationService)

    assert ModerationAction.WARN in service._allowed_execution_actions(decision_action)


def test_delete_warn_records_its_terminal_warning_action() -> None:
    service = object.__new__(ApiModerationService)

    assert service._allowed_execution_actions(ModerationAction.DELETE_WARN) == (
        ModerationAction.WARN,
    )
