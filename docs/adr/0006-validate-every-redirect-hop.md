# 0006 — Validate every redirect hop

- **Status:** Accepted
- **Date:** 2026-11-26

## Context

The first polite-fetching implementation set `follow_redirects=True` on the httpx client and checked robots.txt and the publications allowlist only for the URL in the event. A probe showed that an allowed article URL could redirect to an off-allowlist host, to `169.254.169.254` (the cloud metadata endpoint), or into a path disallowed by robots.txt, and that the unchecked URL was fetched and stored. A crawler that follows redirects automatically treats every response as trusted, even though a `Location` header is untrusted input from the publisher.

The hand-written robots parser also merged every matching `User-agent` group, ignored `Allow`, and used substring matching for agent names. That could block URLs that the publisher's rules allow, and made a specific group fail to override `*`.

A later revision added local wildcard matching by translating each `*` to `.*` in a regex. That parser was both incomplete and unsafe: it did not normalize percent-encoding on rules and targets alike, and a rule with six stars took 12 s to reject a 100-character path while thirty stars never finished. The match runs on the event loop, so a hostile or malformed robots.txt could stall ingestion.

## Decision

- Redirects are followed by `HttpxFetcher` itself, one hop at a time, with `follow_redirects=False` on the client and on each request. The number of redirects is bounded by `settings.max_redirects` for both article URLs and robots.txt URLs.
- Before requesting a hop, the fetcher requires an `http`/`https` scheme, checks the host against the publications allowlist with the same `assert_allowed` used by the pipeline, and checks that hop's URL against that host's robots rules. A failed check is a `PermanentError` and the hop is never sent. robots.txt redirects get the same scheme and allowlist checks; an over-limit robots chain stays retriable instead of allowing the article.
- The URL returned to the pipeline is the URL of the final response, not the URL from the event.
- Robots.txt parsing and matching are delegated to `protego`, Scrapy's RFC 9309 parser. It selects the matching `User-agent` group, applies longest match with `Allow` winning a tie, and handles `*`, trailing `$`, percent-encoding and literal path characters. Empty rules allow everything. Any robots 4xx other than 408/429 means "no rules"; 5xx and network errors remain retriable.

## Consequences

- An open redirect on an approved publication can no longer turn ingestion into a request to an unapproved host or an internal address. The allowlist is enforced at the point of the actual request, inside the `fetch-article` step.
- Each new host adds one robots.txt request, cached per host for the life of the process. A legitimate redirect to a new publisher domain requires adding that domain to `publications.yaml`.
- Article redirect chains longer than `max_redirects` fail permanently instead of looping or consuming unbounded time. Over-limit robots chains are retriable and never turn into allow-all.
- Protego adds a dependency, but replaces a hand-rolled parser and matcher with a vetted implementation. Its wildcard matcher is linear: the same 30-star rule and 3000-character path that did not finish in 100 s now matches in about 0.0002 s. Tests pin the behaviours a naive regex gets wrong, including anchoring, longest-match, encoding, case sensitivity and empty paths.
- A robots.txt redirect to another allowlisted host is followed by hand and the final body is cached under the article's host. A redirect off the allowlist is never requested.
- The robots cache still has no TTL and robots.txt bodies are not size-capped. Both are separate follow-ups; neither can make a forbidden hop be fetched, because hop checks run before the robots body is used. A body cap would also bound the work Protego has to parse.
