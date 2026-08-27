SHELL = /bin/bash

.DEFAULT_GOAL := help

SERVICE_NAME := service-oa-records

CURRENT_DIR := $(shell pwd)

# Docker metadata
GIT_HASH := $(shell git rev-parse HEAD)
GIT_HASH_SHORT := $(shell git rev-parse --short HEAD)
GIT_BRANCH := $(shell git symbolic-ref HEAD --short 2>/dev/null)
GIT_DIRTY := $(shell git status --porcelain)
GIT_TAG := $(shell git describe --tags || echo "no version info")
AUTHOR := $(USER)

# Docker variables
DOCKER_REGISTRY = 074597099015.dkr.ecr.eu-central-1.amazonaws.com
DOCKER_IMG_LOCAL_TAG := $(DOCKER_REGISTRY)/swissgeo/$(SERVICE_NAME):local-$(USER)-$(GIT_HASH_SHORT)

# AWS variables
AWS_DEFAULT_REGION = eu-central-1

# Commands
UV_RUN := uv run
PYTHON := $(UV_RUN) python3
TEST := $(UV_RUN) pytest
RUFF := $(UV_RUN) ruff
TY := $(UV_RUN) ty
UVICORN := $(UV_RUN) uvicorn

# Application specific
APP_SRC_DIR := pygeoapi-swissgeo-extensions
HTTP_PORT ?= 8080
SERVE_ARGS := app:APP --host 0.0.0.0 --port $(HTTP_PORT) \
	--app-dir $(APP_SRC_DIR) --log-config config-files/logging-conf.yaml

# The app always runs on the host (like service-control), so one env file covers
# both. uv loads it into the environment of every `uv run` below; `dockerrun` passes
# the relevant values through to the container, which runs with --net=host.
# `.env` is created from `.env.default`; `.env.local` overrides it when present.
ENV_FILE ?= $(if $(wildcard .env.local),.env.local,.env)
# $(wildcard ...) so the variable is empty when the file does not exist: uv errors
# out on a missing UV_ENV_FILE, which would break CI-only targets (format, lint,
# test) that need no runtime config.
export UV_ENV_FILE := $(wildcard $(ENV_FILE))

# Read back the value the recipes need at the shell level (i.e. outside `uv run`).
OPENSEARCH_URL = $(shell sed -n 's/^OPENSEARCH_URL=//p' $(ENV_FILE) 2>/dev/null)
OTEL_ENDPOINT  = $(shell sed -n 's/^OTEL_EXPORTER_OTLP_ENDPOINT=//p' $(ENV_FILE) 2>/dev/null)

# Aliases the collections in pygeoapi-config.yml resolve to
OPENSEARCH_INDEXES := swissgeo-catalog swissgeo-distributions geoadmin-services

# Mirrors ENV PYTHONPATH in the Dockerfile, so that pygeoapi can import
# swissgeo_provider when running from the repo root.
export PYTHONPATH := $(CURRENT_DIR)/$(APP_SRC_DIR)


.env:
	cp .env.default .env


.PHONY: setup
setup: .env ## Create virtualenv with all packages for development
	uv sync
	# Start a new shell with the virtualenv activated and OPENSEARCH_URL exported, so
	# that the app and the catalogue scripts find the service-control OpenSearch.
	uv run $$SHELL


.PHONY: ci
ci: .env ## Create virtual env with all packages for development using the uv.lock (CI)
	uv sync --frozen


.PHONY: format
format: ## Call ruff format to make sure your code is easier to read and respects some conventions.
	$(RUFF) format
	$(RUFF) check --select I --fix


.PHONY: ci-check-format
ci-check-format: format ## Check the format (CI)
	@if [[ -n `git status --porcelain --untracked-files=no` ]]; then \
	 	>&2 echo "ERROR: the following files are not formatted correctly"; \
	 	>&2 echo "'git status --porcelain' reported changes in those files after a 'make format' :"; \
		>&2 git status --porcelain --untracked-files=no; \
		exit 1; \
	fi

