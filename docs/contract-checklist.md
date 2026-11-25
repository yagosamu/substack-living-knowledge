# Contract checklist

Read this before writing contract tests for a task. Then read it again before committing them. Every item below is a mistake this project has already made: a contract that passed its own review and was later broken by a reviewer's mutant or by the real engine. The issue numbers point to where it happened.

A contract is only as strong as its weakest assertion. CI green, a passing answer key and "all my mutants killed" are not proof, because the author's mutants share the author's blind spots.

## 1. Use examples that discriminate

Pick inputs where the right and the wrong implementation give different answers.

- **Equal values hide which one was used.** Make every source of a field distinct. *(#6: the event title and the page title were identical, so "title from the event" survived.)*
- **Uniform inputs hide boundary rules.** *(#4: equal-length words could not tell "overlap reaches N" from "overlap fits in N".)*
- **Plain names hide transformations.** Use realistic names with separators. *(#5: `"parse"` had no hyphen, so a mutant that rewrote `-` to `_` survived. `"fetch-html"` kills it.)*
- **One input can't tell two derivations apart.** If a value must be derived from X and not Y, vary Y while holding X. *(#6: hashing the HTML instead of the text survived. Same body with different page chrome must give the same hash.)*

## 2. Assert the property, not a proxy for it

- **"Aware" is not "UTC".** Two aware datetimes compare equal by instant, so `== datetime(..., tzinfo=UTC)` and `utcoffset() is not None` both accept `-03:00`. Assert `utcoffset() == timedelta(0)`. *(#4)*
- **"Same shape" is not "fresh copy".** Mutate the returned value and assert the next read is unaffected. *(#5)*
- **One repetition does not prove "every time".** If a property must hold across replays, check at least two. *(#5: a cache that persisted across replays survived a single-replay test.)*
- **A config test that repeats the literal value protects nothing.** Assert what the value is for: the path loads, the URL connects. *(#18 → #6: `== "config/publications.yaml"` froze a path that did not exist.)*

## 3. Test the edges, not only the middle

- **Zero and the limits of every number:** `overlap = 0`, `size = 1`, an empty list, a single item. *(#21: `chunk_overlap=0` repeated a word.)*
- **"Empty" is rarely binary.** A paywall page extracts to `"Sign in"`: not empty, not an article. Define a threshold. *(#6)*
- **Separate the failure classes.** Malformed (truncated XML) ≠ wrong kind (HTML instead of a feed) ≠ valid but empty (a feed with no items). Each needs its own test. *(#4)*
- **Errors have classes too.** 4xx is permanent except 408/429; 5xx and timeouts are retriable. *(#6 → #7)*

## 4. Test against the real engine's behaviour, not the fake's

- **Fakes encode assumptions.** Before asserting how the engine behaves, check the engine (read the SDK, or run it). *(#5: ADR 0003 claimed a reused step name replays the first result. The SDK actually re-keys it to `name:1`.)*
- **Every behaviour the fake provides must also be provided by the production path,** with a test through the production adapter or entry point. *(#5: only the fake rejected duplicate names. #6: a `PermanentError` raised outside a step was never made non-retriable, because translation lived only inside steps.)*
- **Rules in an ADR need an enforcing test,** or they are wishes. *(#6: "step outputs are small" was violated by journaling the full HTML and every vector.)*

## 5. Seed mutants from this list, not from imagination

For every pipeline, plant at least these mutants, in addition to your own:

- move **each** side effect outside its step: fetch, embed, persist, emit, every one *(#6: only fetch and embed were tried, and persist/emit survived);*
- split one transaction into two;
- make each `PermanentError` raise site retriable, including raises outside steps;
- take each field from its other plausible source (event vs page, requested vs final URL);
- change the input of each derived value (a hash over the HTML vs over the text);
- drop the last partial batch, ignore the batch size, skip reordering;
- store or return a live object instead of a copy.

Then let the reviewer plant theirs. The reviewer's survivors, not yours, decide whether the contract is done.

## 6. Distrust the answer key

The throwaway implementation that proves a contract reachable shares its author's blind spots. In this project it had the same bug as the implementer more than once: a truncated feed accepted (#4), a live journal object returned (#5), the full HTML journaled (#6). A passing answer key proves the tests are reachable, never that they are strong.

## Before committing a contract

- [ ] Every field under test has a distinct source in the fixtures (section 1).
- [ ] Every assertion checks the property itself (section 2).
- [ ] Zero, empty, limits and each failure class have a test (section 3).
- [ ] Engine behaviour was checked against the real SDK, and every fake behaviour has a production-path test (section 4).
- [ ] Every mutant in section 5 was planted and killed.
- [ ] Every ADR rule the task touches has an enforcing test (section 4).
