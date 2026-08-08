import json
import sys
from pathlib import Path

import torch
from torch import nn

from scripts.training import export_mit_yolo_onnx, package_yolo_onnx, train_mit_yolo


class _RawMitYolo(nn.Module):
    def forward(self, images):
        batch = images.shape[0]
        classes = torch.tensor([1.0, -1.0]).view(1, 2, 1, 1).expand(batch, -1, -1, -1)
        anchors = torch.zeros(batch, 1, 1, 1, 1)
        boxes = torch.tensor([1.0, 2.0, 3.0, 4.0]).view(1, 4, 1, 1).expand(batch, -1, -1, -1)
        return {"Main": [[classes, anchors, boxes]], "AUX": []}


def test_export_adapter_decodes_xywh_class_matrix() -> None:
    adapter = export_mit_yolo_onnx.MitYoloExportAdapter(
        _RawMitYolo(),
        anchor_grid=torch.tensor([[10.0, 20.0]]),
        scaler=torch.tensor([[2.0]]),
    )

    output = adapter(torch.zeros(1, 3, 8, 8))

    assert output.shape == (1, 1, 6)
    torch.testing.assert_close(output[0, 0, :4], torch.tensor([12.0, 22.0, 8.0, 12.0]))
    torch.testing.assert_close(output[0, 0, 4:], torch.sigmoid(torch.tensor([1.0, -1.0])))


def test_training_wrapper_stages_dataset_config_and_uses_bounded_arguments(
    tmp_path: Path, monkeypatch
) -> None:
    checkout = tmp_path / "checkout"
    (checkout / "yolo" / "config" / "dataset").mkdir(parents=True)
    (checkout / "yolo" / "lazy.py").write_text("", encoding="utf-8")
    dataset = tmp_path / "dataset.yaml"
    dataset.write_text("path: dataset\ntrain: train\nvalidation: val\n", encoding="utf-8")
    calls: list[tuple[list[str], Path | None, dict[str, str] | None]] = []
    monkeypatch.setattr(train_mit_yolo, "prepare_checkout", lambda _checkout: None)
    monkeypatch.setattr(
        train_mit_yolo.subprocess,
        "run",
        lambda command, cwd=None, env=None, check=None: calls.append((command, cwd, env)),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "train_mit_yolo.py", "--checkout", str(checkout), "--dataset-config", str(dataset),
            "--device", "cuda", "--batch-size", "2", "--epochs", "1", "--cpu-workers", "0",
        ],
    )

    train_mit_yolo.main()

    assert (checkout / "yolo" / "config" / "dataset" / "muxivo_core.yaml").read_text() == dataset.read_text()
    command, cwd, environment = calls[0]
    assert "dataset=muxivo_core" in command
    assert "task.epoch=1" in command
    assert "task.data.batch_size=2" in command
    assert "image_size=[640,640]" in command
    assert "cpu_num=0" in command
    assert "weight=False" in command
    assert "accelerator=gpu" in command
    assert "device=1" in command
    assert "use_wandb=True" in command
    assert environment is not None and environment["PYTHONUTF8"] == "1"
    assert environment["WANDB_MODE"] == "disabled"
    assert cwd == checkout.resolve()


def test_packager_records_mit_provenance_and_onnx_checksum(tmp_path: Path, monkeypatch) -> None:
    onnx = tmp_path / "trained.onnx"
    onnx.write_bytes(b"onnx-test-artifact")
    output = tmp_path / "bundle"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "package_yolo_onnx.py", "--onnx", str(onnx), "--output-dir", str(output),
            "--model-name", "moderation-yolo", "--model-version", "test-v1",
            "--class-name", "suspicious_qr", "--class-name", "fake_giveaway_banner",
        ],
    )

    package_yolo_onnx.main()

    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["license"] == "MIT"
    assert manifest["source_commit"] == train_mit_yolo.SOURCE_COMMIT
    assert manifest["class_names"] == ["suspicious_qr", "fake_giveaway_banner"]
    assert len(manifest["onnx_sha256"]) == 64
