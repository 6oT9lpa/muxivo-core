# Muxivo Core

Muxivo Core is a self-hosted moderation engine and FastAPI service for community platforms. Muxivo Discord is its current Discord adapter: it sends a bounded, normalized message request and remains the final enforcement boundary.

The core is platform-independent. Discord, Telegram, web applications, and future adapters integrate through the HTTP contract rather than coupling to rules, models, or persistence internals.

## Production release

The production text-classification release is **ruBERT Tiny2 Moderation — 30 July 2026** (`rubert-tiny2-trained-20260730`). It was evaluated with calibrated thresholds on a leakage-filtered holdout of **75,457** examples.

| Metric | Production ruBERT Tiny2 |
| --- | ---: |
| Micro-F1 | **0.9736** |
| Macro-F1 | **0.9246** |
| Exact match | **0.9605** |
| GTX 1650 p95, batch=1 | **2.14 ms** |
| GTX 1650 throughput | **1,671 msg/s** |
| GTX 1650 peak GPU memory | **497 MB** |

The quality figures use the calibrated release thresholds. The release report excludes 126 cross-split overlaps from the original test set; it is not a claim of performance on arbitrary live Discord traffic. The detailed evidence is versioned in [`models/rubert_models_comparison_gtx1650_20260730.json`](./models/rubert_models_comparison_gtx1650_20260730.json).

The model bundle is deployed separately and is intentionally not committed to this repository. A production host must provide the verified directory referenced by `MUXIVO_CORE_API_RUBERT_MODEL_DIR`; otherwise, a required ruBERT component is unavailable and `/health` is degraded.

## Current capabilities

- FastAPI API with request-size limits, an internal API key, local rate limiting, correlation IDs, and readiness reporting;
- PostgreSQL-backed, guild-scoped policy resolution with YAML defaults;
- text normalization and deterministic signals for URLs, Discord invites, spam, flooding, evasion, Russian profanity, semantic hate, and NSFW-related content;
- local calibrated ruBERT Tiny2 inference from the 30 July 2026 production release;
- phishing enrichment through optional RDAP and Google Safe Browsing providers;
- risk aggregation, conflict resolution, explainable labels/reasons, and a policy-resolved action proposal;
- idempotent moderation-event, action-result, and moderator-feedback lineage;
- optional media analysis: SSRF-safe Discord CDN downloads, decoded-image validation, hashes, OCR, known-scam hash matching, and an ONNX YOLO provider;
- Alembic-owned PostgreSQL migrations, deployment utilities, training/curation tools, and load-test modules.

## Release evidence and graphics

All graphics below are generated from the 30 July model-selection artefacts. They replace the historical training curves that described a pre-production run and must not be used as production evidence.

![Production model profile](./docs/images/production/tiny2_production_profile_20260730.png)

![Production model quality by label](./docs/images/production/tiny2_per_label_quality_20260730.png)

![Production threshold calibration](./docs/images/production/tiny2_threshold_calibration_20260730.png)

![Production thresholds](./docs/images/production/tiny2_thresholds_20260730.png)

![Sensitive-topic quality slices](./docs/images/production/tiny2_topic_slices_20260730.png)

![Label support in the evaluated holdout](./docs/images/production/tiny2_label_support_20260730.png)

![Prediction balance in the evaluated holdout](./docs/images/production/tiny2_prediction_balance_20260730.png)

Regenerate these figures from the checked-in JSON evidence:

```powershell
.\.venv\Scripts\python.exe scripts\training\build_production_model_report.py
```

## Runtime architecture

```mermaid
flowchart LR
    A["Muxivo Discord or another adapter"] --> B["FastAPI boundary"]
    B --> C["Validation, key check, rate limit and queue"]
    C --> D["Text preprocessing"]
    D --> E["Rules and phishing signals"]
    D --> F["ruBERT Tiny2 2026-07-30"]
    E --> G["Signal normalization and conflict resolution"]
    F --> G
    G --> H["Risk score and decision engine"]
    H --> I["Policy-resolved action proposal"]
    H --> J["PostgreSQL event, feedback and audit lineage"]
    B --> K["Optional media pipeline"]
    K --> L["SSRF-safe download and image validation"]
    L --> M["OCR and ONNX YOLO providers"]
    M --> G
    I --> N["API response"]
```

