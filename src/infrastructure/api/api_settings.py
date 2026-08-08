from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ApiSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="MUXIVO_CORE_",
        case_sensitive=False,
        extra="ignore",
    )

    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8000, ge=1, le=65535)
    internal_api_key: str | None = Field(default=None, min_length=16)
    api_docs_enabled: bool = False
    api_max_body_bytes: int = Field(default=65_536, ge=1_024, le=1_048_576)
    api_rate_limit: int = Field(default=120, ge=1, le=10_000)
    api_rate_window_seconds: int = Field(default=60, ge=1, le=3_600)
    api_inference_concurrency: int = Field(default=1, ge=1, le=8)
    api_queue_workers: int = Field(default=2, ge=1, le=8)
    api_queue_size: int = Field(default=500, ge=1, le=10_000)
    api_rubert_enabled: bool = True
    api_rubert_required: bool = True
    api_rubert_model_dir: str = "models/rubert-tiny2-moderation-trained"
    phishing_enabled: bool = False
    phishing_google_safe_browsing_api_key: str | None = Field(default=None, min_length=16)
    phishing_rdap_enabled: bool = False
    phishing_request_timeout_seconds: float = Field(default=2.0, gt=0, le=10)

    media_enabled: bool = False
    media_required: bool = False
    media_max_attachments: int = Field(default=4, ge=1, le=10)
    media_max_file_size_bytes: int = Field(default=10_485_760, ge=1_024, le=104_857_600)
    media_max_total_size_bytes: int = Field(default=20_971_520, ge=1_024, le=209_715_200)
    media_max_width: int = Field(default=8_192, ge=1, le=100_000)
    media_max_height: int = Field(default=8_192, ge=1, le=100_000)
    media_max_pixels: int = Field(default=40_000_000, ge=1, le=100_000_000)
    media_download_timeout_seconds: float = Field(default=10.0, gt=0.0, le=60.0)
    media_max_redirects: int = Field(default=2, ge=0, le=5)
    media_proxy_url: str | None = Field(default=None, max_length=2_048)
    media_allowed_content_types: tuple[str, ...] = ("image/gif", "image/jpeg", "image/png", "image/webp")
    media_allowed_download_hosts: tuple[str, ...] = ("cdn.discordapp.com", "media.discordapp.net")
    media_retention_hours: int = Field(default=24, ge=1, le=720)
    media_hash_cache_ttl: int = Field(default=24, ge=1, le=720)
    media_input_version: str = Field(default="media-v1", min_length=1, max_length=128)
    media_ocr_policy_path: str = "configs/policies/ocr_rules.yaml"
    media_yolo_policy_path: str = "configs/policies/yolo_rules.yaml"
    media_known_scam_hash_registry_path: str | None = Field(default=None, max_length=1_024)
    media_known_scam_phash_distance: int = Field(default=6, ge=0, le=64)

    ocr_enabled: bool = False
    ocr_required: bool = False
    ocr_model_dir: str | None = Field(default=None, max_length=1_024)
    ocr_detection_model_dir: str | None = Field(default=None, max_length=1_024)
    ocr_recognition_model_dir: str | None = Field(default=None, max_length=1_024)
    ocr_model_checksum: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    ocr_cpu_threads: int = Field(default=4, ge=1, le=64)
    ocr_enable_mkldnn: bool = False
    ocr_inference_concurrency: int = Field(default=1, ge=1, le=8)
    ocr_timeout_seconds: float = Field(default=20.0, gt=0.0, le=120.0)
    ocr_max_text_length: int = Field(default=8_000, ge=1, le=32_000)

    yolo_enabled: bool = False
    yolo_required: bool = False
    yolo_model_dir: str | None = Field(default=None, max_length=1_024)
    yolo_device: str = Field(default="cpu", pattern=r"^(cpu|cuda)$")
    yolo_inference_concurrency: int = Field(default=1, ge=1, le=8)
    yolo_timeout_seconds: float = Field(default=20.0, gt=0.0, le=120.0)
    yolo_confidence_threshold: float = Field(default=0.0, ge=0.0, le=1.0)
    yolo_iou_threshold: float = Field(default=1.0, ge=0.0, le=1.0)
    yolo_max_detections: int = Field(default=256, ge=1, le=256)

    @field_validator("media_allowed_content_types")
    @classmethod
    def normalize_content_types(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(dict.fromkeys(value.strip().casefold() for value in values if value.strip()))
        if not normalized or any(not value.startswith("image/") for value in normalized):
            raise ValueError("media_allowed_content_types must contain image MIME types")
        return normalized

    @field_validator("media_allowed_download_hosts")
    @classmethod
    def normalize_download_hosts(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(dict.fromkeys(value.strip().casefold().rstrip(".") for value in values if value.strip()))
        if not normalized or any("/" in value or ":" in value for value in normalized):
            raise ValueError("media_allowed_download_hosts must contain host names")
        return normalized

    @field_validator("media_proxy_url")
    @classmethod
    def validate_media_proxy_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("media_proxy_url must be an HTTP(S) proxy URL")
        return value

    @model_validator(mode="after")
    def validate_media_dependencies(self) -> "ApiSettings":
        if self.media_required and not self.media_enabled:
            raise ValueError("media_required requires media_enabled")
        if self.ocr_required and not self.ocr_enabled:
            raise ValueError("ocr_required requires ocr_enabled")
        if self.yolo_required and not self.yolo_enabled:
            raise ValueError("yolo_required requires yolo_enabled")
        if self.ocr_enabled and not (
            self.ocr_detection_model_dir and self.ocr_recognition_model_dir and self.ocr_model_checksum
        ):
            raise ValueError(
                "ocr_detection_model_dir, ocr_recognition_model_dir and ocr_model_checksum are required when OCR is enabled"
            )
        if self.yolo_enabled and not self.yolo_model_dir:
            raise ValueError("yolo_model_dir is required when YOLO is enabled")
        if self.media_max_total_size_bytes < self.media_max_file_size_bytes:
            raise ValueError("media_max_total_size_bytes must be at least media_max_file_size_bytes")
        return self