.PHONY: check-opensearch-up
check-opensearch-up: .env ## Check that the service-control OpenSearch is reachable
	@if ! curl -sf -m 3 -o /dev/null $(OPENSEARCH_URL)/_cluster/health; then \
		>&2 echo "ERROR: no OpenSearch at $(OPENSEARCH_URL)"; \
		>&2 echo "       This service expects the OpenSearch server from service-control."; \
		>&2 echo "       Start it there first (see README), then retry."; \
		exit 1; \
	fi


.PHONY: check-opensearch
check-opensearch: check-opensearch-up ## Check that OpenSearch is reachable with the expected indexes
	@missing=""; \
	for idx in $(OPENSEARCH_INDEXES); do \
		curl -sf -m 3 -o /dev/null $(OPENSEARCH_URL)/$$idx || missing="$$missing $$idx"; \
	done; \
	if [[ -n "$$missing" ]]; then \
		>&2 echo "ERROR: OpenSearch at $(OPENSEARCH_URL) is up but missing index/alias:$$missing"; \
		>&2 echo "       Load the catalogue from the service-control stack."; \
		exit 1; \
	fi
	@echo "OpenSearch OK at $(OPENSEARCH_URL)"


.PHONY: openapi
openapi: .env ## Generate the OpenAPI document from the pygeoapi config
	# $$PYGEOAPI_* come from $(ENV_FILE), which uv loads into the child environment,
	# so expand them inside the `uv run` shell rather than in make's.
	$(UV_RUN) sh -c 'pygeoapi openapi generate "$$PYGEOAPI_CONFIG" --output-file "$$PYGEOAPI_OPENAPI"'


.PHONY: serve
serve: check-opensearch openapi ## Serve the application locally
	$(UVICORN) $(SERVE_ARGS) --reload


.PHONY: serve-debug
serve-debug: check-opensearch openapi ## Serve the application locally for debugging
	$(PYTHON) -m debugpy --listen localhost:5678 --wait-for-client \
		-m uvicorn $(SERVE_ARGS)


.PHONY: dockerbuild
dockerbuild:  ## Build the docker image locally
	docker build -t $(DOCKER_IMG_LOCAL_TAG) .


.PHONY: dockerlogin
dockerlogin: ## Login to the AWS Docker Registry (ECR)
	aws --profile swisstopo-swissgeo-builder ecr get-login-password --region $(AWS_DEFAULT_REGION) | docker login --username AWS --password-stdin $(DOCKER_REGISTRY)


.PHONY: dockerpush
dockerpush: dockerlogin dockerbuild ## Push to the docker registry
	docker push $(DOCKER_IMG_LOCAL_TAG)


.PHONY: dockerrun
dockerrun: check-opensearch dockerbuild ## Run the locally built docker image
	# --net=host so that localhost:9200 (OpenSearch) and localhost:4317 (collector)
	# resolve exactly as they do for `make serve`. PYGEOAPI_* are already baked into
	# the image, so only the endpoints are passed through.
	docker run \
		-it --net=host \
		--env OPENSEARCH_URL="$(OPENSEARCH_URL)" \
		--env OTEL_EXPORTER_OTLP_ENDPOINT="$(OTEL_ENDPOINT)" \
		--env OTEL_EXPORTER_OTLP_INSECURE=true \
		$(DOCKER_IMG_LOCAL_TAG)


.PHONY: lint
lint: ## Run the ruff linter on the code base and type-checker ty
	$(RUFF) check --fix
	$(TY) check


.PHONY: test-ci
test-ci: ## Run tests in the CI (with coverage report in xml format for codecov)
	$(TEST) --cov --cov-branch --cov-report=xml:coverage.xml


.PHONY: test
test: ## Run tests with coverage report in html format
	$(TEST) --cov --cov-branch --cov-report=html


.PHONY: help
help: ## Display this help
# automatically generate the help page based on the documentation after each make target
# from https://gist.github.com/prwhite/8168133
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage:\n  make \033[36m\033[0m\n"} /^[$$()% a-zA-Z_-]+:.*?##/ { printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2 } /^##@/ { printf "\n\033[1m%s\033[0m\n", substr($$0, 5) } ' $(MAKEFILE_LIST)
