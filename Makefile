.PHONY: setup start generate benchmark check studio studio-native test-studio

setup:
	./scripts/setup.sh

start:
	./scripts/start.sh

generate:
	.venv/bin/python scripts/generate.py

benchmark:
	.venv/bin/python scripts/generate.py --runs 2 --label benchmark

studio:
	./"Start Web Studio.command"

studio-native:
	./"Start Web Studio.command" --native

test-studio:
	.venv/bin/python -m unittest discover -s studio/tests -v

check:
	.venv/bin/python -m py_compile scripts/*.py studio/*.py studio/tests/*.py
	for script in studio/static/*.js; do node --check "$$script"; done
	bash -n scripts/env.sh scripts/setup.sh scripts/start.sh "Start Image Studio.command" "Start Web Studio.command"
	UV_CACHE_DIR="$(CURDIR)/.cache/uv" uv pip check --python .venv/bin/python
	git diff --check
	git diff --cached --check
