# CelesTrak caching design

Design decisions for [issue #59](https://github.com/code-lab-org/tatc-ai/issues/59).
The client implements these decisions with a persistent SQLite cache. Automated regression tests exercise the public client functions.

## Agreed behavior

- Cache GP orbital data and SATCAT satellite-name searches, with separate expiry rules.
- Persist the cache across server restarts and container replacement.
- When GP data expires, require a successful refresh before using it for a new calculation. Report a refresh failure explicitly. Unexpired entries remain usable.
- Unexpired cached results remain usable while upstream requests are paused.
- A recognized temporary rate-limit block starts a two-hour system-wide lock. After the lock, downloads become eligible again.
- Service failures, including HTTP 5xx responses, timeouts, connection failures, and unusable upstream responses, start a ten-minute system-wide lock. This cooldown does not make individual HTTP requests wait ten minutes. Keep short HTTP request timeouts.
- Use a ten-second connection timeout and thirty-second read-inactivity timeout for both GP and SATCAT requests. These are initial engineering settings, not CelesTrak requirements or strict total download deadlines.
- Report non-200 responses through an explanatory app error and diagnostic server logs. During a lock, report the saved reason and retry eligibility time without making another upstream request.
- Contacting CelesTrak is deferred. This change does not add automatic external incident reports or require a contact workflow.
- Cap MCP container logs at approximately 30 MB per container using Docker rotation, with three files of approximately 10 MB each. Apply the limit in development and deployment. Retention depends on log volume, and container replacement may remove logs sooner.
- After a temporary rate-limit lock expires, allow one upstream request only when someone needs uncached data. Do not poll in the background. Another recognized temporary rate limit starts a fresh two-hour lock.
- Cache valid SATCAT satellite-name search responses, including valid no-match results, for 12 hours. Cache GP responses for two hours.
- If the persistent cache is unavailable, stop downloads and explain the storage failure in the app instead of bypassing the cache.
- Limit cached response data to approximately 50 MB, separately from the log limit. Remove expired entries first. If the cache remains full, report the capacity issue instead of evicting unexpired responses and downloading them again later.
- Other request failures are reported to the researcher through the app and stop the current lookup. Do not add a blanket two-hour suppression of each failed query or automatic retries after these HTTP failures. Local argument corrections and successful no-match follow-up searches remain distinct from retries after an upstream HTTP failure.
- Automated regression tests are part of the implementation deliverable and should be added to the repository alongside the existing MCP server tests. The detailed cases below describe the proposed scope.

## Recovery behavior

Temporary rate-limit blocks use automatic cooldown recovery rather than indefinite operator-controlled suspension. Report failures through the app and diagnostic server logs. Automatic maintainer email is outside the agreed scope.

The user chose a ten-minute lock for outages and other service failures because a two-hour outage pause would unnecessarily delay recovery. Persist cooldown eligibility separately from cached response expiry. Serve unexpired cached results during either lock. After a lock expires, the next needed upstream request is the recovery attempt; do not schedule background polling. Another outage starts a new ten-minute lock, while a recognized temporary rate limit starts a two-hour lock. A shorter outage cooldown must not shorten an active rate-limit lock.

The [CelesTrak usage policy](https://celestrak.org/usage-policy.php) states that GP data updates every two hours and SATCAT updates manually once or twice daily. It requires machine clients to stop querying after any non-200 response and report it to a human. The [GP FAQ, under "How can I avoid getting blocked?"](https://celestrak.org/NORAD/documentation/gp-data-formats.php) explicitly states that temporary blocks are removed within two hours after the offending process stops. Its linked [403 example](https://celestrak.org/images/403-example.png) likewise describes restoration after excessive downloads cease for two hours. This supports the proposed two-hour lock for recognized temporary rate-limit blocks, but does not establish recovery timing for firewall blocks or other errors.

## Implementation details

- Persist the lock separately from response entries, and check it across all application CelesTrak requests. Coordinate concurrent requests so recovery and identical cache misses do not produce duplicate fetches.
- Recognize HTTP 429 and CelesTrak's explicit temporary-rate-limit HTTP 403 messages; do not classify every 403 as a temporary rate limit.
- Distinguish local validation errors and successful no-match responses from actual upstream non-200 responses. Existing tools accept identifiers and search terms, not arbitrary CelesTrak URLs, so an LLM cannot repair a broken server endpoint through its inputs.
- Use SQLite at `CELESTRAK_CACHE_PATH`, defaulting to `~/.cache/tatc-ai/celestrak.sqlite3` for native runs. Compose mounts a dedicated volume at `/data/celestrak`. Hash the endpoint and sorted upstream query parameters for cache keys. Keep ranking and output limits outside the cached raw responses, so changing a search result limit does not repeat a download.
- Serialize upstream fetches with a file lock shared across threads and processes that use the same cache path. This protects one application's shared store; it does not coordinate independent caches on different machines sharing a public IP.
- Read fresh cached responses without waiting for that network lock. Cache-hit activity updates are best effort if SQLite is busy, so diagnostics do not delay usable cached data.
- Bound cached response bodies to 50 MiB in total and bound individual response downloads. SQLite has additional bounded page/index/journal overhead, so the response-data budget is not an exact total disk-space cap.
- If a downloaded response cannot fit, suppress another download of that same query until existing data expires, while allowing smaller unrelated responses to fit. Keep at most 1,024 such capacity-failure records; when that metadata is full, new misses fail closed until records expire. Cached responses remain usable.
- Distinguish unusable upstream responses from legitimate HTTP 200 no-data responses. An unavailable satellite is not by itself evidence of a provider outage.
- Exercise the public client functions using the regression cases below.
- Suppress repeated diagnostic log entries as described below.

## Established constraints

The configured MCP service runs one Python process and handles synchronous tools in worker threads. Concurrent calls can reach the cache. The deployment mounts separate OAuth and CelesTrak cache volumes. The client owns the application's direct CelesTrak HTTP calls.

HTTP request timeouts govern the network fetch, not subsequent TAT-C calculations. Requests supports separate connection and read timeouts; its read timeout measures inactivity between received bytes, not a deadline for the whole download. These values do not establish a strict total wall-clock deadline. See [Requests timeout documentation](https://requests.readthedocs.io/en/latest/user/advanced/#timeouts).

Preserve the distinction between cache age since retrieval and the epoch of the orbital elements. A recently retrieved record may still contain older elements, and existing scientific validation remains necessary.

The [GP documentation FAQ](https://celestrak.org/NORAD/documentation/gp-data-formats.php) says rate-limit blocks include a custom HTTP 403 response explaining the cause. It warns that repeated 403, 301, and 404 responses can lead to a firewall block requiring CelesTrak's manual review. Temporary rate-limit blocks have documented two-hour recovery after excessive requests stop. This recovery period is separate from the two-hour GP data update interval. Other processes sharing the public IP can keep the offending traffic active even while this application is paused.

## Agreed app reporting

For each non-200 response, return a structured tool error that lets the chat explain the HTTP status, a safe summary of the cause, whether downloads are paused, and the retry eligibility time when known. Record diagnostic details in server logs. During a rate-limit lock, serve unexpired cached results or return the saved pause reason without sending another request. Do not follow redirects or continue alternate-query fallbacks after the error. Automatic maintainer email and automatic contact with CelesTrak are not agreed requirements.

CelesTrak's requirement is to report the failure to a human for investigation, not to send each incident to CelesTrak. Its GP FAQ suggests contacting CelesTrak when a block remains unexplained and supplying a screenshot of the error, including the affected IP address. Such escalation is distinct from the app's routine error reporting. No automatic external reporting is needed for the agreed app-and-log behavior.

For diagnosis, retain the failed URL and parameters, HTTP status, relevant response headers, a bounded response-body excerpt, UTC failure time, and recent request/cache history. These distinguish an incorrect URL, repeated downloads, a provider outage, and a possible IP block.

Keep at most 64 recent activity records in SQLite, containing event times, event types, and shortened query hashes. Include the latest 12 records in a failure's diagnostic log rather than writing a log line for every cached lookup. Record successful recovery once. Omit invalid HTTP 200 bodies and raw validation values from app errors and logs.

## Agreed log retention

Both Compose definitions explicitly configure the MCP service's JSON logging driver and rotation limits, overriding the Docker host's logging defaults for this service.

The user accepted the approximately 30 MB cap. Send diagnostics to the MCP container's standard error and explicitly configure Docker log rotation for that service in both Compose files. Keep at most three files of approximately 10 MB each, for approximately 30 MB of MCP logs per container, deleting the oldest automatically. This limit covers all MCP container output, not only CelesTrak diagnostics. It does not guarantee a number of retained days, and container replacement may remove logs sooner. Do not add a second application-managed log archive.

Record the upstream failure and lock start once, then recovery or a new failure when an actual upstream request occurs. Repeated requests served from the saved lock state should still receive an app explanation without repeating the full diagnostic log entry. Bound response excerpts and omit credentials and unrelated request content.

The [Docker JSON logging documentation](https://docs.docker.com/engine/logging/drivers/json-file/) documents the unlimited default and the `max-size` and `max-file` rotation controls.

## Developer verification

Add automated regression tests during implementation and run them before submitting the change. They do not add researcher actions or runtime confirmation prompts. Exercise the existing public GP-fetch and SATCAT-search functions with controlled HTTP responses and a controllable clock. Cover cache reuse and expiry, one fetch for simultaneous identical requests, persistence across a fresh interpreter, the two-hour temporary-rate-limit lock, the ten-minute service-failure lock, concurrent recovery, unavailable or full storage, and the agreed reporting behavior. Advancing the test clock avoids waiting for actual cooldowns or making live requests to CelesTrak.
