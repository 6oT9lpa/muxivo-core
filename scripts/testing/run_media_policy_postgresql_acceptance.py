"""Run migration and media-policy lifecycle checks on a disposable PostgreSQL cluster."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def run(command: list[str], *, env: dict[str, str] | None = None, timeout: int = 30) -> None:
    print("RUN", " ".join(command), flush=True)
    subprocess.run(command, check=True, env=env, timeout=timeout)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--postgres-bin", type=Path, default=Path(r"D:\postgres\bin"))
    parser.add_argument("--port", type=int, default=55432)
    args = parser.parse_args()

    temporary_root = Path(tempfile.gettempdir()).resolve()
    acceptance_root = (temporary_root / "muxivo-coreator-pg-acceptance").resolve()
    if not acceptance_root.is_relative_to(temporary_root) or acceptance_root == temporary_root:
        raise RuntimeError("unsafe PostgreSQL acceptance directory")
    data_directory = acceptance_root / "data"
    log_path = acceptance_root / "postgres.log"
    pg_ctl = args.postgres_bin / "pg_ctl.exe"
    initdb = args.postgres_bin / "initdb.exe"
    psql = args.postgres_bin / "psql.exe"
    for binary in (pg_ctl, initdb, psql):
        if not binary.is_file():
            raise FileNotFoundError(binary)

    if acceptance_root.exists():
        if (data_directory / "postmaster.pid").exists():
            run([str(pg_ctl), "stop", "-D", str(data_directory), "-m", "fast", "-w"])
        shutil.rmtree(acceptance_root)
    acceptance_root.mkdir()

    environment = os.environ.copy()
    test_url = f"postgresql://postgres@127.0.0.1:{args.port}/muxivo_core_acceptance?connect_timeout=5"
    environment.update(
        DATABASE_URL=test_url,
        TEST_POSTGRESQL_URL=test_url,
        PGCONNECT_TIMEOUT="5",
    )
    try:
        run(
            [
                str(initdb),
                "-D", str(data_directory),
                "--username=postgres",
                "--auth=trust",
                "--encoding=UTF8",
                "--locale=C",
            ],
            timeout=45,
        )
        run(
            [
                str(pg_ctl), "start", "-D", str(data_directory), "-l", str(log_path),
                "-o", f"-p {args.port} -h 127.0.0.1", "-w",
            ]
        )
        run(
            [
                str(psql), "--no-password", "-h", "127.0.0.1", "-p", str(args.port),
                "-U", "postgres", "-d", "postgres", "-v", "ON_ERROR_STOP=1",
                "-c", "CREATE DATABASE muxivo_core_acceptance",
            ],
            env=environment,
        )
        run([str(Path(".venv/Scripts/alembic.exe")), "upgrade", "head"], env=environment, timeout=45)
        run([str(Path(".venv/Scripts/alembic.exe")), "current"], env=environment)
        run(
            [
                str(Path(".venv/Scripts/python.exe")), "-m", "pytest",
                "tests/integration/test_postgresql_media_policy_repository.py", "-q",
                "--basetemp=.pytest_tmp_postgresql_acceptance",
            ],
            env=environment,
            timeout=45,
        )
        run(
            [
                str(psql), "--no-password", "-h", "127.0.0.1", "-p", str(args.port),
                "-U", "postgres", "-d", "muxivo_core_acceptance", "-Atc",
                "SELECT version_num FROM alembic_version",
            ],
            env=environment,
        )
    finally:
        if (data_directory / "postmaster.pid").exists():
            try:
                run([str(pg_ctl), "stop", "-D", str(data_directory), "-m", "fast", "-w"])
            except (subprocess.SubprocessError, OSError) as exc:
                print(f"PostgreSQL cleanup warning: {type(exc).__name__}", file=sys.stderr)
        if acceptance_root.exists():
            shutil.rmtree(acceptance_root)


if __name__ == "__main__":
    main()
