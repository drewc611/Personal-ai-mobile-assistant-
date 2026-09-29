PY ?= python3.12
VENV ?= .venv
BIN = $(VENV)/bin

.PHONY: venv test lint fmt injection acceptance check preflight tf-fmt tf-check tf-validate build size clean

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
	cd .. && $(CURDIR)/$(BIN)/ruff check errand/ scripts/

fmt:
	cd .. && $(CURDIR)/$(BIN)/ruff check --fix errand/ scripts/ && terraform -chdir=infra fmt -recursive

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

# Is the configuration actually finished? Run before deploying.
preflight:
	$(PY) ../scripts/preflight.py infra/terraform.tfvars

# The Lambda zip. Two things this deliberately does not do:
#
# It installs requirements-lambda.txt, not requirements.txt. The agent's
# dependencies (strands-agents, bedrock-agentcore and their trees) belong to
# the AgentCore Runtime image; bundling them here added ~50MB to a zip that
# has a 50MB direct-upload limit, for code the Lambdas never import.
#
# It copies the source with an exclude list rather than `cp -r`. `terraform
# init` leaves ~700MB of provider binaries in infra/.terraform, and the normal
# order of work is to run terraform before building, so a plain copy sweeps
# them into the artifact.
LAMBDA_EXCLUDES = --exclude=tests --exclude=infra --exclude=__pycache__ \
	--exclude=.venv --exclude='*.pyc' --exclude=.terraform

build:
	rm -rf ../build && mkdir -p ../build/package
	$(BIN)/pip install --quiet --target ../build/package -r ../requirements-lambda.txt
	mkdir -p ../build/package/errand
	tar -cf - $(LAMBDA_EXCLUDES) -C .. errand | tar -xf - -C ../build/package
	find ../build/package -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
	cd ../build/package && zip -qr ../errand.zip .
	@$(MAKE) --no-print-directory size

# Lambda refuses a direct upload over 50MB. Finding that out from `terraform
# apply` costs a round trip; finding it out here costs nothing.
size:
	@size=$$(stat -c%s ../build/errand.zip); \
	mb=$$((size / 1048576)); \
	echo "built ../build/errand.zip ($${mb}MB)"; \
	if [ $$size -gt 52428800 ]; then \
		echo "ERROR: over Lambda's 50MB direct-upload limit"; exit 1; \
	fi

clean:
	rm -rf ../build $(VENV)
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
