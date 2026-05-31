.PHONY: lint typecheck test run

lint:
	ruff check src/ tests/

typecheck:
	mypy src/

test:
	pytest

run:
	uvicorn reconciliation.infrastructure.api.main:app --reload --port 8000
