# Media moderation acceptance report — 2026-07-31

## Verified locally

- Muxivo Core full suite: `1666 passed, 1 skipped` (`python -m pytest -q`). The skipped test is the
  explicitly gated disposable-PostgreSQL integration test.
- Muxivo Discord full Python suite: `216 passed, 41 skipped` with coverage disabled because the existing
  `.coverage` file was locked by another process.
- Activity: `16 passed` (`npm test -- --run`) and a successful `tsc && vite build`.
- ONNX Runtime `1.28.0` installed on Python 3.14; `pip check` reported no broken requirements.
- CPU runtime providers: `AzureExecutionProvider`, `CPUExecutionProvider`.
- Local GPU: NVIDIA GeForce RTX 4060 Laptop GPU, 8 GB. This is not the production GTX 1650.
- A disposable PostgreSQL 17 cluster accepted a clean Alembic upgrade through
  `0002_media_policy_snapshots`. The real repository lifecycle passed: SAVE revision 1, stale-write
  conflict, SAVE revision 2, RESET revision 3, and the exact three audit records. The runner stopped
  and removed the cluster afterward.
- Runtime searches found no `IMAGE_SCAM` in Muxivo Discord. Its only Muxivo Core occurrences are negative
  tests that verify the value is rejected.
- A real PaddleOCR CPU smoke had previously completed with three Russian/English/URL lines,
  mean confidence `0.9693473`, and `1507 ms` latency using the verified local model bundle.
- The pinned MIT YOLO checkout completed a real one-epoch CUDA/AMP smoke on its official five-image
  mock train/validation fixture with YOLO v9-s (`9.8M` trainable parameters) and an RTX 4060 Laptop
  GPU. This required Python `3.13.14` and `lightning==2.5.6`; Python 3.14 and Lightning 2.6 were
  incompatible with that pinned upstream CLI/progress-bar implementation.
- The smoke checkpoint (`80,140,918` bytes) exported through the repository adapter to a
  `29,112,771` byte ONNX graph. ONNX Runtime verified one dynamic-batch input and a finite
  `(1, 2100, 84)` `xywh + class probabilities` output.
- The checksum-packaged smoke graph loaded through the exact production provider. Five CPU runs on
  the upstream demo image measured mean `4407.204 ms`, p95 `4566.939 ms`, and `0.227 images/s`.
  These values prove execution only; a one-epoch mock model is neither an accuracy candidate nor a
  production performance result.

## Implemented commits

Muxivo Core:

- `703bf09` strict OCR and detector YAML defaults.
- `72f7a46`, `ee8b845` verified PaddleOCR v3 CPU runtime and Windows hardening.
- `965c1b1` versioned PostgreSQL media snapshots, audit, revision conflict and YAML fallback.
- `76dc2cd` verified MIT YOLO ONNX runtime and effective guild policy wiring.
- `54fa67a` pinned MIT training/export workflow.
- `2f7b49a` production-provider benchmark runner.
- `3c892f9` OCR policy filtering followed by shared preprocessing and Tiny2.
- `da4a0f7` Alembic psycopg 3 URL handling.
- Verified CUDA training wrapper and runtime-compatible ONNX export adapter.

Muxivo Discord:

- `161e3e9` Activity/backend media-policy save, reload, reset, conflict and unavailable flow.

## External acceptance still required

These checks were not represented as successful:

- Production training cannot start because no moderation object-detection images, annotations or
  dataset YAML are present. Only the upstream mock fixture was used.
- Production ONNX/TensorRT accuracy and latency cannot be measured because no trained moderation
  detector artifact exists. The mock ONNX execution evidence above must not be used as model quality.
- GTX 1650 FP16/INT8 benchmarking requires that physical production GPU and an engine built on it.
- The real Muxivo DS Activity scenarios A–E require a deployed test environment, Discord session,
  test guild and explicit deployment authority. No deployment, push or remote-server mutation was
  performed.

When those prerequisites exist, use `scripts/training/train_mit_yolo.py`, export the selected
checkpoint with `scripts/training/export_mit_yolo_onnx.py`, package it with
`scripts/training/package_yolo_onnx.py`, and retain the JSON result produced by
`python -m scripts.media.benchmark_onnx_yolo` with the release artifact.

The PostgreSQL lifecycle can be reproduced without using `.env` credentials:

```powershell
python -m scripts.testing.run_media_policy_postgresql_acceptance
```
