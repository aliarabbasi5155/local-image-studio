.PHONY: help install start start-native start-docker stop status pin logs update uninstall comfyui generate benchmark test check

PYTHON := .venv/bin/python
UV := $(shell command -v uv 2>/dev/null || if test -x .cache/bin/uv; then printf '%s' '.cache/bin/uv'; else printf '%s' '.venv/bin/uv'; fi)
NODE := $(shell if test -x runtime/node/current/bin/node; then printf '%s' 'runtime/node/current/bin/node'; else command -v node; fi)

help:
	@echo "Local Image Studio"
	@echo "  make install        Install or repair everything (same as ./install.sh)"
	@echo "  make start          Start the studio and print its address and PIN"
	@echo "  make start-docker   macOS: serve the website from Docker Desktop instead"
	@echo "  make start-native   Serve the website without Docker"
	@echo "  make status         Show what is running"
	@echo "  make stop           Stop everything until the next login"
	@echo "  make logs           Follow the website and helper logs"
	@echo "  make pin            Print the website PIN"
	@echo "  make update         Get the latest version and reinstall"
	@echo "  make uninstall      Remove the login services (keeps your library and models)"
	@echo "  make comfyui        Run ComfyUI's own interface at http://127.0.0.1:8188"
	@echo "  make test / check   Run the test suite / static checks"

install:
	./install.sh

start:
	$(PYTHON) scripts/launcher.py start --no-browser

start-native:
	$(PYTHON) scripts/launcher.py start --native --no-browser

start-docker:
	$(PYTHON) scripts/launcher.py start --docker --no-browser

stop:
	$(PYTHON) scripts/launcher.py stop

status:
	$(PYTHON) scripts/launcher.py status

pin:
	@$(PYTHON) scripts/launcher.py pin

logs:
	tail -n 50 -F runtime/web-studio.log runtime/sam3-service.log runtime/comfyui-launcher.log

update:
	git pull --ff-only
	./install.sh

uninstall:
	$(PYTHON) scripts/launcher.py uninstall

comfyui:
	./scripts/start.sh

generate:
	$(PYTHON) scripts/generate.py

benchmark:
	$(PYTHON) scripts/generate.py --runs 2 --label benchmark

test:
	$(PYTHON) -m unittest discover -s studio/tests -v
	$(NODE) --test studio/tests/*.test.cjs

check:
	$(PYTHON) -m py_compile scripts/*.py studio/*.py studio/tests/*.py
	for script in studio/static/*.js; do $(NODE) --check "$$script"; done
	bash -n install.sh scripts/env.sh scripts/start.sh docker/engine-entrypoint.sh "Local Image Studio.command"
	UV_CACHE_DIR="$(CURDIR)/.cache/uv" $(UV) pip check --python $(PYTHON)
	if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then git diff --check && git diff --cached --check; fi
