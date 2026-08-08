# MIT YOLO training and local ONNX runtime

The application does not import Ultralytics or any AGPL package. Training is isolated in the
official MIT-licensed `MultimediaTechLab/YOLO` checkout pinned to commit
`c4cb5f6f56102eceeaa7d75e23e1125cd0373eaf`. Production loads only a verified ONNX artifact
through ONNX Runtime.

## Dataset contract

Prepare a normal object-detection dataset outside Git. Its dataset YAML must point to train and
validation images and declare class names in exactly the same order later passed to the packaging
command. Only the detector classes allowed by `configs/policies/yolo_rules.yaml` may enter the
production manifest.

## Prepare and train

Use a dedicated virtual environment for the external trainer. The wrapper verifies both the pinned
commit and the MIT license before invoking the upstream command. Pin `lightning==2.5.6`; newer
2.6 releases changed the custom progress-bar API used by this upstream revision. The wrapper keeps
WandB in disabled mode so its expected logger contract is present without network publication:

```powershell
py -3.13 -m venv .venv-yolo-mit
.\.venv-yolo-mit\Scripts\python.exe -m pip install `
  torch==2.13.0+cu130 torchvision==0.28.0+cu130 `
  --index-url https://download.pytorch.org/whl/cu130
.\.venv-yolo-mit\Scripts\python.exe -m pip install -r E:\models\src\mit-yolo\requirements.txt
.\.venv-yolo-mit\Scripts\python.exe -m pip install `
  lightning==2.5.6 onnx==1.19.1 onnxruntime==1.23.2 onnxscript==0.5.4

python scripts/training/train_mit_yolo.py `
  --checkout E:\models\src\mit-yolo `
  --dataset-config E:\datasets\moderation-images\dataset.yaml `
  --device cuda --model v9-s --batch-size 4 --epochs 100 --image-size 640
```

Python 3.14 is not supported by the pinned upstream Hydra CLI; the verified Windows smoke used
Python 3.13.14. Select the PyTorch CUDA wheel matching the target driver instead of copying the
CUDA 13.0 command blindly to a different host.

Export the selected Lightning checkpoint through the repository adapter. It converts the upstream
multi-head output to the runtime's fixed `[batch, boxes, xywh + class probabilities]` contract:

```powershell
python scripts/training/export_mit_yolo_onnx.py `
  --checkout E:\models\src\mit-yolo `
  --training-config E:\models\runs\v9-dev\.hydra\config.yaml `
  --checkpoint E:\models\runs\v9-dev\checkpoints\best.ckpt `
  --output E:\models\runs\moderation-v1.onnx --image-size 640
```

## Package for production

```powershell
python scripts/training/package_yolo_onnx.py `
  --onnx E:\models\runs\best.onnx `
  --output-dir E:\Muxivo Core\models\media\yolo\moderation-v1 `
  --model-name moderation-yolov9-s --model-version moderation-v1 `
  --class-name suspicious_qr --class-name fake_giveaway_banner `
  --output-layout xywh_classes
```

Set `YOLO_ENABLED=true`, `YOLO_MODEL_DIR` to that bundle, and choose `YOLO_DEVICE=cpu` or `cuda`.
For CUDA install `requirements-media-gpu.txt`; for CPU install `requirements-media.txt`. Health is
ready for CUDA only when ONNX Runtime actually activates `CUDAExecutionProvider`.

Benchmark the exact production provider and retain the JSON report with the model release:

```powershell
python -m scripts.media.benchmark_onnx_yolo `
  --model-dir E:\Muxivo Core\models\media\yolo\moderation-v1 `
  --image E:\datasets\moderation-images\benchmark.png `
  --device cuda --warmup 10 --iterations 100 `
  --output E:\models\reports\moderation-v1-gtx1650.json
```
