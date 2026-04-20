# tqdm Pipeline Progress Design

**Date:** 2026-04-15
**Status:** Approved

## Goal

Add tqdm progress bars to the CLI pipeline so the operator can see exactly where the run is and how much work remains — without touching the frontend or server path.

## Scope

Two bars only:
1. **Stage bar** in `main.py` — 5 steps, one per pipeline stage
2. **Article bar** in `processor.py` — one tick per LLM call, updating in real time as concurrent futures complete

Everything else (`fetcher.py`, `ranker.py`, `renderer.py`, `emailer.py`) is unchanged.

## Stage Bar (`main.py`)

- Initialize a single `tqdm` bar with `total=5` before the pipeline begins
- Call `bar.set_description(...)` and `bar.update(1)` at the start of each stage
- Replace `log.info` stage-boundary calls with `tqdm.write(...)` so output doesn't corrupt the bar
- `leave=True` so the completed bar stays visible after the run

Stages and their labels:
| # | Label |
|---|-------|
| 1 | Fetching articles |
| 2 | Processing with Claude / local LLM |
| 3 | Ranking |
| 4 | Rendering |
| 5 | Sending email (or Dry-run) |

## Article Bar (`processor.py`)

- Switch from `executor.map(lambda ...)` to `executor.submit` + `concurrent.futures.as_completed`
- Wrap `as_completed(futures)` in `tqdm(total=len(articles), desc="Articles", unit="art")`
- Collect results as futures complete; ordering is irrelevant since `ranker.py` sorts by `rank_score`
- Bar lives entirely inside `process_articles()` — no interface changes

## Dependency

Add `tqdm` to `pyproject.toml` under `[tool.poetry.dependencies]`.

## What Does NOT Change

- `logging.basicConfig` stays in `main.py` (used by server/background runs)
- Frontend, server, and launchd paths are unaffected
- The article dict schema contract is unchanged
- All existing tests remain valid — no mocking changes required
