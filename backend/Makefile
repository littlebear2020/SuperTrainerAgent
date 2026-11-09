install:
	uv sync

dev:
	PYTHONPATH=. uv run uvicorn app.gateway.app:app --host 0.0.0.0 --port 8001 --reload

gateway:
	PYTHONPATH=. uv run uvicorn app.gateway.app:app --host 0.0.0.0 --port 8001

test:
	CI=true PYTHONPATH=. uv run pytest tests/ -q -n auto --dist loadfile

test-serial:
	CI=true PYTHONPATH=. uv run pytest tests/ -v

# 2026-10-05 基建批：分层选择——剔掉 e2e/live 整文件门控用例（live 本就
# 零成本 skip，deselect 只是语义显式化）。完成定义仍是 make test 全量绿。
test-fast:
	CI=true PYTHONPATH=. uv run pytest tests/ -q -n auto --dist loadfile -m "not slow and not e2e and not live"

# 覆盖率可见性（不设阈值门槛，用于找防护盲区）；生成 htmlcov/index.html
test-cov:
	CI=true PYTHONPATH=. uv run pytest tests/ -q -n auto --dist loadfile \
		--cov=app --cov=deerflow --cov-report=term-missing --cov-report=html

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff check . --fix && uv run ruff format .
