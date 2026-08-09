"""Build README graphics from the immutable 2026-07-30 model-selection reports."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt


ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / "docs" / "reports" / "rubert_tiny2_validation_20260809.json"
OUTPUT = ROOT / "docs" / "images" / "validation"
PRODUCTION_MODEL = "tiny2"


def load_report(path: Path) -> dict:
    with path.open(encoding="utf-8") as file:
        return json.load(file)


def save(figure: plt.Figure, name: str, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    figure.tight_layout(rect=(0, 0, 1, 0.94))
    figure.savefig(output / name, dpi=180, bbox_inches="tight")
    plt.close(figure)


def production_profile(model: dict, rows: int, output: Path) -> None:
    metrics = model["quality"]["model_calibrated"]["metrics"]
    figure, axis = plt.subplots(figsize=(7.6, 4.2))
    quality = axis.bar(
        ["Micro-F1", "Macro-F1", "Exact match"],
        [metrics["micro_f1"], metrics["macro_f1"], metrics["exact_match"]],
        color=("#4c78a8", "#54a24b", "#f58518"),
    )
    axis.bar_label(quality, fmt="%.4f", padding=3)
    axis.set_ylim(0, 1.08)
    axis.set_ylabel("Score")
    axis.set_title(f"ruBERT Tiny2 calibrated validation quality ({rows:,} rows)")
    save(figure, "tiny2_validation_quality_20260809.png", output)


def speed_profile(model: dict, rows: int, output: Path) -> None:
    benchmark = model["benchmark"]
    figure, axes = plt.subplots(1, 2, figsize=(10.2, 4.2), gridspec_kw={"width_ratios": (1.1, 1)})
    latency = axes[0].bar(
        ["p50 latency", "p95 latency"],
        [benchmark["batch_1_latency_p50_ms"], benchmark["batch_1_latency_p95_ms"]],
        color=("#4c78a8", "#f58518"),
    )
    axes[0].bar_label(latency, labels=[f"{value:.2f} ms" for value in (benchmark["batch_1_latency_p50_ms"], benchmark["batch_1_latency_p95_ms"])], padding=3)
    axes[0].set_ylim(0, max(10, benchmark["batch_1_latency_p95_ms"] * 1.3))
    axes[0].set_ylabel("Milliseconds")
    axes[0].set_title("Single-message latency")
    axes[1].axis("off")
    axes[1].text(0, 0.78, "Validation inference profile", fontsize=13, fontweight="bold")
    axes[1].text(0, 0.52, f"Throughput: {benchmark['rows_per_second']:,.0f} msg/s\nBatch size: {benchmark['batch_size']}\nRows evaluated: {rows:,}\nDevice: {benchmark['device_name']}", fontsize=11, va="top")
    axes[1].text(0, 0.08, f"Model bundle: {benchmark['model_directory_bytes'] / (1024 * 1024):,.0f} MiB", fontsize=10, color="#4d4d4d")
    save(figure, "tiny2_validation_speed_20260809.png", output)


def per_label(model: dict, output: Path) -> None:
    labels = model["quality"]["model_calibrated"]["metrics"]["per_label"]
    names = list(labels)
    figure, axis = plt.subplots(figsize=(11, 5.4))
    width = 0.25
    for offset, metric, color in ((-width, "precision", "#4c78a8"), (0, "recall", "#54a24b"), (width, "f1", "#f58518")):
        axis.bar([index + offset for index in range(len(names))], [labels[name][metric] for name in names], width, label=metric.title(), color=color)
    axis.set_xticks(range(len(names)), names, rotation=40, ha="right")
    axis.set_ylim(0, 1.05)
    axis.set_ylabel("Score")
    axis.set_title("ruBERT Tiny2 calibrated validation quality by label")
    axis.legend()
    save(figure, "tiny2_validation_per_label_quality_20260809.png", output)


def calibration(model: dict, output: Path) -> None:
    quality = model["quality"]
    fixed, calibrated = (quality[key]["metrics"] for key in ("fixed_0_5", "model_calibrated"))
    names = ["Micro-F1", "Macro-F1", "Exact match"]
    keys = ("micro_f1", "macro_f1", "exact_match")
    figure, axis = plt.subplots(figsize=(7.6, 4.4))
    for offset, entry, label, color in ((-0.18, fixed, "Fixed threshold 0.50", "#9ecae9"), (0.18, calibrated, "Calibrated thresholds", "#f58518")):
        bars = axis.bar([index + offset for index in range(3)], [entry[key] for key in keys], 0.36, label=label, color=color)
        axis.bar_label(bars, fmt="%.3f", padding=2, fontsize=8)
    axis.set_xticks(range(3), names)
    axis.set_ylim(0, 1.08)
    axis.set_title("Calibration effect: ruBERT Tiny2, 30 July 2026")
    axis.legend()
    save(figure, "tiny2_validation_threshold_calibration_20260809.png", output)


def thresholds(model: dict, output: Path) -> None:
    values = model["quality"]["model_calibrated"]["thresholds"]
    figure, axis = plt.subplots(figsize=(10.5, 4.6))
    bars = axis.bar(list(values), list(values.values()), color="#4c78a8")
    axis.bar_label(bars, fmt="%.2f", padding=2, fontsize=8)
    axis.set_ylim(0, 1.0)
    axis.set_ylabel("Decision threshold")
    axis.set_title("ruBERT Tiny2 calibrated per-label thresholds")
    axis.tick_params(axis="x", rotation=40)
    save(figure, "tiny2_validation_thresholds_20260809.png", output)


def topic_slices(model: dict, output: Path) -> None:
    slices = model["quality"]["model_calibrated"]["topic_slices"]
    if not slices:
        return
    names = list(slices)
    figure, axis = plt.subplots(figsize=(7.6, 4.4))
    for offset, metric, color in ((-0.22, "micro_f1", "#4c78a8"), (0, "macro_f1", "#54a24b"), (0.22, "exact_match", "#f58518")):
        axis.bar([index + offset for index in range(3)], [slices[name][metric] for name in names], 0.22, label=metric.replace("_", " ").title(), color=color)
    axis.set_xticks(range(3), [f"{name}\n(n={slices[name]['rows']})" for name in names])
    axis.set_ylim(0, 1.05)
    axis.set_title("Sensitive-topic holdout slices — interpret small samples cautiously")
    axis.legend(ncol=3, loc="lower center")
    save(figure, "tiny2_validation_topic_slices_20260809.png", output)


def label_counts(model: dict, key: str, title: str, filename: str, output: Path) -> None:
    values = model["quality"]["model_calibrated"]["metrics"]["per_label"]
    names = list(values)
    figure, axis = plt.subplots(figsize=(10.5, 4.6))
    bars = axis.bar(names, [values[name][key] for name in names], color="#54a24b" if key == "support" else "#f58518")
    axis.bar_label(bars, fmt="%.0f", padding=2, fontsize=7, rotation=90)
    axis.set_title(title)
    axis.set_ylabel("Examples")
    axis.tick_params(axis="x", rotation=40)
    save(figure, filename, output)


def main() -> None:
    parser = argparse.ArgumentParser(description="Render ruBERT validation evidence charts.")
    parser.add_argument("--report", type=Path, default=REPORT)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    report = load_report(args.report)
    models = report["models"]
    production = models[PRODUCTION_MODEL]
    rows = int(report["holdout_rows"])
    production_profile(production, rows, args.output)
    speed_profile(production, rows, args.output)
    per_label(production, args.output)
    calibration(production, args.output)
    thresholds(production, args.output)
    topic_slices(production, args.output)
    label_counts(production, "support", "ruBERT Tiny2 validation support by label", "tiny2_validation_label_support_20260809.png", args.output)
    label_counts(production, "predicted_positives", "ruBERT Tiny2 validation predicted positives by label", "tiny2_validation_prediction_balance_20260809.png", args.output)


if __name__ == "__main__":
    main()
