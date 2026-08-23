# Candidate Pool Bulk Selection P0 Implementation Plan

1. Add the reusable Candidate Pool selection domain reducer and its focused
   unit tests before wiring the view.
2. Make Candidate Member list limits explicit in the Web API/query keys, then
   implement one-page-at-a-time cursor navigation and page-size selection.
3. Wire the reducer into Candidate Result: row/header checkboxes, clear page,
   clear all, ALL_MATCH, accurate counts, accessible controls, and conflict
   preservation.
4. Extend the Campaign add DTO/API/client with the backward-compatible
   ALL_MATCH selection form.
5. Raise only Candidate Member list maximum to 200 and add endpoint coverage.
6. Add bounded repository/service helpers for ALL_MATCH exclusion validation,
   ambiguity preflight, source keyset reading, and chunked Campaign writes.
7. Add HTTP/service coverage for security, idempotency, audit, duplicate/rejoin
   behavior, explicit compatibility, ALL_MATCH, and bounded processing.
8. Run formatting, targeted suites, static checks, full requested relevant
   suites, `git diff --check`, inspect the final delta, and create one atomic
   commit only if every required gate passes.
