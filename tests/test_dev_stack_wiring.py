import json
import os
import re
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import TypedDict, cast

import pytest

from living_kb import dev_stack
from living_kb.config import Settings


class StackInfo(TypedDict):
    project_name: str
    ports: dict[str, int]
    env: dict[str, str]


def _clean_environment() -> dict[str, str]:
    environment = os.environ.copy()
    names = set(dev_stack.EXPLICIT_ENV_VARS) | {field.upper() for field in Settings.model_fields}
    for name in names:
        environment.pop(name, None)
    return environment


@pytest.fixture
def linked_worktree(tmp_path: Path) -> Iterator[Path]:
    repo_root = Path(__file__).resolve().parents[1]
    main = tmp_path / "main"
    main.mkdir()
    for relative in (
        "Makefile",
        "infra/dev-stack.sh",
        "infra/docker-compose.yml",
        "living_kb/dev_stack.py",
        ".env.example",
    ):
        destination = main / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(repo_root / relative, destination)
    subprocess.run(["git", "init", "-q", str(main)], check=True)
    subprocess.run(
        ["git", "-C", str(main), "config", "user.email", "wiring@example.test"], check=True
    )
    subprocess.run(["git", "-C", str(main), "config", "user.name", "Wiring Test"], check=True)
    subprocess.run(["git", "-C", str(main), "add", "."], check=True)
    subprocess.run(["git", "-C", str(main), "commit", "-qm", "fixture"], check=True)
    worker = tmp_path / "worker"
    subprocess.run(
        ["git", "-C", str(main), "worktree", "add", "-q", "--detach", str(worker), "HEAD"],
        check=True,
    )
    try:
        yield worker
    finally:
        subprocess.run(
            ["git", "-C", str(main), "worktree", "remove", "--force", str(worker)],
            check=False,
            capture_output=True,
        )


@pytest.fixture
def fake_tools(linked_worktree: Path, tmp_path: Path) -> tuple[Path, dict[str, str], Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    command_log = tmp_path / "commands.log"
    command_log.touch()
    real_bash = shutil.which("bash")
    real_docker = shutil.which("docker")
    assert real_bash is not None
    assert real_docker is not None
    (bin_dir / "bash").write_text(
        '#!/bin/bash\nprintf "%s\\n" "$*" >> "$COMMAND_LOG"\nexec "$REAL_BASH" "$@"\n',
        encoding="utf-8",
    )
    (bin_dir / "uv").write_text(
        '#!/bin/bash\nprintf "%s\\n" "$*" >> "$COMMAND_LOG"\n',
        encoding="utf-8",
    )
    (bin_dir / "docker").write_text(
        "#!/bin/bash\n"
        'printf "%s\\n" "$*" >> "$COMMAND_LOG"\n'
        'if [[ " $* " == *" config "* ]]; then exec "$REAL_DOCKER" "$@"; fi\n',
        encoding="utf-8",
    )
    for shim in ("bash", "uv", "docker"):
        (bin_dir / shim).chmod(0o755)
    environment = _clean_environment()
    environment["PATH"] = f"{bin_dir}{os.pathsep}{environment.get('PATH', '')}"
    environment["COMMAND_LOG"] = str(command_log)
    environment["REAL_BASH"] = real_bash
    environment["REAL_DOCKER"] = real_docker
    environment["LIVING_KB_DEV_STACK_PYTHON"] = "python3"
    return linked_worktree, environment, command_log


def _run(worktree: Path, args: list[str], environment: dict[str, str]) -> str:
    result = subprocess.run(
        args, cwd=worktree, env=environment, capture_output=True, text=True, check=True
    )
    return result.stdout


def _stack_info(worktree: Path, environment: dict[str, str]) -> StackInfo:
    output = _run(
        worktree, ["python3", "-m", "living_kb.dev_stack", "--format", "json"], environment
    )
    return cast(StackInfo, json.loads(output))


def test_fresh_env_example_still_yields_derived_ports(linked_worktree: Path) -> None:
    example = (linked_worktree / ".env.example").read_text(encoding="utf-8")
    for key in dev_stack.EXPLICIT_ENV_VARS:
        assert re.search(rf"^{key}=", example, re.MULTILINE) is None
    shutil.copy2(linked_worktree / ".env.example", linked_worktree / ".env")

    info = _stack_info(linked_worktree, _clean_environment())

    assert info["project_name"] != "infra"
    assert info["ports"] != dev_stack.BASE_PORTS
    assert info["env"]["DATABASE_URL"].endswith(f"localhost:{info['ports']['postgres']}/living_kb")
    assert info["env"]["INNGEST_BASE_URL"] == f"http://localhost:{info['ports']['inngest']}"


def test_start_uses_the_derived_project_and_api_port(
    fake_tools: tuple[Path, dict[str, str], Path],
) -> None:
    worktree, environment, command_log = fake_tools
    info = _stack_info(worktree, environment)

    _run(worktree, ["make", "start"], environment)
    commands = command_log.read_text(encoding="utf-8")

    assert "infra/dev-stack.sh start" in commands
    assert f"-p {info['project_name']}" in commands
    assert f"--port {info['ports']['api']}" in commands
    assert "-p infra" not in commands
    assert "--port 8000" not in commands


def test_stop_and_reset_target_only_the_derived_project(
    fake_tools: tuple[Path, dict[str, str], Path],
) -> None:
    worktree, environment, command_log = fake_tools
    project = _stack_info(worktree, environment)["project_name"]

    _run(worktree, ["make", "stop"], environment)
    _run(worktree, ["make", "reset"], environment)
    commands = command_log.read_text(encoding="utf-8")

    assert f"-p {project} down" in commands
    assert f"-p {project} down -v --remove-orphans" in commands
    assert "-p infra" not in commands


def test_compose_config_points_inngest_at_the_derived_api_port(
    fake_tools: tuple[Path, dict[str, str], Path],
) -> None:
    worktree, environment, _ = fake_tools
    info = _stack_info(worktree, environment)
    api_port = info["ports"]["api"]

    output = _run(worktree, ["make", "stack-plan"], environment)

    assert f"host.docker.internal:{api_port}/api/inngest" in output
    assert "host.docker.internal:8000/api/inngest" not in output
    assert f"project={info['project_name']}" in output
    assert f"-p {info['project_name']}" in output


def test_make_test_goes_through_the_stack_wrapper(
    fake_tools: tuple[Path, dict[str, str], Path],
) -> None:
    worktree, environment, command_log = fake_tools

    _run(worktree, ["make", "test"], environment)
    commands = command_log.read_text(encoding="utf-8")

    assert "infra/dev-stack.sh test" in commands
    assert "run pytest" in commands


def test_stack_env_is_regenerated_on_every_invocation(
    fake_tools: tuple[Path, dict[str, str], Path],
) -> None:
    worktree, environment, _ = fake_tools
    env_file = worktree / ".dev-stack.env"
    env_file.write_text("COMPOSE_PROJECT_NAME=stale\nAPI_PORT=59999\n", encoding="utf-8")
    info = _stack_info(worktree, environment)

    _run(worktree, ["make", "stack-env"], environment)
    assert f"COMPOSE_PROJECT_NAME={info['project_name']}" in env_file.read_text(encoding="utf-8")
    assert f"API_PORT={info['ports']['api']}" in env_file.read_text(encoding="utf-8")
    assert "stale" not in env_file.read_text(encoding="utf-8")

    env_file.unlink()
    _run(worktree, ["make", "stack-env"], environment)
    assert env_file.exists()
