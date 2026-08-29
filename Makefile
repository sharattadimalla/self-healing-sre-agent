# Self-Healing SRE Agent — CHG-001 (managed api + compose stack)
# `agent` / `locust` / scenario targets arrive in later changes.

SHELL := /bin/bash
API_REPLICAS ?= 2
BASE ?= http://localhost:$(or $(TRAEFIK_HTTP_PORT),80)
ADMIN_TOKEN ?= dev-admin-token
ADMIN_HDR := -H "X-Admin-Token: $(ADMIN_TOKEN)" -H "Content-Type: application/json"

.PHONY: help up down restart rebuild ps logs health seed \
        fault-error fault-latency fault-clear remediate-flag remediate-cache state test

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

up: ## Build and start the stack (scaled)
	docker compose up --build -d --scale api=$(API_REPLICAS)

down: ## Stop and remove the stack + volumes
	docker compose down -v

restart: ## Restart the api service
	docker compose restart api

rebuild: ## Rebuild the api image and recreate
	docker compose up -d --build --scale api=$(API_REPLICAS) api

ps: ## Show running services
	docker compose ps

logs: ## Tail api logs
	docker compose logs -f api

health: ## Curl /healthz through Traefik
	curl -fsS $(BASE)/healthz && echo

seed: ## Create a handful of orders
	@for i in $$(seq 1 20); do \
		curl -fsS -X POST $(BASE)/orders -H "Content-Type: application/json" \
			-d "{\"item\":\"widget-$$i\",\"quantity\":$$((RANDOM % 5 + 1)),\"price_cents\":$$((RANDOM % 5000))}" >/dev/null; \
	done; echo "seeded 20 orders"

fault-error: ## Inject a 30% error rate
	curl -fsS -X POST $(BASE)/admin/fault $(ADMIN_HDR) -d '{"type":"error","magnitude":0.3}' && echo

fault-latency: ## Inject 800ms DB latency
	curl -fsS -X POST $(BASE)/admin/fault $(ADMIN_HDR) -d '{"type":"latency","ms":800,"target":"db"}' && echo

fault-clear: ## Clear all injected faults
	curl -fsS -X POST $(BASE)/admin/fault $(ADMIN_HDR) -d '{"type":"clear"}' && echo

remediate-flag: ## Roll back the "feature flag" (stops injected errors)
	curl -fsS -X POST $(BASE)/admin/remediation $(ADMIN_HDR) -d '{"action":"disable_feature_flag"}' && echo

remediate-cache: ## Enable the response cache
	curl -fsS -X POST $(BASE)/admin/remediation $(ADMIN_HDR) -d '{"action":"enable_cache"}' && echo

state: ## Show current fault + remediation state
	curl -fsS $(BASE)/admin/state $(ADMIN_HDR) && echo

test: ## Run the api unit tests
	cd api && python -m pytest -q
