"""Local identity and port allocation for isolated development stacks."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import socket
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

PORT_NAMES = ("postgres", "adminer", "inngest", "api")
BASE_PORTS = {"postgres": 5433, "adminer": 8081, "inngest": 8288, "api": 8000}
WORKTREE_BASE_PORTS = {"postgres": 15433, "adminer": 18081, "inngest": 18288, "api": 18000}
PORT_ENV_VARS = {
    "postgres": "POSTGRES_PORT",
    "adminer": "ADMINER_PORT",
    "inngest": "INNGEST_PORT",
    "api": "API_PORT",
}
EXPLICIT_ENV_VARS = (
    "COMPOSE_PROJECT_NAME",
    "POSTGRES_PORT",
    "ADMINER_PORT",
    "INNGEST_PORT",
    "API_PORT",
    "DATABASE_URL",
    "INNGEST_BASE_URL",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_DB",
)
ALLOCATION_FILE_NAME = ".dev-stack.local.json"
ENV_FILE_NAME = ".dev-stack.env"
PORT_STRIDE = 10
CANDIDATE_COUNT = 512


@dataclass(frozen=True)
class Allocation:
    project_name: str
    ports: dict[str, int]


@dataclass(frozen=True)
class StackPlan:
    project_name: str
    ports: dict[str, int]
    explicit: dict[str, str]

    def env(self) -> dict[str, str]:
        values = {
            "COMPOSE_PROJECT_NAME": self.project_name,
            "POSTGRES_PORT": str(self.ports["postgres"]),
            "ADMINER_PORT": str(self.ports["adminer"]),
            "INNGEST_PORT": str(self.ports["inngest"]),
            "API_PORT": str(self.ports["api"]),
        }
        database_url = self.explicit.get("DATABASE_URL")
        if database_url is None:
            user = quote(self.explicit.get("POSTGRES_USER", "living_kb"), safe="")
            password = quote(self.explicit.get("POSTGRES_PASSWORD", "living_kb_pw"), safe="")
            database = quote(self.explicit.get("POSTGRES_DB", "living_kb"), safe="")
            database_url = (
                f"postgresql+asyncpg://{user}:{password}@localhost:"
                f"{self.ports['postgres']}/{database}"
            )
        values["DATABASE_URL"] = database_url
        values["INNGEST_BASE_URL"] = self.explicit.get("INNGEST_BASE_URL") or (
            f"http://localhost:{self.ports['inngest']}"
        )
        return values

    def as_line(self) -> str:
        return " ".join(f"{key}={value}" for key, value in self.env().items())


def _run_git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ("git", "-C", str(root), *args), check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def worktree_paths(root: Path) -> list[Path]:
    output = _run_git(root, "worktree", "list", "--porcelain")
    return [
        Path(line.removeprefix("worktree "))
        for line in output.splitlines()
        if line.startswith("worktree ")
    ]


def is_primary_worktree(root: Path) -> bool:
    common_dir = Path(_run_git(root, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    return common_dir.resolve() == (root / ".git").resolve()


def checkout_slug(root: Path) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", root.name.lower()).strip("-")
    return slug or hashlib.blake2b(str(root).encode(), digest_size=4).hexdigest()


def _path_hash(root: Path) -> str:
    return hashlib.blake2b(str(root).encode(), digest_size=4).hexdigest()


def _default_project_name(root: Path, primary: bool) -> str:
    if primary:
        return "infra"
    return f"living-kb-{checkout_slug(root)}-{_path_hash(root)}"


def _dotenv_values(root: Path) -> dict[str, str]:
    path = root / ".env"
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def explicit_values(root: Path, environ: Mapping[str, str] | None = None) -> dict[str, str]:
    values = _dotenv_values(root)
    environ = os.environ if environ is None else environ
    for name in EXPLICIT_ENV_VARS:
        if name in environ:
            values[name] = environ[name]
    return values


def _read_allocation(root: Path) -> Allocation | None:
    try:
        payload = json.loads((root / ALLOCATION_FILE_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    project_name = payload.get("project_name")
    raw_ports = payload.get("ports")
    if not isinstance(project_name, str) or not isinstance(raw_ports, dict):
        return None
    ports: dict[str, int] = {}
    for name in PORT_NAMES:
        value = raw_ports.get(name)
        if not isinstance(value, int) or isinstance(value, bool):
            return None
        ports[name] = value
    return Allocation(project_name=project_name, ports=ports)


def _write_allocation(root: Path, allocation: Allocation) -> None:
    payload = {
        "project_name": allocation.project_name,
        "ports": allocation.ports,
    }
    (root / ALLOCATION_FILE_NAME).write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _occupied_ports(root: Path) -> set[int]:
    occupied = set(BASE_PORTS.values())
    for path in worktree_paths(root):
        if path.resolve() == root.resolve():
            continue
        allocation = _read_allocation(path)
        if allocation is not None:
            occupied.update(allocation.ports.values())
    return occupied


def _port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("0.0.0.0", port))
        except OSError:
            return False
    return True


def _candidate_ports(index: int) -> dict[str, int]:
    return {name: base + PORT_STRIDE * index for name, base in WORKTREE_BASE_PORTS.items()}


def _candidate_start(root: Path) -> int:
    digest = hashlib.blake2b(str(root.resolve()).encode(), digest_size=2).digest()
    return int.from_bytes(digest, "big") % CANDIDATE_COUNT


def _allocate_ports(root: Path, primary: bool) -> Allocation:
    default_name = _default_project_name(root, primary)
    if primary:
        return Allocation(project_name=default_name, ports=dict(BASE_PORTS))
    occupied = _occupied_ports(root)
    existing = _read_allocation(root)
    if (
        existing is not None
        and existing.project_name == default_name
        and not (set(existing.ports.values()) & occupied)
    ):
        return existing
    start = _candidate_start(root)
    for offset in range(CANDIDATE_COUNT):
        candidate = _candidate_ports((start + offset) % CANDIDATE_COUNT)
        values = set(candidate.values())
        if values & occupied or any(not _port_is_free(port) for port in values):
            continue
        allocation = Allocation(project_name=default_name, ports=candidate)
        _write_allocation(root, allocation)
        return allocation
    raise RuntimeError(f"No free port block found for worktree {root}")


def _parse_port(value: str, name: str) -> int:
    try:
        port = int(value)
    except ValueError as error:
        raise ValueError(f"{name} must be an integer port, got {value!r}") from error
    if not 1024 <= port <= 65535:
        raise ValueError(f"{name} must be between 1024 and 65535, got {port}")
    return port


def build_plan(root: Path, environ: Mapping[str, str] | None = None) -> StackPlan:
    root = root.resolve()
    explicit = explicit_values(root, environ)
    primary = is_primary_worktree(root)
    allocation = _allocate_ports(root, primary)
    ports = dict(allocation.ports)
    for name, env_var in PORT_ENV_VARS.items():
        if env_var in explicit:
            ports[name] = _parse_port(explicit[env_var], env_var)
    project_name = explicit.get("COMPOSE_PROJECT_NAME") or allocation.project_name
    return StackPlan(project_name=project_name, ports=ports, explicit=explicit)


def write_env_file(plan: StackPlan, path: Path) -> None:
    lines = [f"{key}={shlex.quote(value)}" for key, value in plan.env().items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--write-env", type=Path)
    group.add_argument("--format", choices=("line", "shell", "json"), default=None)
    args = parser.parse_args(argv)
    plan = build_plan(args.root)
    if args.write_env is not None:
        write_env_file(plan, args.write_env)
        return 0
    output_format = args.format or "line"
    if output_format == "json":
        print(
            json.dumps(
                {
                    "project_name": plan.project_name,
                    "ports": plan.ports,
                    "env": plan.env(),
                },
                sort_keys=True,
            )
        )
    elif output_format == "shell":
        for key, value in plan.env().items():
            print(f"{key}={shlex.quote(value)}")
    else:
        print(plan.as_line())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
