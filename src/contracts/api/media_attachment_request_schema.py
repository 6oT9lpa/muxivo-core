from pydantic import Field, HttpUrl, model_validator

from src.contracts.api.api_model import ApiModel
from src.domain.media.media_attachment import MediaAttachment


class MediaAttachmentRequestSchema(ApiModel):
    attachment_id: str = Field(min_length=1, max_length=128, pattern=r"^[0-9A-Za-z_.:-]+$")
    download_url: HttpUrl | None = Field(default=None, max_length=2_048)
    media_reference: str | None = Field(default=None, min_length=1, max_length=512)
    file_name: str | None = Field(default=None, max_length=255)
    # Discord documents this field as optional. The downloaded bytes decide the
    # actual format; this value is retained as an untrusted transport hint.
    content_type: str | None = Field(default=None, max_length=127, pattern=r"^[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+$")
    file_size: int = Field(gt=0, le=104_857_600)
    width: int | None = Field(default=None, gt=0, le=100_000)
    height: int | None = Field(default=None, gt=0, le=100_000)

    @model_validator(mode="after")
    def validate_source(self) -> "MediaAttachmentRequestSchema":
        if (self.download_url is None) == (self.media_reference is None):
            raise ValueError("exactly one of download_url or media_reference is required")
        if self.download_url is not None and self.download_url.scheme != "https":
            raise ValueError("download_url must use HTTPS")
        return self

    def to_domain(self) -> MediaAttachment:
        return MediaAttachment(
            attachment_id=self.attachment_id,
            download_url=str(self.download_url) if self.download_url is not None else None,
            media_reference=self.media_reference,
            file_name=self.file_name,
            content_type=self.content_type.casefold() if self.content_type else None,
            file_size=self.file_size,
            width=self.width,
            height=self.height,
        )