The API returns a recommendation and an action plan; it does **not** call Discord or punish a member. Muxivo Discord applies its own guild policy and enforcement mode after receiving the result.

See the full component and trust-boundary description in [Architecture](./docs/ARCHITECTURE.md).

## API

Start the canonical local or production service:

```bash
python main_api.py
```

Important endpoints:

- `GET /health` — database, policy, model, media, OCR, and image-provider readiness;
- `POST /moderation/messages` — analyse one normalized platform message;
- `POST /moderation/media` — analyse one message and supported image attachments as one decision;
- `POST /moderation/feedback` — persist idempotent moderator feedback;
- `POST /actions/result` — persist a terminal action-execution result;
- `GET /api/policies/effective` — inspect the resolved effective policy.

The service is designed for an internal network boundary. Muxivo Discord normally calls it on localhost; public exposure without network controls and an internal API key is unsupported.

Media downloads accept HTTPS only from configured hosts. Initial and redirect destinations are checked for public routability, proxies are disabled, responses are streamed under a byte limit, and decoded JPEG/PNG/static WebP properties are validated independently of declared metadata. Original image bytes and full temporary URLs are not persisted or logged.

## Configuration

Copy [`.env.example`](./.env.example) and set at least the database, internal key, and production model path:

```env
DATABASE_URL=postgresql://ai_moder:change_me@127.0.0.1:5432/ai_moder
MUXIVO_CORE_INTERNAL_API_KEY=change_me
MUXIVO_CORE_API_HOST=127.0.0.1
MUXIVO_CORE_API_PORT=8000
MUXIVO_CORE_API_RUBERT_ENABLED=true
MUXIVO_CORE_API_RUBERT_REQUIRED=true
MUXIVO_CORE_API_RUBERT_MODEL_DIR=/opt/muxivo-core/models/rubert-tiny2-trained-20260730
```

Media processing is disabled by default. Enable it only after configuring explicit file, request, dimensions, retention, host allowlist, OCR, and YOLO settings in `.env.example`. Required but unavailable optional components deliberately degrade health instead of silently fabricating output.

Apply schema changes through Alembic before starting a new release:

```powershell
.\.venv\Scripts\alembic.exe upgrade head
```

Runtime repositories never issue DDL. PostgreSQL is the supported runtime and migration target; SQLite is only legacy-import territory.

## Deployment

Create a release archive without secrets, logs, environments, runtime data, or model weights:

```powershell
scripts/deploy/build_muxivo_core_release.ps1
```

Deploy the application and install the separately verified model bundle under the configured directory, for example:

```text
/opt/muxivo-core/models/rubert-tiny2-trained-20260730
```

Then enable and inspect the service:

```bash
sudo systemctl enable muxivo-core.service
sudo systemctl restart muxivo-core.service
sudo systemctl status muxivo-core.service
curl -fsS http://127.0.0.1:8000/health
```

## Testing

```bash
python -m pytest
python -m pytest tests/modules/preprocessing tests/presentation/api
python -m pytest tests/application tests/contracts/api tests/infrastructure/media
python scripts/testing/run_moderation_load_test.py --base-url http://127.0.0.1:8000
```

## Documentation, privacy and licence

- [Architecture](./docs/ARCHITECTURE.md)
- [Sensitive-topic data sources](./docs/SENSITIVE_TOPIC_DATA_SOURCES.md)
- [Media moderation acceptance report](./docs/reports/media_moderation_acceptance_2026-07-31.md)
- [Privacy Policy](./docs/PRIVACY_POLICY.md)
- [Terms of Service / Acceptable Use](./docs/TERMS_OF_SERVICE.md)

This is proprietary commercial software, not open-source software. No production use, copying, modification, redistribution, hosting, resale, white-label use, or SaaS use is granted without a separate written licence or contract. See [LICENSE](./LICENSE) and [Commercial License Terms](./COMMERCIAL_LICENSE.md).
