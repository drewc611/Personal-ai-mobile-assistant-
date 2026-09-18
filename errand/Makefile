PY ?= python3.12
VENV ?= .venv
BIN = $(VENV)/bin

.PHONY: venv test lint fmt injection acceptance build clean

venv:
	$(PY) -m venv $(VENV)
	$(BIN)/pip install --quiet --upgrade pip
	$(BIN)/pip install --quiet -r ../requirements-dev.txt

test:
	cd .. && $(CURDIR)/$(BIN)/python -m pytest errand/tests

injection:
	cd .. && $(CURDIR)/$(BIN)/python -m pytest errand/tests/injection -m injection

acceptance:
	cd .. && $(CURDIR)/$(BIN)/python -m pytest errand/tests/acceptance -m acceptance

lint:
	cd .. && $(CURDIR)/$(BIN)/ruff check errand/

fmt:
	cd .. && $(CURDIR)/$(BIN)/ruff check --fix errand/

build:
	rm -rf ../build && mkdir -p ../build/package
	$(BIN)/pip install --quiet --target ../build/package -r ../requirements.txt
	cp -r ../errand ../build/package/errand
	find ../build/package -name '__pycache__' -type d -prune -exec rm -rf {} +
	find ../build/package -name 'tests' -type d -prune -exec rm -rf {} +
	cd ../build/package && zip -qr ../errand.zip .
	@echo "built ../build/errand.zip"

clean:
	rm -rf ../build $(VENV)
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
