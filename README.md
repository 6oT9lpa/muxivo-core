# Muxivo Core

![Muxivo logo](./docs/images/muxivo-logo.png)

Muxivo Core is a local moderation engine and HTTP API for community platforms.
It is currently used by Muxivo Discord to analyze selected Discord channels through a
self-hosted API.

The project is platform-independent at the core: Discord, Telegram, dashboards,
and future adapters should send normalized requests to the API or application
services instead of coupling directly to the moderation pipeline.

## Current Status

Ready components:

- FastAPI moderation API;
- PostgreSQL-backed policy repository with YAML fallback;
- preprocessing rules for URLs, invites, spam, flood, evasion, semantic hate,
  and NSFW signals;
- ruBERT tiny2 moderation classifier loading from
  `models/rubert-tiny2-moderation-trained`;
- CUDA inference when NVIDIA drivers and CUDA-ready PyTorch are available;
- moderation rule engine with risk scoring and conflict handling;
- decision engine with action bundles;
- action policy and dry-run capable action executor;
- health endpoint that reports database, policy, and ruBERT readiness;
- a separate, disabled-by-default media endpoint with SSRF-safe Discord CDN
  downloading, decoded-image validation, exact/perceptual hashes, OCR integration,
  and a versioned image-detector port;
- deployment scripts for `/opt/muxivo-core`;
- training, evaluation, and model utility scripts;
- load-testing module and local testing script.

The API can run without a GPU, but GPU inference is preferred when available.
The production server currently loads the trained ruBERT model on CUDA when the
NVIDIA driver is installed and PyTorch reports `cuda_available=True`.

## Training Results

The current moderation dataset contains 1,049,399 examples with a
`735,000 / 157,202 / 157,197` train, validation, and test split. The ruBERT
model was trained on the 735,000-example training split. The selected checkpoint
is epoch 4 (`checkpoint-183752`) with validation micro-F1 `0.9298`, macro-F1
`0.8381`, and exact match `0.8610`. The final held-out test result is micro-F1
`0.9358` and macro-F1 `0.8396`.

![Training overview](./docs/images/training/training_overview.png)

The loss curve shows the training trajectory and the best validation-loss point.
The final training epoch is retained for comparison, while the best checkpoint
is selected separately from the final checkpoint.

![Training and validation loss](./docs/images/training/loss_by_step.png)

Validation metrics make it possible to compare micro-F1, macro-F1, and exact
match across epochs and identify the checkpoint selected for deployment.

![Validation metrics by epoch](./docs/images/training/validation_metrics_by_epoch.png)

The learning-rate chart documents warmup and decay, which is useful when
comparing runs with different training schedules.

![Learning rate by step](./docs/images/training/learning_rate_by_step.png)

Per-label precision, recall, and F1 expose uneven model quality that aggregate
metrics can hide. `TOXIC` has the lowest recall among labels with meaningful
held-out support (`0.7816`) and remains a priority for additional hard examples.
`FLOOD` has only one held-out example, so it must remain primarily
rule-engine driven until representative test coverage is added.

![Test quality by moderation label](./docs/images/training/test_per_label_metrics.png)

Target-versus-predicted positive counts make threshold and calibration bias
visible for each label.

![Test target versus predicted label balance](./docs/images/training/test_prediction_balance.png)

The training corpus is intentionally multi-label, so label totals can exceed the
number of rows. The chart below is useful for identifying underrepresented
classes before the next training run.

![Training dataset label distribution](./docs/images/training/label_distribution.png)

The source distribution chart documents how public, synthetic, and moderated
project examples contribute to the assembled dataset.

![Training dataset source distribution](./docs/images/training/source_distribution.png)

Regenerate the local report after a training run:

```powershell
.\.venv\Scripts\python.exe scripts\training\build_rubert_training_report.py
```

Use `--include-test-evaluation` to rerun inference over the test split and
refresh per-label metrics.

## Training Workflow

