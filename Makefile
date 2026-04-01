SHELL = /bin/bash

.DEFAULT_GOAL := help

SERVICE_NAME := service-oa-records

CURRENT_DIR := $(shell pwd)

HTTP_PORT ?= 3000

# Docker metadata
GIT_HASH := $(shell git rev-parse HEAD)
GIT_HASH_SHORT := $(shell git rev-parse --short HEAD)
GIT_BRANCH := $(shell git symbolic-ref HEAD --short 2>/dev/null)
GIT_DIRTY := $(shell git status --porcelain)
GIT_TAG := $(shell git describe --tags || echo "no version info")
AUTHOR := $(USER)


# Flask specific
APP_SRC_DIR := app

# Commands
UV_RUN := uv run
PYTHON := $(UV_RUN) python3
TEST := $(UV_RUN) pytest
RUFF := $(UV_RUN) ruff
TY := $(UV_RUN) ty

# Docker variables?
DOCKER_REGISTRY = 074597099015.dkr.ecr.eu-central-1.amazonaws.com
DOCKER_IMG_LOCAL_TAG := $(DOCKER_REGISTRY)/swissgeo/$(SERVICE_NAME):local-$(USER)-$(GIT_HASH_SHORT)

# AWS variables
AWS_DEFAULT_REGION = eu-central-1

# Logging
LOGS_DIR = $(PWD)/logs

.PHONY: git-info
git-info:
	@echo "GIT_HASH=$(GIT_HASH)"
	@echo "GIT_HASH_SHORT=$(GIT_HASH_SHORT)"
	@echo "GIT_BRANCH=$(GIT_BRANCH)"
	@echo "GIT_DIRTY=$(GIT_DIRTY)"
	@echo "GIT_TAG=$(GIT_TAG)"
	@echo "AUTHOR=$(AUTHOR)"
	@echo "DOCKER_IMG_LOCAL_TAG=$(DOCKER_IMG_LOCAL_TAG)"


.PHONY: ci
ci:
	# Create virtual env with all packages for development using the Pipfile.lock
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


.PHONY: serve
serve: start-localstack ## Serve the application locally
	ENV_FILE=.env $(UV_RUN) flask --env-file .env --app app run --port=$(HTTP_PORT) --debug


.PHONY: gunicornserve
gunicornserve: start-localstack ## Serve the application locally with gunicorn
	ENV_FILE=.env $(UV_RUN) gunicorn --bind 0.0.0.0:$(HTTP_PORT) --reload app.wsgi:app


.PHONY: dockerlogin
dockerlogin: ## Login to the AWS Docker Registry (ECR)
	aws --profile swisstopo-swissgeo-builder ecr get-login-password --region $(AWS_DEFAULT_REGION) | docker login --username AWS --password-stdin $(DOCKER_REGISTRY)


.PHONY: dockerbuild
dockerbuild:  $(LOGS_DIR) ## Create a docker image
	docker build --no-cache \
		--build-arg GIT_HASH="$(GIT_HASH)" \
		--build-arg GIT_BRANCH="$(GIT_BRANCH)" \
		--build-arg GIT_DIRTY="$(GIT_DIRTY)" \
		--build-arg VERSION="$(GIT_TAG)" \
		--build-arg HTTP_PORT="$(HTTP_PORT)" \
		--build-arg AUTHOR="$(AUTHOR)" -t $(DOCKER_IMG_LOCAL_TAG) .


.PHONY: dockerpush
dockerpush: dockerbuild ## Push to the docker registry
	docker push $(DOCKER_IMG_LOCAL_TAG)


.PHONY: dockerrun
dockerrun: start-localstack dockerbuild ## Run the locally built docker image
	docker run \
		-it -p $(HTTP_PORT):8080 \
		--env-file=${ENV_FILE} \
		--env ALLOWED_HOSTS=127.0.0.1 \
		--net=host \
		$(DOCKER_IMG_LOCAL_TAG) -m app.wsgi


.PHONY: lint
lint: ## Run the linter on the code base and type-checker ty
	$(RUFF) check
	$(TY) check

.PHONY: test-ci
test-ci: $(LOGS_DIR) ## Run tests in the CI
	$(TEST) --cov --cov-branch --cov-report=xml:coverage.xml


.PHONY: test
test: $(LOGS_DIR) ## Run tests locally
	$(TEST) --cov --cov-branch --cov-report=html


.PHONY: help
help: ## Display this help
# automatically generate the help page based on the documentation after each make target
# from https://gist.github.com/prwhite/8168133
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage:\n  make \033[36m\033[0m\n"} /^[$$()% a-zA-Z_-]+:.*?##/ { printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2 } /^##@/ { printf "\n\033[1m%s\033[0m\n", substr($$0, 5) } ' $(MAKEFILE_LIST)

$(LOGS_DIR):
	mkdir -p -m=777 $(LOGS_DIR)
