"""Repository copy for workers. Each worker operates in its own directory."""

import shutil
import subprocess
from pathlib import Path


def prepare_worker_repo(
    source: Path,
    worker_root: Path,
    worker_id: str,
) -> Path:
    """Copy source to worker_root / worker_id / repo. Returns the worker's working directory."""
    dest = worker_root / worker_id / "repo"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        shutil.rmtree(dest)
    if source.is_dir():
        shutil.copytree(source, dest, ignore=shutil.ignore_patterns(".git", ".tasuki"))
        # If .git exists, copy it as-is to preserve clone status (simplified: copy .git entirely)
        git_src = source / ".git"
        if git_src.exists():
            shutil.copytree(git_src, dest / ".git")
    else:
        dest.mkdir(parents=True)
    return dest


def clone_worker_repo(git_url: str, worker_root: Path, worker_id: str) -> Path:
    """Prepare a worker copy by git cloning."""
    dest = worker_root / worker_id / "repo"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        shutil.rmtree(dest)
    subprocess.run(["git", "clone", "--depth", "1", git_url, str(dest)], check=True)
    return dest
