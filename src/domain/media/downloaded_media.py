from pydantic import BaseModel, ConfigDict, Field


class DownloadedMedia(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    attachment_id: str
    content: bytes
    declared_content_type: str | None = None
    sanitized_host: str = Field(max_length=253)
    download_latency_ms: int = Field(ge=0)
