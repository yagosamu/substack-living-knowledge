# 0004 — Per-worktree development stacks

- **Status:** Accepted
- **Date:** 2026-11-25

## Context

Several worktrees of this repository run at the same time. They used one Compose project name and one set of host ports, so a second `make start` failed on a busy port, and `make stop` in one worktree could recreate or remove another worktree's containers and volume. Workers were compensating with manual port overrides, which is easy to forget and makes `DATABASE_URL` and the Inngest app URL drift apart.

## Decision

- The primary worktree, detected by comparing `git rev-parse --git-common-dir` with its own `.git`, keeps the existing Compose project `infra` and ports 5433/8081/8288/8000.
- A linked worktree gets a Compose project name derived from its directory name plus a short hash of its absolute path, and a port block derived deterministically from that path. If a candidate block is taken, the next one is tried; the chosen block is persisted in the gitignored `.dev-stack.local.json` so later starts reuse it. Other worktrees' persisted blocks are read but never written.
- `infra/dev-stack.sh` writes a gitignored `.dev-stack.env` with the effective project name, ports, `DATABASE_URL` and `INNGEST_BASE_URL`. Compose, the API process and pytest all consume the same values.
- Explicit shell or `.env` values for the project name, ports, database URL and Inngest base URL win over derived values.

## Consequences

- Two worktrees can start, migrate and test at the same time without sharing containers, networks, volumes or ports; stopping or resetting one never touches another.
- A worktree's ports stay stable across restarts, including while its stack is running.
- Local generated files are machine-specific and gitignored; moving a worktree changes its derived identity and may allocate a new block.
- If another process takes a persisted port, the operator either frees it or sets an explicit override.
- `.env.example` must leave the per-worktree ports and URLs commented out. Since the README tells people to copy it to `.env`, pinning 5433/8081/8288/8000 there would turn an explicit override into the default and silently restore the original collision.
- `Settings` reads `.env` first and the generated `.dev-stack.env` second, so generated values win over `.env` while real shell variables still win over both. This order is load-bearing: flipping it makes the app and tests use stale ports while Compose uses derived ones.
- Allocation is check-then-act with no lock. Two worktrees whose hashes land on the same free block and start at the same instant can both choose it before either writes its allocation file or Docker binds the ports. The probability is about 1/512 per pair; the escape hatch is an explicit port override, and a lock would be the next step if this is ever observed.
- The dev-only allocation numbers live with the dev tooling, not in the application's `Settings`, because they describe the local environment rather than application behaviour.
