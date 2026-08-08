from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from src.contracts.api.media_attachment_request_schema import MediaAttachmentRequestSchema
from src.contracts.api.media_moderation_request_schema import MediaModerationRequestSchema
from src.contracts.api.moderation_message_request_schema import ModerationMessageRequestSchema


def _message(**changes) -> ModerationMessageRequestSchema:
    payload = {
        "guild_id": "1",
        "channel_id": "2",
        "user_id": "3",
        "message_id": "4",
        "raw_text": "hello",
        "created_at": datetime.now(timezone.utc),
        "has_attachments": True,
        "attachment_count": 1,
    }
    payload.update(changes)
    return ModerationMessageRequestSchema(**payload)


def _attachment(attachment_id: str = "5") -> MediaAttachmentRequestSchema:
    return MediaAttachmentRequestSchema(
        attachment_id=attachment_id,
        download_url="https://cdn.discordapp.com/attachments/1/2/image.png",
        file_name="image.png",
        content_type="image/png",
        file_size=128,
        width=8,
        height=8,
    )


def test_media_contract_accepts_strict_synchronized_payload() -> None:
    request = MediaModerationRequestSchema(message=_message(), attachments=(_attachment(),))
    assert request.attachments[0].to_domain().download_url is not None


def test_media_contract_accepts_missing_optional_discord_content_type() -> None:
    attachment = MediaAttachmentRequestSchema(
        attachment_id="5",
        download_url="https://cdn.discordapp.com/attachments/1/2/image.png",
        file_size=128,
    )
    assert attachment.content_type is None


@pytest.mark.parametrize(
    "changes",
    (
        {"download_url": None, "media_reference": None},
        {"download_url": "https://cdn.discordapp.com/a.png", "media_reference": "opaque"},
        {"download_url": "http://cdn.discordapp.com/a.png"},
        {"unexpected": "value"},
    ),
)
def test_media_attachment_requires_one_https_source_and_forbids_extra(changes: dict) -> None:
    payload = {
        "attachment_id": "5",
        "download_url": "https://cdn.discordapp.com/a.png",
        "content_type": "image/png",
        "file_size": 10,
    }
    payload.update(changes)
    with pytest.raises(ValidationError):
        MediaAttachmentRequestSchema(**payload)


def test_media_contract_rejects_duplicate_attachment_ids() -> None:
    with pytest.raises(ValidationError):
        MediaModerationRequestSchema(
            message=_message(attachment_count=2),
            attachments=(_attachment(), _attachment()),
        )


def test_media_contract_rejects_attachment_count_mismatch() -> None:
    with pytest.raises(ValidationError):
        MediaModerationRequestSchema(
            message=_message(attachment_count=2),
            attachments=(_attachment(),),
        )
