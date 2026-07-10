# Live Polling for Run Status

**Date:** 2026-04-15
**Status:** Approved

## Overview

Add a JavaScript poller to the dashboard so the run history table updates in place — no manual refresh required. When a run is triggered (or already in progress on page load), an in-progress row appears immediately and resolves to its final state when the pipeline finishes.

## Goals

- Run history table updates in place without a page reload
- In-progress row appears immediately on click, before the first poll response
- Polling starts automatically on page load if a run is already in progress (e.g. launchd-triggered)
- Buttons and running badge reflect live state

## Non-Goals

- WebSockets / SSE — polling at 3s is sufficient for a personal tool
- New backend endpoints — existing `/run` (POST) and `/runs` (GET) are sufficient
- Per-article progress updates

## Architecture

Frontend-only change. The Jinja2 template continues to render the initial page server-side. Once a run is in progress, JS takes full ownership of the run table and running badge.

```
click → POST /run → {run_id}
         ↓
   optimistic row inserted at top of table
   running badge shown, buttons disabled
         ↓
   setInterval 3s → GET /runs → renderRuns(runs)
         ↓  (repeats while any run has status "running")
   running badge hidden, buttons re-enabled
   table reflects final state of completed run
```

If the server renders `running: true` on initial page load, polling starts automatically without a click.

## Components

Three JS functions added to `dashboard.html.jinja2` in a `<script>` block before `</body>`:

### `triggerRun(clean)`
- POSTs to `/run` or `/run?clean=true`
- On 409 response: shows a brief inline "already running" message, does not insert a duplicate row
- On success: inserts an optimistic row at the top of `<tbody>` using the `run_id` from the response, calls `showRunning()`, calls `startPolling()`
- On other errors: re-enables buttons, hides badge

### `startPolling()`
- Starts a `setInterval` at 3000ms
- Each tick: `fetch('/runs')` → `renderRuns(data)`
- Stops the interval when no run in the response has `status: "running"`
- On fetch error: swallows silently and retries next tick
- Tracks the active `run_id` (set by `triggerRun`). If a poll response does not yet contain that `run_id` (race between background thread DB insert and first poll tick), `renderRuns` re-inserts the optimistic row at the top rather than wiping it.

### `renderRuns(runs)`
Rebuilds `<tbody>` innerHTML to mirror the Jinja2 row structure:

| Column | Running | Completed (success) | Completed (error) |
|--------|---------|---------------------|-------------------|
| Started | ISO timestamp | ISO timestamp | ISO timestamp |
| Status | `badge-running` | `badge-success` | `badge-error` + error message |
| Articles | `—` | count | `—` |
| Duration | `—` | computed from `started_at` / `completed_at` | computed |
| Newsletter | `—` | `<a href="/runs/{id}">View</a>` | `—` |

Duration is computed in JS: `(new Date(completed_at) - new Date(started_at)) / 1000`, formatted as `Xm Ys` or `Xs`.

Also updates running badge visibility and button disabled state based on whether any run is `"running"`.

### Helper: `showRunning()` / `stopRunning()`
Small helpers that toggle the running badge and button disabled state, called by `triggerRun` and `startPolling`.

## Page Load Auto-Start

The template already passes `running` from the server. Add inline JS:

```html
{% if running %}
<script>startPolling();</script>
{% endif %}
```

This covers the case where launchd triggered a run before the user opened the dashboard.

## Error Handling

| Scenario | Behaviour |
|----------|-----------|
| POST `/run` returns 409 | Show "A run is already in progress" near the buttons for 3s, start polling |
| POST `/run` returns other error | Re-enable buttons, hide badge |
| GET `/runs` fails mid-poll | Swallow error, retry on next tick |
| Run completes with `status: "error"` | Row shows red `Error` badge + error message, polling stops |

## Files Changed

- `templates/dashboard.html.jinja2` — replace `<form>` buttons (already done), add `renderRuns`, `startPolling`, `triggerRun`, page-load auto-start

No backend changes required.
