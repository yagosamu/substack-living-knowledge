# 0005 — Validate every redirect hop

- **Status:** Accepted
- **Date:** 2026-11-26

## Context

The first polite-fetching implementation set `follow_redirects=True` on the httpx client and checked robots.txt and the publications allowlist only for the URL in the event. A probe showed that an allowed article URL could redirect to an off-allowlist host, to `169.254.169.254` (the cloud metadata endpoint), or into a path disallowed by robots.txt, and that the unchecked URL was fetched and stored. A crawler that follows redirects automatically treats every response as trusted, even though a `Location` header is untrusted input from the publisher.

The hand-written robots parser also merged every matching `User-agent` group, ignored `Allow`, and used substring matching for agent names. That could block URLs that the publisher's rules allow, and made a specific group fail to override `*`.

## Decision

- Redirects are followed by `HttpxFetcher` itself, one hop at a time, with `follow_redirects=False` on the client and on each request. The number of redirects is bounded by `settings.max_redirects`.
- Before requesting a hop, the fetcher requires an `http`/`https` scheme, checks the host against the publications allowlist with the same `assert_allowed` used by the pipeline, and checks that hop's URL against that host's robots rules. A failed check is a `PermanentError` and the hop is never sent.
- The URL returned to the pipeline is the URL of the final response, not the URL from the event.
- Robots rules are evaluated with `urllib.robotparser.RobotFileParser`: the standard library already implements `Allow`, wildcards and empty rules. Groups are selected before parsing so a group matching our User-Agent wins over `*`, including when `*` is listed first. Any robots 4xx other than 408/429 means "no rules"; 5xx and network errors remain retriable.

## Consequences

- An open redirect on an approved publication can no longer turn ingestion into a request to an unapproved host or an internal address. The allowlist is enforced at the point of the actual request, inside the `fetch-article` step.
- Each new host adds one robots.txt request, cached per host for the life of the process. A legitimate redirect to a new publisher domain requires adding that domain to `publications.yaml`.
- Redirect chains longer than `max_redirects` fail permanently instead of looping or consuming unbounded time.
- `RobotFileParser` is stdlib and requires no new dependency. Group selection is done by us because the parser otherwise uses the first applicable group, which can let a wildcard group precede and shadow a specific one.
- The robots cache still has no TTL and robots.txt bodies are not size-capped. Both are separate follow-ups; neither can make a forbidden hop be fetched, because hop checks run before the robots body is used.
