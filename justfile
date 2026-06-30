# Agentic AI News Summary Service — task runner
# Install `just`: https://github.com/casey/just  (macOS: `brew install just`)
# Run `just` with no args to list all recipes.

# Port the FastAPI dashboard listens on
serve_port := "8000"
# Local llama.cpp server settings (must match LOCAL_LLM_URL in .env — :8089)
llm_port := "8089"
home_dir := env_var("HOME")
# llama.cpp server binary (not on PATH; lives in the local build)
llama_bin := home_dir / "llama-cpp/build/bin/llama-server"
# GGUF model — defaults to news-agent-14b (symlinked from the Ollama blob store).
# Override: `just llama model=/path/to.gguf` or export LLAMA_GGUF.
llm_model := env_var_or_default("LLAMA_GGUF", home_dir / "llama-cpp/models/news-agent-14b.gguf")

# Default: show available recipes
default:
    @just --list

# --- Setup ------------------------------------------------------------------

# Install Python dependencies
install:
    poetry install

# --- Database (Supabase Postgres + Alembic) ---------------------------------

# Apply all pending migrations to the database
migrate:
    poetry run alembic upgrade head

# Autogenerate a new migration from model changes: `just makemigration "add foo"`
makemigration message:
    poetry run alembic revision --autogenerate -m "{{message}}"

# Show current migration revision
db-current:
    poetry run alembic current

# One-time backfill of the legacy SQLite data into Postgres
backfill:
    poetry run python -m scripts.migrate_sqlite_to_pg

# --- Local LLM (llama.cpp) --------------------------------------------------

# Start the local llama.cpp server on :{{llm_port}} (the port the app expects).
# Unloads Ollama's copy first — both can't hold the 14B in 24GB unified memory —
# and uses -c 8192 (larger contexts OOM the Metal GPU alongside the weights).
# Usage: `just llama`  or  `just llama model=/path/to.gguf`
llama model=llm_model:
    -ollama stop news-agent-14b 2>/dev/null
    {{llama_bin}} -m {{model}} --alias news-agent-14b --port {{llm_port}} -ngl 99 -c 8192 --jinja --temp 0 --repeat-penalty 1.0 --flash-attn on -np 1

# --- App --------------------------------------------------------------------

# Start the dashboard server (auto-reload) on :{{serve_port}}
serve:
    poetry run uvicorn src.server:app --reload --port {{serve_port}}

# Full pipeline run (fetches, processes, ranks, emails)
run:
    poetry run python -m src.main

# Dry run: skip email, print HTML to stdout
run-dry:
    poetry run python -m src.main --dry-run

# Clean run: delete seen_articles from the past LOOKBACK_HOURS before fetching
run-clean:
    poetry run python -m src.main --clean

# Bring up everything for local dev: migrate, then serve
# (start `just llama` and your Supabase access separately)
dev: migrate serve

# --- Deploy -----------------------------------------------------------------

# Register launchd scheduling (macOS, 7 AM + 6 PM daily)
install-launchd:
    cp deploy/com.agenticnews.service.plist ~/Library/LaunchAgents/
    launchctl load ~/Library/LaunchAgents/com.agenticnews.service.plist

# --- Tests ------------------------------------------------------------------

# Run the full test suite (DB tests need Docker running for testcontainers)
test:
    poetry run pytest -q

# Run a single test file or node: `just test-one tests/test_db.py`
test-one target:
    poetry run pytest {{target}} -v