```mermaid
flowchart LR
    A[Public and project data] --> B[Sanitization and relabeling]
    B --> C[Dataset split]
    C --> D[ruBERT training]
    D --> E[Validation threshold calibration]
    E --> F[Test and hard-pack evaluation]
    F --> G[Training report and charts]
    G --> H[Local moderation model]
```

## Architecture

```mermaid
flowchart LR
    A[Discord or platform adapter] --> B[Local FastAPI API]
    B --> C[Text preprocessing]
    B --> M[Media ingest and validation]
    M --> N[OCR provider]
    M --> O[Image detector provider]
    N --> F
    O --> F
    C --> D[Preprocessing rules]
    C --> E[ruBERT classifier]
    D --> F[Moderation rule engine]
    E --> F
    F --> G[Risk score]
    G --> H[Decision engine]
    H --> I[Action policy and executor]
    H --> J[Dataset collector]
    I --> K[API response]
    J --> L[PostgreSQL training feedback]
```

Core principles:

- one class per file where practical;
- platform adapters stay thin;
- rules and policies are data-driven;
- destructive actions are policy-gated;
- model output is explainable through labels, scores, reasons, and evidence;
- logs are written for important service, policy, model, and request events;
- the engine assists moderators and does not replace human governance.

## API

Canonical local/production service (also selects a psycopg-compatible event loop on Windows):

```text
python main_api.py
```

Important endpoints:

- `GET /health` - database, policy, and model readiness;
- `POST /moderation/messages` - analyze a platform message;
- `POST /moderation/media` - analyze one message and its image attachments in
  one shared moderation decision;
- `POST /moderation/feedback` - persist idempotent moderator feedback linked by
  event ID or guild-scoped message ID;
- `POST /actions/result` - persist terminal platform execution results;
- `GET /api/policies/effective` - inspect effective policies.

The API should be protected by an internal API key and network boundary. In the
Muxivo Discord deployment it listens on localhost and is called by the Discord bot
backend.

Database schema changes are owned exclusively by Alembic and applied during
startup; runtime repositories never issue DDL. Request correlation IDs are
persisted across the moderation event, action result, and feedback lineage.
The action contract includes `KICK`; unknown action values are rejected by the
API contract rather than mapped to a stronger fallback action.

The optional known-scam registry is a JSON array of objects containing a
validated `sha256`, `phash`, or both. Exact SHA-256 matches and pHash matches
within `MUXIVO_CORE_MEDIA_KNOWN_SCAM_PHASH_DISTANCE` produce an explicit SCAM
signal. Promotion from shadow mode can be gated with
`scripts/evaluation/check_shadow_acceptance.py`.

Production startup and inference do not read
`configs/training/sensitive_topic_curation.yaml`; curation tests use explicit
in-memory policies instead of restoring that retired training fixture.

Media request example:

```json
{
  "message": {
    "platform": "discord",
    "message_id": "1234567890",
    "guild_id": "100",
    "channel_id": "200",
    "user_id": "300",
    "timestamp": "2026-07-31T12:00:00Z",
    "raw_text": "message caption",
    "has_attachments": true,
    "attachment_count": 1
  },
  "attachments": [
    {
      "attachment_id": "400",
      "download_url": "https://cdn.discordapp.com/attachments/example/image.png",
      "file_name": "image.png",
      "content_type": "image/png",
      "file_size": 12345,
      "width": 640,
      "height": 480
    }
  ]
}
```

The response extends the regular moderation response with attachment status,
detected MIME, hashes, language/confidence, labels, warnings, stage latency, and
safe model identifiers. It never returns full OCR text.

## Configuration

Example:

```env
DATABASE_URL=postgresql://ai_moder:change_me@127.0.0.1:5432/ai_moder
API_HOST=127.0.0.1
API_PORT=8000
API_KEY=change_me
API_RUBERT_REQUIRED=true
API_RUBERT_MODEL_DIR=models/rubert-tiny2-moderation-trained
LOG_LEVEL=INFO
```

