# Reviewing a task PR

You are the reviewer. You did not write this code, and your job is to find where it fails. The human decides the merge, and your report is what they decide from.

## Steps

1. **Load the contract.** Read the PR's spec (`docs/tasks/NNN-*.md`), the ADRs it links, and the full diff (`gh pr diff <n>`). Check out the branch.
2. **Confirm green.** Run `uv run ruff check . && uv run mypy && uv run pytest`. If anything fails, report it and stop at this step.
3. **Walk the acceptance checklist.** Mark every item in the spec's checklist **met**, **not met** or **partial**. Cite the evidence for each one: a file and line, or a test name. An item counts as met only with evidence.
4. **Attack the tests.** For each behaviour in the contract, plant a **mutant** in the implementation: flip a condition, drop a step, return early, change a boundary. Run the tests, then revert the mutant. A mutant that survives (tests still green) exposes a gap in the tests. Plant at least one mutant per contract behaviour, and record each as `mutant → killed | survived`.
5. **Check scope and design.**
   - Changes outside the spec's scope.
   - Edited contract tests.
   - Inline magic numbers that belong in `config.py`.
   - Real network or API calls in tests.
   - Missing `run_id` in logs from Inngest functions.
   - Behaviour the spec asks for that has no test.
6. **Report.** Post one PR review comment with `gh pr review <n> --comment --body-file <file>`, in this shape:

```markdown
## Verdict: ready | changes needed

### Checklist
- [x] <item> — <evidence>
- [ ] <item> — <what is missing>

### Mutants
| Mutant | Location | Result |
|---|---|---|

### Findings
1. **<severity: blocker | should-fix | nit>** <file:line> — <problem> → <suggested fix>
```

The review is done when every checklist item has a verdict with evidence, every contract behaviour has at least one mutant recorded, and the comment is posted.
