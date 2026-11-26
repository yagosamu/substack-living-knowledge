#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 2 ]; then
  printf 'usage: prove-concurrent-stacks.sh <worktree-a> <worktree-b> [output-dir]\n' >&2
  exit 2
fi

worktree_a="$(cd "$1" && pwd)"
worktree_b="$(cd "$2" && pwd)"
output_dir="${3:-/tmp/living-kb-stack-proof}"
mkdir -p "$output_dir"

for worktree in "$worktree_a" "$worktree_b"; do
  common="$(git -C "$worktree" rev-parse --path-format=absolute --git-common-dir)"
  if [ "$(realpath "$common")" = "$(realpath "$worktree/.git")" ]; then
    printf 'refusing to start a stack in the primary checkout: %s\n' "$worktree" >&2
    exit 1
  fi
done

stack_pids=()
test_pids=()
cleanup() {
  set +e
  for pid in "${test_pids[@]}"; do
    kill -TERM "$pid" 2>/dev/null
  done
  for pid in "${stack_pids[@]}"; do
    kill -TERM -- "-$pid" 2>/dev/null
  done
  sleep 2
  for worktree in "$worktree_a" "$worktree_b"; do
    (cd "$worktree" && make reset) >> "$output_dir/cleanup.log" 2>&1
  done
  set -e
}
trap cleanup EXIT

start_stack() {
  local name="$1" root="$2"
  python3 - "$root" "$output_dir/$name-start.log" "$output_dir/$name-start.pid" <<'PY'
from pathlib import Path
import subprocess
import sys

root, log, pidfile = sys.argv[1:]
with open(log, "w", encoding="utf-8") as output:
    process = subprocess.Popen(
        ["make", "start"],
        cwd=root,
        stdout=output,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
Path(pidfile).write_text(str(process.pid), encoding="utf-8")
PY
  local pid
  pid="$(cat "$output_dir/$name-start.pid")"
  stack_pids+=("$pid")
}

wait_for_api() {
  local name="$1" pid
  pid="$(cat "$output_dir/$name-start.pid")"
  for _ in $(seq 1 240); do
    if grep -q "Uvicorn running on" "$output_dir/$name-start.log"; then
      return 0
    fi
    if ! kill -0 "$pid" 2>/dev/null; then
      printf '%s exited before the API started\n' "$name" >&2
      tail -n 60 "$output_dir/$name-start.log" >&2
      return 1
    fi
    sleep 1
  done
  printf 'timed out waiting for %s API\n' "$name" >&2
  tail -n 60 "$output_dir/$name-start.log" >&2
  return 1
}

env_value() {
  local worktree="$1" key="$2"
  (set -a; . "$worktree/.dev-stack.env"; set +a; printf '%s\n' "${!key}")
}

resource_names() {
  local kind="$1" project="$2"
  if [ "$kind" = "containers" ]; then
    docker ps --filter "label=com.docker.compose.project=$project" --format '{{.Names}}' | sort
  else
    docker volume ls --filter "label=com.docker.compose.project=$project" --format '{{.Name}}' | sort
  fi
}

assert_disjoint() {
  local label="$1" first="$2" second="$3" overlap
  overlap="$(comm -12 <(printf '%s\n' "$first") <(printf '%s\n' "$second") | sed '/^$/d')"
  if [ -n "$overlap" ]; then
    printf 'shared %s: %s\n' "$label" "$overlap" >&2
    return 1
  fi
}

infra_before="$(docker ps --filter label=com.docker.compose.project=infra --format '{{.Names}} {{.Status}}' | sort)"

start_stack a "$worktree_a"
start_stack b "$worktree_b"
wait_for_api a
wait_for_api b

project_a="$(env_value "$worktree_a" COMPOSE_PROJECT_NAME)"
project_b="$(env_value "$worktree_b" COMPOSE_PROJECT_NAME)"
containers_a="$(resource_names containers "$project_a")"
containers_b="$(resource_names containers "$project_b")"
volumes_a="$(resource_names volumes "$project_a")"
volumes_b="$(resource_names volumes "$project_b")"

[ "$project_a" != "$project_b" ]
[ -n "$(printf '%s' "$containers_a" | tr -d '\n')" ]
[ -n "$(printf '%s' "$containers_b" | tr -d '\n')" ]
[ -n "$(printf '%s' "$volumes_a" | tr -d '\n')" ]
[ -n "$(printf '%s' "$volumes_b" | tr -d '\n')" ]
assert_disjoint containers "$containers_a" "$containers_b"
assert_disjoint volumes "$volumes_a" "$volumes_b"

for key in POSTGRES_PORT ADMINER_PORT INNGEST_PORT API_PORT; do
  value_a="$(env_value "$worktree_a" "$key")"
  value_b="$(env_value "$worktree_b" "$key")"
  if [ "$value_a" = "$value_b" ]; then
    printf 'shared %s: %s\n' "$key" "$value_a" >&2
    exit 1
  fi
done

(cd "$worktree_a" && make test > "$output_dir/a-test.log" 2>&1) &
test_pids+=("$!")
(cd "$worktree_b" && make test > "$output_dir/b-test.log" 2>&1) &
test_pids+=("$!")

status_a=0
status_b=0
wait "${test_pids[0]}" || status_a=$?
wait "${test_pids[1]}" || status_b=$?

infra_after="$(docker ps --filter label=com.docker.compose.project=infra --format '{{.Names}} {{.Status}}' | sort)"
if [ "$infra_before" != "$infra_after" ]; then
  printf 'primary checkout stack changed\n' >&2
  exit 1
fi

printf 'worktree A: %s\n' "$worktree_a"
printf 'worktree B: %s\n' "$worktree_b"
printf 'project A: %s\n' "$project_a"
printf 'project B: %s\n' "$project_b"
for key in POSTGRES_PORT ADMINER_PORT INNGEST_PORT API_PORT; do
  printf '%s: A=%s B=%s\n' "$key" "$(env_value "$worktree_a" "$key")" "$(env_value "$worktree_b" "$key")"
done
printf 'containers A: %s\n' "$(printf '%s' "$containers_a" | tr '\n' ' ')"
printf 'containers B: %s\n' "$(printf '%s' "$containers_b" | tr '\n' ' ')"
printf 'volumes A: %s\n' "$(printf '%s' "$volumes_a" | tr '\n' ' ')"
printf 'volumes B: %s\n' "$(printf '%s' "$volumes_b" | tr '\n' ' ')"
printf 'primary checkout stack unchanged: %s\n' "${infra_after:-none}"
printf 'test status: A=%s B=%s\n' "$status_a" "$status_b"
printf 'logs: %s\n' "$output_dir"

if [ "$status_a" -ne 0 ] || [ "$status_b" -ne 0 ]; then
  printf 'one or both test suites failed\n' >&2
  tail -n 40 "$output_dir/a-test.log" >&2
  tail -n 40 "$output_dir/b-test.log" >&2
  exit 1
fi