Media moderation is off by default. Its complete settings are documented in
`.env.example`; the important switches are `MUXIVO_CORE_MEDIA_ENABLED`,
`MUXIVO_CORE_MEDIA_REQUIRED`, the per-file/request/dimension limits, the exact
Discord CDN host allowlist, retention, cache TTL, and separately validated OCR
and YOLO settings.

Downloads accept HTTPS from configured hosts only. Every initial and redirected
host is resolved and rejected if any destination is non-public; system proxies
are disabled. Responses are streamed under a hard byte limit and then decoded
as JPEG, PNG, or static WebP. Declared metadata is advisory: decoded MIME,
dimensions, pixel count, animation state, and decompression-bomb limits are
checked independently. Image bytes and full temporary URLs are not logged.

The optional PaddleOCR adapter is installed separately from
`requirements-media.txt`, loads once per process, and uses its own concurrency
and timeout limits. OCR output is normalized, sensitive values are redacted
before persistence, and the unredacted value is used only transiently for the
existing semantic classifier. The image-detector/YOLO port and policy mapping
are present, but no detector implementation or model is shipped; enabling it
therefore reports unavailable instead of returning synthetic detections.

Media metadata and versioned analysis results are stored in PostgreSQL. Original
image bytes are not persisted. Redacted OCR fields use
`MUXIVO_CORE_MEDIA_RETENTION_HOURS`; deployments should run their normal
retention cleanup against `retention_until`. Cached results are considered
compatible only when model name/version, input pipeline version, and policy
version all match.

`GET /health` reports `media`, `ocr`, and `image` independently as `disabled`,
`ready`, or `unavailable`. A required component affects the overall `degraded`
status; an optional unavailable provider produces an explicit request warning.

Apply the PostgreSQL schema before starting an updated service:

```powershell
.\.venv\Scripts\alembic.exe upgrade head
```

The revision is additive, idempotently fills version fields for existing rows,
and adds unique keys used by repeat message/attachment requests. SQLite is not
a supported runtime or migration target.

Model artifacts are intentionally not packed into release archives by default.
Deploy the trained model separately into:

```text
/opt/muxivo-core/models/rubert-tiny2-moderation-trained
```

## Deployment

Build a release archive without secrets, logs, virtual environments, runtime
data, and model artifacts:

```powershell
scripts/deploy/build_muxivo_core_release.ps1
```

Upload and deploy to the local server:

```powershell
scripts/deploy/deploy_muxivo_core_local.ps1 `
  -SshPassword $env:MUXIVO_CORE_SSH_PASSWORD `
  -RootPassword $env:MUXIVO_CORE_ROOT_PASSWORD
```

Production directory:

```text
/opt/muxivo-core
```

Systemd service:

```bash
sudo systemctl enable muxivo-core.service
sudo systemctl restart muxivo-core.service
sudo systemctl status muxivo-core.service
```

GPU check:

```bash
nvidia-smi
/opt/muxivo-core/.venv/bin/python - <<'PY'
import torch
print(torch.cuda.is_available())
print(torch.cuda.device_count())
print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else "cpu")
PY
```

## Testing

```bash
python -m pytest
```

Targeted examples:

```bash
python -m pytest tests/modules/preprocessing tests/presentation/api
python -m pytest tests/application tests/contracts/api tests/infrastructure/media
python scripts/testing/run_moderation_load_test.py --base-url http://127.0.0.1:8000
```

## Data And Privacy

Muxivo Core may process message text, platform IDs, policy metadata, labels,
risk scores, confidence values, and technical logs. See:

- [Privacy Policy](./docs/PRIVACY_POLICY.md)
- [Terms of Service / Acceptable Use](./docs/TERMS_OF_SERVICE.md)

## License

This project is proprietary commercial software. It is not open source.

No production use, commercial use, copying, modification, redistribution,
hosting, resale, white-label use, or SaaS use is granted unless a separate
written commercial license or contract explicitly allows it.

See:

- [LICENSE](./LICENSE)
- [Commercial License Terms](./COMMERCIAL_LICENSE.md)
