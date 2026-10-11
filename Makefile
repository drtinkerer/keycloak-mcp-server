.PHONY: help install clean test lint format coverage pre-commit local container deploy undeploy deps

# OpenShift namespace (can be overridden: make deploy openshift NAMESPACE=my-project)
NAMESPACE ?= $(shell oc project -q 2>/dev/null)

.DEFAULT_GOAL := help

help: ## Show this help message
	@echo "Usage: make [target]"
	@echo ""
	@echo "Targets:"
	@grep -E '^[a-zA-Z_-]+:.*##' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*##"}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

deps: ## Check required CLI tools (uv, podman, oc)
	@which uv > /dev/null && echo "uv: $(shell uv --version)" || (echo "Error: uv not found. Please install uv." && exit 1)
	@which podman > /dev/null && echo "podman: $(shell podman --version)" || (echo "Error: podman not found. Please install podman." && exit 1)
	@podman compose version > /dev/null 2>&1 && echo "podman compose: $(shell podman compose version)" || (echo "Error: podman compose not found. Please install podman compose." && exit 1)
	@which oc > /dev/null && echo "oc: $(shell oc version --client)" || (echo "Error: oc not found. Please install oc." && exit 1)

install: ## Sync locked dependencies and install pre-commit hooks
	uv sync --locked
	uv run --locked pre-commit install
	@test -f .env || cp .env.example .env

clean: ## Remove generated caches and build artifacts
	rm -rf .mypy_cache .ruff_cache .pytest_cache __pycache__ build dist htmlcov

test: ## Run test suite
	uv run --locked pytest

lint: ## Run ruff linter and mypy type checker
	uv run --locked ruff check .
	uv run --locked mypy keycloak_mcp_server/

format: ## Auto-fix lint issues and format code
	uv run --locked ruff check . --fix
	uv run --locked ruff format .

coverage: ## Run tests with coverage report (80% minimum)
	uv run --locked pytest --cov=keycloak_mcp_server --cov-report=term-missing --cov-report=html --cov-fail-under=80

pre-commit: ## Run all pre-commit hooks
	uv run --locked pre-commit run --all-files

local: ## Start MCP server locally
	@test -f .env || cp .env.example .env
	uv run --locked keycloak-mcp-server

container: ## Build and run with podman compose
	export PODMAN_COMPOSE_SILENT=true
	podman compose --no-ansi up --build --force-recreate --remove-orphans  --timeout=60

deploy: ## Deploy to target (usage: make deploy openshift)
	@if [ "$(filter openshift,$(MAKECMDGOALS))" = "openshift" ]; then \
		echo "Checking for oc CLI..."; \
		which oc > /dev/null || (echo "Error: oc CLI not found. Please install OpenShift CLI." && exit 1); \
		echo "Validating namespace..."; \
		if [ -z "$(NAMESPACE)" ]; then \
			echo "Error: NAMESPACE not set. Usage: make deploy openshift NAMESPACE=your-project"; \
			exit 1; \
		fi; \
		echo "Using namespace: $(NAMESPACE)"; \
		echo "Switching to namespace..."; \
		oc project $(NAMESPACE) || (echo "Error: Cannot switch to namespace '$(NAMESPACE)'. Check permissions." && exit 1); \
		echo "Updating namespace references..."; \
		sed -i.bak "s|NAMESPACE_PLACEHOLDER|$(NAMESPACE)|g" deployment/openshift/deployment.yaml; \
		sed -i.bak "s|namespace: keycloak-mcp-server|namespace: $(NAMESPACE)|g" deployment/openshift/kustomization.yaml; \
		echo "Creating BuildConfig and ImageStream..."; \
		oc apply -f deployment/openshift/buildconfig.yaml; \
		oc apply -f deployment/openshift/imagestream.yaml; \
		echo "Building container image from source..."; \
		oc start-build keycloak-mcp-server --from-dir=. --follow || (mv deployment/openshift/deployment.yaml.bak deployment/openshift/deployment.yaml 2>/dev/null; mv deployment/openshift/kustomization.yaml.bak deployment/openshift/kustomization.yaml 2>/dev/null; exit 1); \
		echo "Deploying resources to OpenShift..."; \
		oc apply -k deployment/openshift/ || (mv deployment/openshift/deployment.yaml.bak deployment/openshift/deployment.yaml 2>/dev/null; mv deployment/openshift/kustomization.yaml.bak deployment/openshift/kustomization.yaml 2>/dev/null; exit 1); \
		rm -f deployment/openshift/deployment.yaml.bak deployment/openshift/kustomization.yaml.bak; \
		echo "Deployment complete!"; \
		echo "Checking deployment status..."; \
		oc get pods -l app=keycloak-mcp-server; \
		echo ""; \
		echo "Useful commands:"; \
		echo "  View logs: oc logs -l app=keycloak-mcp-server --tail=100"; \
		echo "  Get route: oc get route keycloak-mcp-server"; \
		echo "  Check status: oc get pods,svc,route -l app=keycloak-mcp-server"; \
	else \
		echo "Usage: make deploy [openshift]"; \
		echo "Available deployment targets: openshift"; \
		exit 1; \
	fi

undeploy: ## Remove deployment (usage: make undeploy openshift)
	@if [ "$(filter openshift,$(MAKECMDGOALS))" = "openshift" ]; then \
		echo "Checking for oc CLI..."; \
		which oc > /dev/null || (echo "Error: oc CLI not found. Please install OpenShift CLI." && exit 1); \
		oc project $(NAMESPACE) || (echo "Error: Cannot switch to namespace '$(NAMESPACE)'" && exit 1); \
		echo "Removing OpenShift deployment..."; \
		oc delete deployment,service,route,configmap,secret,pvc,buildconfig,imagestream -l app=keycloak-mcp-server 2>/dev/null || true; \
		echo "Undeployment complete!"; \
	else \
		echo "Usage: make undeploy [openshift]"; \
		echo "Available undeployment targets: openshift"; \
		exit 1; \
	fi

%:
	@:
