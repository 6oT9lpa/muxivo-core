from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.training.curation.sensitive_topic_matcher import SensitiveTopicMatcher
from src.training.rubert.rubert_training_config import RuBertTrainingConfig

DEFAULT_DATASET_DIR = PROJECT_ROOT / "data" / "exports" / "moderation_dataset_v2"
DEFAULT_TEST_PATH = PROJECT_ROOT / "data" / "exports" / "moderation_dataset" / "test.jsonl"
DEFAULT_TOPIC_CONFIG = PROJECT_ROOT / "configs" / "training" / "sensitive_topic_curation.yaml"
FOCUS_LABELS = ("INVITE", "HATE", "THREAT", "TOXIC", "SAFE")


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as source:
        return [json.loads(line) for line in source if line.strip()]


def _normalized_digest(text: str) -> bytes:
    normalized = " ".join(text.casefold().split())
    return hashlib.sha256(normalized.encode("utf-8")).digest()


def _load_reference_digests(paths: list[Path]) -> set[bytes]:
    digests: set[bytes] = set()
    for path in paths:
        with path.open(encoding="utf-8") as source:
            for line in source:
                if not line.strip():
                    continue
                digests.add(_normalized_digest(json.loads(line)["text"]))
    return digests


def _filter_holdout(
    rows: list[dict[str, Any]],
    reference_digests: set[bytes],
) -> tuple[list[dict[str, Any]], int]:
    filtered = [row for row in rows if _normalized_digest(row["text"]) not in reference_digests]
    return filtered, len(rows) - len(filtered)


def _sigmoid(values: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-values))


def _per_label_metrics(
    labels: np.ndarray,
    predictions: np.ndarray,
    label_names: list[str],
) -> dict[str, dict[str, float | int]]:
    from sklearn.metrics import precision_recall_fscore_support

    precision, recall, f1, support = precision_recall_fscore_support(
        labels,
        predictions,
        average=None,
        zero_division=0,
    )
    result: dict[str, dict[str, float | int]] = {}
    for index, label in enumerate(label_names):
        target = labels[:, index]
        predicted = predictions[:, index]
        false_positives = int(np.logical_and(predicted == 1, target == 0).sum())
        false_negatives = int(np.logical_and(predicted == 0, target == 1).sum())
        negatives = int((target == 0).sum())
        result[label] = {
            "support": int(support[index]),
            "predicted_positives": int(predicted.sum()),
            "precision": float(precision[index]),
            "recall": float(recall[index]),
            "f1": float(f1[index]),
            "false_positives": false_positives,
            "false_negatives": false_negatives,
            "false_positive_rate": float(false_positives / negatives) if negatives else 0.0,
        }
    return result


def _metrics(
    probabilities: np.ndarray,
    labels: np.ndarray,
    thresholds: np.ndarray,
    label_names: list[str],
) -> dict[str, Any]:
    from sklearn.metrics import f1_score, precision_score, recall_score

    predictions = (probabilities >= thresholds).astype(np.int32)
    return {
        "micro_f1": float(f1_score(labels, predictions, average="micro", zero_division=0)),
        "macro_f1": float(f1_score(labels, predictions, average="macro", zero_division=0)),
        "micro_precision": float(precision_score(labels, predictions, average="micro", zero_division=0)),
        "micro_recall": float(recall_score(labels, predictions, average="micro", zero_division=0)),
        "exact_match": float(np.all(predictions == labels, axis=1).mean()),
        "positive_predictions": int(predictions.sum()),
        "positive_targets": int(labels.sum()),
        "per_label": _per_label_metrics(labels, predictions, label_names),
    }


def _topic_slices(
    *,
    rows: list[dict[str, Any]],
    probabilities: np.ndarray,
    labels: np.ndarray,
    thresholds: np.ndarray,
    label_names: list[str],
    matcher: SensitiveTopicMatcher | None,
) -> dict[str, Any]:
    if matcher is None:
        return {}
    topic_indices: dict[str, list[int]] = {"family": [], "race": [], "gender": []}
    for index, row in enumerate(rows):
        for topic in matcher.detect_topics(row["text"]):
            if topic in topic_indices:
                topic_indices[topic].append(index)

    result: dict[str, Any] = {}
    for topic, indices in topic_indices.items():
        if not indices:
            result[topic] = {"rows": 0}
            continue
        index_array = np.asarray(indices, dtype=np.int64)
        metrics = _metrics(
            probabilities[index_array],
            labels[index_array],
            thresholds,
            label_names,
        )
        result[topic] = {
            "rows": len(indices),
            "micro_f1": metrics["micro_f1"],
            "macro_f1": metrics["macro_f1"],
            "exact_match": metrics["exact_match"],
            "focus_labels": {
                label: metrics["per_label"][label]
                for label in FOCUS_LABELS
                if label in metrics["per_label"]
            },
        }
    return result


def _load_thresholds(model_dir: Path, label_names: list[str]) -> np.ndarray:
    path = model_dir / "thresholds.json"
    configured = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    return np.asarray([float(configured.get(label, 0.5)) for label in label_names], dtype=np.float32)


def _model_size_bytes(model_dir: Path) -> int:
    return sum(path.stat().st_size for path in model_dir.rglob("*") if path.is_file())


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = (len(ordered) - 1) * percentile
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = rank - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def _predict_model(
    *,
    model_dir: Path,
    rows: list[dict[str, Any]],
    batch_size: int,
    latency_samples: int,
) -> tuple[np.ndarray, list[str], dict[str, Any]]:
    import torch
    from transformers import AutoConfig, AutoModelForSequenceClassification, AutoTokenizer

    training_config = RuBertTrainingConfig.load()
    model_config = AutoConfig.from_pretrained(model_dir, local_files_only=True)
    tokenizer = AutoTokenizer.from_pretrained(
        model_dir,
        local_files_only=True,
        use_fast=getattr(model_config, "use_fast_tokenizer", True),
    )
    model = AutoModelForSequenceClassification.from_pretrained(
        model_dir,
        local_files_only=True,
        use_safetensors=True,
    )
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model.to(device)
    model.eval()
    if device.type == "cuda":
        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats()

    def predict_texts(texts: list[str]) -> np.ndarray:
        batch = tokenizer(
            texts,
            return_tensors="pt",
            padding=True,
            truncation=True,
            max_length=training_config.model.max_length,
        ).to(device)
        with torch.inference_mode():
            logits = model(**batch).logits
        if device.type == "cuda":
            torch.cuda.synchronize()
        return _sigmoid(logits.detach().cpu().numpy())

    warmup_texts = [row["text"] for row in rows[: min(batch_size, len(rows))]]
    for _ in range(3):
        predict_texts(warmup_texts)

    probabilities: list[np.ndarray] = []
    started = time.perf_counter()
    for start in range(0, len(rows), batch_size):
        texts = [row["text"] for row in rows[start : start + batch_size]]
        probabilities.append(predict_texts(texts))
    inference_seconds = time.perf_counter() - started

    latencies_ms: list[float] = []
    sample_count = min(latency_samples, len(rows))
    if sample_count:
        step = max(1, len(rows) // sample_count)
        for row in rows[::step][:sample_count]:
            started = time.perf_counter()
            predict_texts([row["text"]])
            latencies_ms.append((time.perf_counter() - started) * 1000.0)

    peak_gpu_memory_mb = (
        float(torch.cuda.max_memory_allocated() / (1024 * 1024))
        if device.type == "cuda"
        else 0.0
    )
    label_names = [model.config.id2label[index] for index in range(model.config.num_labels)]
    benchmark = {
        "device": str(device),
        "device_name": torch.cuda.get_device_name(device) if device.type == "cuda" else "CPU",
        "batch_size": batch_size,
        "rows": len(rows),
        "inference_seconds": inference_seconds,
        "rows_per_second": float(len(rows) / inference_seconds),
        "batch_1_latency_samples": len(latencies_ms),
        "batch_1_latency_mean_ms": float(statistics.fmean(latencies_ms)) if latencies_ms else 0.0,
        "batch_1_latency_p50_ms": _percentile(latencies_ms, 0.50),
        "batch_1_latency_p95_ms": _percentile(latencies_ms, 0.95),
        "peak_gpu_memory_mb": peak_gpu_memory_mb,
        "model_directory_bytes": _model_size_bytes(model_dir),
    }
    del model
    if device.type == "cuda":
        torch.cuda.empty_cache()
    return np.vstack(probabilities), label_names, benchmark


def _parse_model(value: str) -> tuple[str, Path]:
    try:
        name, raw_path = value.split("=", maxsplit=1)
    except ValueError as error:
        raise argparse.ArgumentTypeError("Expected NAME=PATH.") from error
    path = Path(raw_path).resolve()
    if not name or not path.is_dir():
        raise argparse.ArgumentTypeError(f"Model directory does not exist: {path}")
    return name, path


def compare(
    *,
    models: list[tuple[str, Path]],
    dataset_dir: Path,
    test_path: Path,
    topic_config: Path | None,
    batch_size: int,
    latency_samples: int,
    filter_overlaps: bool = True,
) -> dict[str, Any]:
    original_rows = _load_jsonl(test_path)
    if filter_overlaps:
        reference_digests = _load_reference_digests(
            [dataset_dir / "train.jsonl", dataset_dir / "validation.jsonl"]
        )
        rows, overlap_count = _filter_holdout(original_rows, reference_digests)
    else:
        rows = original_rows
        overlap_count = 0
    labels = np.asarray([row["labels"] for row in rows], dtype=np.int32)
    matcher = (
        SensitiveTopicMatcher.from_yaml(topic_config)
        if topic_config is not None
        else None
    )

    model_results: dict[str, Any] = {}
    expected_labels: list[str] | None = None
    for name, model_dir in models:
        probabilities, label_names, benchmark = _predict_model(
            model_dir=model_dir,
            rows=rows,
            batch_size=batch_size,
            latency_samples=latency_samples,
        )
        if expected_labels is None:
            expected_labels = label_names
        elif label_names != expected_labels:
            raise ValueError(f"Label order mismatch for {name}: {label_names} != {expected_labels}")
        if labels.shape[1] != len(label_names):
            raise ValueError(
                f"Dataset has {labels.shape[1]} labels, but {name} has {len(label_names)}."
            )

        calibrated_thresholds = _load_thresholds(model_dir, label_names)
        threshold_sets = {
            "fixed_0_5": np.full(len(label_names), 0.5, dtype=np.float32),
            "model_calibrated": calibrated_thresholds,
        }
        quality: dict[str, Any] = {}
        for threshold_name, thresholds in threshold_sets.items():
            quality[threshold_name] = {
                "thresholds": {
                    label: float(threshold)
                    for label, threshold in zip(label_names, thresholds, strict=True)
                },
                "metrics": _metrics(probabilities, labels, thresholds, label_names),
                "topic_slices": _topic_slices(
                    rows=rows,
                    probabilities=probabilities,
                    labels=labels,
                    thresholds=thresholds,
                    label_names=label_names,
                    matcher=matcher,
                ),
            }
        model_results[name] = {
            "model_dir": str(model_dir),
            "benchmark": benchmark,
            "quality": quality,
        }

    return {
        "test_path": str(test_path.resolve()),
        "reference_dataset_dir": str(dataset_dir.resolve()),
        "cross_split_overlap_filter_applied": filter_overlaps,
        "original_test_rows": len(original_rows),
        "excluded_cross_split_overlaps": overlap_count,
        "holdout_rows": len(rows),
        "models": model_results,
    }


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        description="Compare trained ruBERT moderation models on a decontaminated holdout."
    )
    parser.add_argument(
        "--model",
        action="append",
        required=True,
        type=_parse_model,
        help="Model in NAME=PATH form. Repeat for every model.",
    )
    parser.add_argument("--dataset-dir", type=Path, default=DEFAULT_DATASET_DIR)
    parser.add_argument("--test-path", type=Path, default=DEFAULT_TEST_PATH)
    parser.add_argument(
        "--topic-config",
        type=Path,
        default=None,
        help="Optional sensitive-topic policy for topic slices. Omit for a plain split evaluation.",
    )
    parser.add_argument("--batch-size", type=int, default=128)
    parser.add_argument("--latency-samples", type=int, default=200)
    parser.add_argument(
        "--skip-overlap-filter",
        action="store_true",
        help="Use only when --test-path is already decontaminated.",
    )
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    result = compare(
        models=args.model,
        dataset_dir=args.dataset_dir.resolve(),
        test_path=args.test_path.resolve(),
        topic_config=args.topic_config.resolve() if args.topic_config else None,
        batch_size=args.batch_size,
        latency_samples=args.latency_samples,
        filter_overlaps=not args.skip_overlap_filter,
    )
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
