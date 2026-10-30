.PHONY: setup start generate benchmark check studio studio-native studio-linux test-studio setup-video

UV := $(shell command -v uv 2>/dev/null || printf '%s' '.venv/bin/uv')
NODE := $(shell if test -x runtime/node/current/bin/node; then printf '%s' 'runtime/node/current/bin/node'; else command -v node; fi)

setup-video:
	.venv/bin/python scripts/setup_video.py

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

studio-linux:
	bash scripts/start_linux_docker.sh

test-studio:
	.venv/bin/python -m unittest discover -s studio/tests -v
	$(NODE) --test studio/tests/*.test.cjs

check:
	.venv/bin/python -m py_compile scripts/*.py studio/*.py studio/tests/*.py
	for script in studio/static/*.js; do $(NODE) --check "$$script"; done
	bash -n scripts/env.sh scripts/setup.sh scripts/start.sh scripts/setup_linux.sh scripts/start_linux_docker.sh "Start Image Studio.command" "Start Web Studio.command"
	UV_CACHE_DIR="$(CURDIR)/.cache/uv" $(UV) pip check --python .venv/bin/python
	if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then git diff --check && git diff --cached --check; fi
