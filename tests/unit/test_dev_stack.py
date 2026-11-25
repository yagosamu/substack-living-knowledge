import socket
import subprocess
from pathlib import Path

import pytest

from living_kb import dev_stack
from living_kb.config import Settings


def _fake_git(common_dir: Path, worktrees: list[Path]) -> object:
    def run(root: Path, *args: str) -> str:
        if args[:2] == ("worktree", "list"):
            return "\n".join(f"worktree {path}" for path in worktrees)
        if args[0] == "rev-parse":
            return str(common_dir)
        raise AssertionError(f"unexpected git call: {args}")

    return run


def _free(_port: int) -> bool:
    return True


def _busy(_port: int) -> bool:
    return False


def _first_candidate(_root: Path) -> int:
    return 0


def test_primary_worktree_keeps_the_existing_project_and_ports(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(dev_stack, "_run_git", _fake_git(tmp_path / ".git", [tmp_path]))
    monkeypatch.setattr(dev_stack, "_port_is_free", _free)

    plan = dev_stack.build_plan(tmp_path, {})

    assert plan.project_name == "infra"
    assert plan.ports == dev_stack.BASE_PORTS
    assert plan.env()["DATABASE_URL"].endswith("localhost:5433/living_kb")
    assert plan.env()["INNGEST_BASE_URL"] == "http://localhost:8288"
    assert not (tmp_path / dev_stack.ALLOCATION_FILE_NAME).exists()


def test_linked_worktrees_get_stable_disjoint_ports(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    main = tmp_path / "main"
    first = tmp_path / "worker-one"
    second = tmp_path / "worker-two"
    for path in (main, first, second):
        (path / ".git").mkdir(parents=True)
    worktrees = [main, first, second]
    monkeypatch.setattr(dev_stack, "_port_is_free", _free)

    def git_for(root: Path, *args: str) -> str:
        if args[:2] == ("worktree", "list"):
            return "\n".join(f"worktree {path}" for path in worktrees)
        if args[0] == "rev-parse":
            return str(main / ".git")
        raise AssertionError(f"unexpected git call: {args}")

    monkeypatch.setattr(dev_stack, "_run_git", git_for)
    first_plan = dev_stack.build_plan(first, {})
    second_plan = dev_stack.build_plan(second, {})

    assert first_plan.project_name != "infra"
    assert first_plan.project_name != second_plan.project_name
    assert not set(first_plan.ports.values()) & set(second_plan.ports.values())
    assert not set(first_plan.ports.values()) & set(dev_stack.BASE_PORTS.values())

    monkeypatch.setattr(dev_stack, "_port_is_free", _busy)
    assert dev_stack.build_plan(first, {}).ports == first_plan.ports


def test_occupied_port_block_is_skipped(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    main = tmp_path / "main"
    worker = tmp_path / "worker"
    other = tmp_path / "other"
    for path in (main, worker, other):
        (path / ".git").mkdir(parents=True)
    monkeypatch.setattr(dev_stack, "_run_git", _fake_git(main / ".git", [main, worker, other]))
    monkeypatch.setattr(dev_stack, "_port_is_free", _free)
    monkeypatch.setattr(dev_stack, "_candidate_start", _first_candidate)
    dev_stack._write_allocation(
        other, dev_stack.Allocation("living-kb-other", dev_stack._candidate_ports(0))
    )

    plan = dev_stack.build_plan(worker, {})

    assert plan.ports == dev_stack._candidate_ports(1)


def test_explicit_environment_overrides_derived_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(dev_stack, "_run_git", _fake_git(tmp_path / ".git", [tmp_path]))
    monkeypatch.setattr(dev_stack, "_port_is_free", _free)

    plan = dev_stack.build_plan(
        tmp_path,
        {
            "COMPOSE_PROJECT_NAME": "custom-stack",
            "POSTGRES_PORT": "6543",
            "ADMINER_PORT": "9222",
            "API_PORT": "8123",
            "DATABASE_URL": "postgresql+asyncpg://u:p@localhost:6543/custom_db",
            "INNGEST_BASE_URL": "http://localhost:9999",
        },
    )

    assert plan.project_name == "custom-stack"
    assert plan.ports["postgres"] == 6543
    assert plan.ports["adminer"] == 9222
    assert plan.ports["api"] == 8123
    assert plan.env()["DATABASE_URL"] == "postgresql+asyncpg://u:p@localhost:6543/custom_db"
    assert plan.env()["INNGEST_BASE_URL"] == "http://localhost:9999"


def test_env_file_port_override_updates_dependent_urls(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".env").write_text("POSTGRES_PORT=5543\nINNGEST_PORT=9999\n", encoding="utf-8")
    monkeypatch.setattr(dev_stack, "_run_git", _fake_git(tmp_path / ".git", [tmp_path]))
    monkeypatch.setattr(dev_stack, "_port_is_free", _free)

    plan = dev_stack.build_plan(tmp_path, {})

    assert plan.ports["postgres"] == 5543
    assert plan.ports["inngest"] == 9999
    assert plan.env()["DATABASE_URL"].endswith("localhost:5543/living_kb")
    assert plan.env()["INNGEST_BASE_URL"] == "http://localhost:9999"


def test_generated_stack_env_beats_dotenv_in_settings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for field in Settings.model_fields:
        monkeypatch.delenv(field.upper(), raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "DATABASE_URL=postgresql+asyncpg://living_kb:living_kb_pw@localhost:1111/from_dotenv\n"
        "API_PORT=8000\n"
        "INNGEST_BASE_URL=http://localhost:8288\n",
        encoding="utf-8",
    )
    (tmp_path / ".dev-stack.env").write_text(
        "DATABASE_URL=postgresql+asyncpg://living_kb:living_kb_pw@localhost:2222/generated\n"
        "API_PORT=8222\n"
        "INNGEST_BASE_URL=http://localhost:8223\n",
        encoding="utf-8",
    )

    config = Settings()

    assert config.database_url.endswith("localhost:2222/generated")
    assert config.api_port == 8222
    assert config.inngest_base_url == "http://localhost:8223"


def test_same_named_worktrees_get_distinct_projects_and_hashes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    main = tmp_path / "main"
    first = tmp_path / "one" / "worker"
    second = tmp_path / "two" / "worker"
    for path in (main, first, second):
        (path / ".git").mkdir(parents=True)
    monkeypatch.setattr(dev_stack, "_run_git", _fake_git(main / ".git", [main, first, second]))
    monkeypatch.setattr(dev_stack, "_port_is_free", _free)

    first_plan = dev_stack.build_plan(first, {})
    second_plan = dev_stack.build_plan(second, {})

    assert first_plan.project_name.startswith("living-kb-worker-")
    assert first_plan.project_name != second_plan.project_name
    assert dev_stack._candidate_start(first) != dev_stack._candidate_start(second)
    assert dev_stack._candidate_start(first) == dev_stack._candidate_start(first)
    assert dev_stack._candidate_start(second) == dev_stack._candidate_start(second)
    assert first_plan.ports != second_plan.ports


def test_candidate_start_is_path_derived_and_repeatable(tmp_path: Path) -> None:
    first = tmp_path / "alpha"
    second = tmp_path / "beta"
    assert dev_stack._candidate_start(first) == dev_stack._candidate_start(first)
    assert dev_stack._candidate_start(second) == dev_stack._candidate_start(second)
    assert dev_stack._candidate_start(first) != dev_stack._candidate_start(second)


def test_shell_values_win_over_dotenv_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".env").write_text("POSTGRES_PORT=5111\nADMINER_PORT=5222\n", encoding="utf-8")
    monkeypatch.setattr(dev_stack, "_run_git", _fake_git(tmp_path / ".git", [tmp_path]))
    monkeypatch.setattr(dev_stack, "_port_is_free", _free)

    plan = dev_stack.build_plan(tmp_path, {"POSTGRES_PORT": "6111", "ADMINER_PORT": "6222"})

    assert plan.ports["postgres"] == 6111
    assert plan.ports["adminer"] == 6222


def test_real_port_probe_detects_a_bound_socket() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as bound:
        bound.bind(("0.0.0.0", 0))
        port = int(bound.getsockname()[1])
        assert dev_stack._port_is_free(port) is False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("0.0.0.0", 0))
        free_port = int(probe.getsockname()[1])
    assert dev_stack._port_is_free(free_port) is True


def test_written_env_round_trips_through_settings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for field in Settings.model_fields:
        monkeypatch.delenv(field.upper(), raising=False)
    url = "postgresql+asyncpg://u:p w@localhost:5433/db with space"
    plan = dev_stack.StackPlan(
        project_name="derived-project",
        ports=dict(dev_stack.BASE_PORTS),
        explicit={"DATABASE_URL": url},
    )
    path = tmp_path / ".dev-stack.env"
    monkeypatch.chdir(tmp_path)

    dev_stack.write_env_file(plan, path)
    text = path.read_text(encoding="utf-8")

    assert f"DATABASE_URL='{url}'" in text
    assert "INNGEST_BASE_URL=http://localhost:8288" in text
    sourced = subprocess.run(
        ["bash", "-c", '. "$1"; printf "%s" "$DATABASE_URL"', "bash", str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert sourced.stdout == url
    config = Settings()
    assert config.database_url == url
    assert config.inngest_base_url == "http://localhost:8288"


def test_persisted_block_is_dropped_when_another_worktree_claims_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    main = tmp_path / "main"
    worker = tmp_path / "worker"
    other = tmp_path / "other"
    for path in (main, worker, other):
        (path / ".git").mkdir(parents=True)
    monkeypatch.setattr(dev_stack, "_run_git", _fake_git(main / ".git", [main, worker, other]))
    monkeypatch.setattr(dev_stack, "_port_is_free", _free)
    stale = dev_stack.Allocation(
        project_name=dev_stack._default_project_name(worker, False),
        ports=dev_stack._candidate_ports(7),
    )
    dev_stack._write_allocation(worker, stale)
    dev_stack._write_allocation(other, stale)

    plan = dev_stack.build_plan(worker, {})

    assert plan.ports != stale.ports


def test_persisted_block_with_a_stale_project_name_is_replaced(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    main = tmp_path / "main"
    worker = tmp_path / "worker"
    for path in (main, worker):
        (path / ".git").mkdir(parents=True)
    monkeypatch.setattr(dev_stack, "_run_git", _fake_git(main / ".git", [main, worker]))
    monkeypatch.setattr(dev_stack, "_port_is_free", _free)
    dev_stack._write_allocation(
        worker, dev_stack.Allocation("old-project", dev_stack._candidate_ports(9))
    )

    plan = dev_stack.build_plan(worker, {})
    stored = dev_stack._read_allocation(worker)

    assert plan.project_name == dev_stack._default_project_name(worker, False)
    assert stored is not None
    assert stored.project_name == plan.project_name


def test_dev_base_ports_match_the_spec_defaults(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for field in Settings.model_fields:
        monkeypatch.delenv(field.upper(), raising=False)
    monkeypatch.chdir(tmp_path)
    config = Settings()
    assert config.postgres_port == dev_stack.BASE_PORTS["postgres"]
    assert config.adminer_port == dev_stack.BASE_PORTS["adminer"]
    assert config.inngest_port == dev_stack.BASE_PORTS["inngest"]
    assert config.api_port == dev_stack.BASE_PORTS["api"]
