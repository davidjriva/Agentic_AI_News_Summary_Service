install:
	poetry install

run:
	poetry run python src/main.py

run-dry:
	poetry run python src/main.py --dry-run

serve:
	poetry run uvicorn src.server:app --reload --port 8000

test:
	poetry run pytest tests/ -v

install-launchd:
	cp deploy/com.agenticnews.service.plist ~/Library/LaunchAgents/
	launchctl load ~/Library/LaunchAgents/com.agenticnews.service.plist
