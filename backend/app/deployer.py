import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Callable

import docker
import httpx
from docker.errors import DockerException, NotFound

from .config import settings

TARGET_LABEL = "nexus.target"
Log = Callable[[str], None]


def _client() -> docker.DockerClient:
    return docker.from_env(version="auto")


def clone(url: str, log: Log) -> tuple[Path, str]:
    """Shallow-clone a repository into a temp dir. Returns (repo_dir, commit_sha)."""
    temp = Path(tempfile.mkdtemp(prefix="nexus-deploy-"))
    repo = temp / "repo"
    log(f"git clone --depth 1 {url}")
    done = subprocess.run(["git", "clone", "--depth", "1", url, str(repo)], capture_output=True, text=True, timeout=90)
    if done.returncode != 0:
        shutil.rmtree(temp, ignore_errors=True)
        raise RuntimeError(f"git clone failed: {done.stderr.strip()[-500:]}")
    sha = subprocess.run(["git", "-C", str(repo), "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    log(f"Cloned commit {sha}")
    return repo, sha


def exposed_port(source_dir: Path, default: int = 8000) -> int:
    dockerfile = source_dir / "Dockerfile"
    match = re.search(r"^\s*EXPOSE\s+(\d+)", dockerfile.read_text(errors="ignore"), re.MULTILINE | re.IGNORECASE)
    return int(match.group(1)) if match else default


def build(source_dir: Path, tag: str, run_id: str, log: Log) -> None:
    if not (source_dir / "Dockerfile").is_file():
        raise RuntimeError("No Dockerfile found at the repository root, so NEXUS cannot build a container for it.")
    log(f"docker build -t {tag} .")
    api = _client().api
    for chunk in api.build(path=str(source_dir), tag=tag, rm=True, forcerm=True, decode=True, labels={TARGET_LABEL: run_id}):
        if "error" in chunk:
            raise RuntimeError(f"Image build failed: {chunk['error'].strip()}")
        line = (chunk.get("stream") or "").strip()
        # skip pip/npm progress bars, which are mostly box-drawing glyphs
        if line and "\u2501" not in line and "\u2588" not in line:
            log(line)


def start(tag: str, name: str, run_id: str, log: Log) -> None:
    log(f"docker run --name {name} --network {settings.docker_network} {tag}")
    _client().containers.run(
        tag, name=name, detach=True, network=settings.docker_network,
        labels={TARGET_LABEL: run_id}, mem_limit="512m", nano_cpus=1_000_000_000,
    )


def wait_ready(name: str, port: int, log: Log) -> str:
    """Poll the container until it answers HTTP. Returns its base URL."""
    base_url = f"http://{name}:{port}"
    container = _client().containers.get(name)
    deadline = time.monotonic() + settings.deploy_timeout_seconds
    while time.monotonic() < deadline:
        container.reload()
        if container.status in ("exited", "dead"):
            tail = container.logs(tail=30).decode(errors="ignore")
            raise RuntimeError(f"Container exited during startup:\n{tail}")
        for probe in ("/health", "/"):
            try:
                response = httpx.get(base_url + probe, timeout=2)
                if response.status_code < 500:
                    log(f"{base_url}{probe} answered HTTP {response.status_code}; service is ready")
                    return base_url
            except httpx.HTTPError:
                pass
        time.sleep(1)
    raise RuntimeError(f"Service did not answer on port {port} within {settings.deploy_timeout_seconds}s")


def container_logs(name: str, tail: int = 50) -> str:
    try:
        return _client().containers.get(name).logs(tail=tail).decode(errors="ignore")
    except DockerException:
        return ""


def teardown(name: str | None, tag: str | None, source_dir: Path | None, log: Log) -> None:
    client = _client()
    if name:
        try:
            client.containers.get(name).remove(force=True)
            log(f"Removed container {name}")
        except NotFound:
            pass
    if tag:
        try:
            client.images.remove(tag, force=True)
            log(f"Removed image {tag}")
        except NotFound:
            pass
    if source_dir and source_dir.name == "repo":
        shutil.rmtree(source_dir.parent, ignore_errors=True)
        log("Deleted cloned source")


def cleanup_leftovers() -> None:
    """Remove target containers and images left behind by a previous backend process."""
    try:
        client = _client()
        for container in client.containers.list(all=True, filters={"label": TARGET_LABEL}):
            container.remove(force=True)
        for image in client.images.list(filters={"label": TARGET_LABEL}):
            client.images.remove(image.id, force=True)
    except DockerException:
        pass
