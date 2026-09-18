PY ?= python3.12
VENV ?= .venv
BIN = $(VENV)/bin

.PHONY: venv test lint fmt injection acceptance check tf-fmt tf-check tf-validate build clean

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
	cd .. && $(CURDIR)/$(BIN)/ruff check --fix errand/ && terraform -chdir=infra fmt -recursive

# The same two terraform commands CI runs, in the same order. `terraform fmt`
# is picky about alignment inside a block and there is no way to get it right
# by eye, so run `make check` before pushing rather than finding out from a
# red build.
tf-check:
	terraform -chdir=infra fmt -check -recursive

tf-fmt:
	terraform -chdir=infra fmt -recursive

tf-validate:
	terraform -chdir=infra init -backend=false -input=false
	terraform -chdir=infra validate

# Everything CI checks, locally.
check: lint tf-check tf-validate test

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
