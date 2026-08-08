"""Prepare and run the pinned MIT YOLO trainer outside the application environment."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


SOURCE_REPOSITORY = "https://github.com/MultimediaTechLab/YOLO.git"
SOURCE_COMMIT = "c4cb5f6f56102eceeaa7d75e23e1125cd0373eaf"


def verify_checkout(checkout: Path) -> None:
    actual = subprocess.run(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if actual != SOURCE_COMMIT:
        raise RuntimeError("MIT YOLO checkout does not match the pinned commit")
    if not (checkout / "LICENSE").read_text(encoding="utf-8").startswith("MIT License"):
        raise RuntimeError("MIT YOLO checkout has an unexpected license")


def prepare_checkout(checkout: Path) -> None:
    if not checkout.exists():
        subprocess.run(["git", "clone", SOURCE_REPOSITORY, str(checkout)], check=True)
    subprocess.run(["git", "-C", str(checkout), "fetch", "origin", SOURCE_COMMIT], check=True)
    subprocess.run(["git", "-C", str(checkout), "checkout", "--detach", SOURCE_COMMIT], check=True)
    verify_checkout(checkout)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkout", type=Path, required=True)
    parser.add_argument("--dataset-config", type=Path, required=True)
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cuda")
    parser.add_argument("--model", default="v9-s")
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--image-size", type=int, default=640)
    parser.add_argument("--cpu-workers", type=int, default=4)
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    if not args.dataset_config.is_file():
        raise FileNotFoundError(f"dataset config is missing: {args.dataset_config}")
    prepare_checkout(args.checkout.resolve())
    dataset_name = "muxivo_core"
    upstream_dataset_config = args.checkout.resolve() / "yolo" / "config" / "dataset" / f"{dataset_name}.yaml"
    if args.dataset_config.resolve() != upstream_dataset_config.resolve():
        shutil.copy2(args.dataset_config.resolve(), upstream_dataset_config)
    if args.prepare_only:
        return
    environment = os.environ.copy()
    environment.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8", WANDB_MODE="disabled")
    accelerator = "gpu" if args.device == "cuda" else "cpu"
    subprocess.run(
        [
            sys.executable,
            str(args.checkout.resolve() / "yolo" / "lazy.py"),
            "task=train",
            f"task.data.batch_size={args.batch_size}",
            f"task.epoch={args.epochs}",
            f"image_size=[{args.image_size},{args.image_size}]",
            f"cpu_num={args.cpu_workers}",
            f"model={args.model}",
            f"dataset={dataset_name}",
            f"accelerator={accelerator}",
            "device=1",
            "weight=False",
            "use_wandb=True",
        ],
        cwd=args.checkout.resolve(),
        env=environment,
        check=True,
    )


if __name__ == "__main__":
    main()
