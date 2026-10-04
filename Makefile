.PHONY: setup start generate benchmark check

setup:
	./scripts/setup.sh

start:
	./scripts/start.sh

generate:
	.venv/bin/python scripts/generate.py

benchmark:
	.venv/bin/python scripts/generate.py --runs 2 --label benchmark

check:
	.venv/bin/python -m py_compile scripts/*.py
	bash -n scripts/env.sh scripts/setup.sh scripts/start.sh
	UV_CACHE_DIR="$(CURDIR)/.cache/uv" uv pip check --python .venv/bin/python
	git diff --check
	git diff --cached --check
