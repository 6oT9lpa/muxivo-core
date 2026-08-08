import os
import subprocess
import sys
from pathlib import Path


def test_production_entrypoint_imports_without_retired_sensitive_topic_config(
    tmp_path,
) -> None:
    root = Path(__file__).resolve().parents[3]
    assert not (
        tmp_path / "configs" / "training" / "sensitive_topic_curation.yaml"
    ).exists()
    environment = {
        **os.environ,
        "DATABASE_URL": "postgresql://ignored:ignored@127.0.0.1:1/ignored",
        "MUXIVO_CORE_INTERNAL_API_KEY": "entrypoint-smoke-key-32-characters",
        "MUXIVO_CORE_API_RUBERT_ENABLED": "false",
        "MUXIVO_CORE_API_RUBERT_REQUIRED": "false",
        "PYTHONPATH": os.pathsep.join(
            filter(None, (str(root), os.environ.get("PYTHONPATH")))
        ),
    }
    result = subprocess.run(
        [sys.executable, "-c", "import main_api; print(main_api.app.title)"],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().endswith("Muxivo Core")
