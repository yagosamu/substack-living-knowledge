# 0002 — Implementer model selection

- **Status:** Accepted
- **Date:** 2026-11-21

## Context

ADR 0001 delegates implementation to coding agents and gates every change with contract tests, CI and a reviewer from a different model family. That leaves open which model implements by default. To decide on evidence, task #4 (the pure building blocks: identity, publications allowlist, chunking and feed parsing) went out as a bake-off. Each of three OpenCode Go models got the identical issue and contract tests, in its own worktree branched from the contract branch:

- Kimi K3.1 Pro (`opencode-go/kimi-k3.1-pro`), PR #15
- DeepSeek V4 Pro (`opencode-go/deepseek-v4-pro`), PR #16
- GLM 5.3 (`opencode-go/glm-5.3`), PR #17

Each PR was reviewed by Claude Sonnet following `docs/review.md`: acceptance checklist with evidence, mutation testing, and scope and design checks.

## Results

| | Kimi K3.1 Pro | DeepSeek V4 Pro | GLM 5.3 |
|---|---|---|---|
| Push → green CI cycles | 1 | 2 | 1 |
| Acceptance checklist | 6 met, 1 partial | **7 met** | 6 met, 1 partial |
| Tests added beyond the contract | 0 | **7** | 0 |
| Mutants planted / non-equivalent survivors | 45 / 10 | 41 / **5** | 43 / 15 |
| Survivors attributable to the implementation | few | 3 (1 should-fix) | 9 |
| Scope discipline | minimal | one unrequested behaviour (drops empty-title entries) | added untested extras: Atom support, ISO-date and `dc:date` fallbacks |
| Feed parsing | stdlib `xml.etree` | `feedparser` + hand-written stubs | stdlib `xml.etree` |
| Contract files unchanged | yes | yes | yes |

The first review (Kimi's) ran before the reviewer was told to separate **contract gaps** (mutants that survive because the contract tests are too weak) from gaps in the implementation. By their names, most of Kimi's 10 survivors are contract gaps.

## Decision

- **DeepSeek V4 Pro is the default implementer.** It was the only model that tested its own code beyond the contract and met the whole checklist, and it left the fewest surviving mutants. Its extra CI cycle is cheap next to that.
- **Kimi K3.1 Pro is the fallback.** It was the leanest and fastest, a good fit for small, tightly specified tasks.
- **GLM 5.3 is not used by default.** It shipped behaviour nobody asked for, and nothing protects that code. This failure mode grows with task size.
- **The choice is re-evaluated** after the first fan-out of I/O-bound tasks (#7–#11), because one task of pure functions is a small sample.

## Consequences

- The bake-off also tested the contract. Every review found contract gaps, which were then closed in contract v2:
  - UTC was not really enforced, because aware datetimes compare equal by instant;
  - malformed XML passed as an empty feed;
  - the overlap boundary was ambiguous ("reach" vs "fit" `chunk_overlap`);
  - `notes` and title stripping were unprotected.
- Mutation review proved more informative than CI status: all three PRs were green on CI.
- Model selection is pinned per worktree with an untracked `opencode.json` (`"model": "opencode-go/<id>"`). Orca cannot pass `--model` to OpenCode running in WSL, and models under the plain `opencode/` provider are pay-as-you-go, outside the OpenCode Go plan.
- The reviewer is itself fallible. One review counted a killed mutant as equivalent, and another blamed the implementer for a dependency added by the contract commit. Its findings are evidence for the human's merge decision, not a verdict.
