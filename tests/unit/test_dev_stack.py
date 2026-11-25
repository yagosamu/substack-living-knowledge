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
            "API_PORT": "8123",
            "DATABASE_URL": "postgresql+asyncpg://u:p@localhost:6543/custom_db",
            "INNGEST_BASE_URL": "http://localhost:9999",
        },
    )

    assert plan.project_name == "custom-stack"
    assert plan.ports["postgres"] == 6543
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


def test_settings_follow_generated_stack_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for field in Settings.model_fields:
        monkeypatch.delenv(field.upper(), raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("POSTGRES_PORT=5433\n", encoding="utf-8")
    (tmp_path / ".dev-stack.env").write_text(
        "DATABASE_URL=postgresql+asyncpg://living_kb:living_kb_pw@localhost:15433/living_kb\n"
        "API_PORT=18000\n",
        encoding="utf-8",
    )

    config = Settings()

    assert config.database_url.endswith("localhost:15433/living_kb")
    assert config.api_port == 18000
