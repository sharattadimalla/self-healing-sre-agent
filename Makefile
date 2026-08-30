# Self-Healing SRE Agent — end-to-end demo.

SHELL := /bin/bash
API_REPLICAS ?= 2
BASE ?= http://localhost:$(or $(TRAEFIK_HTTP_PORT),80)
AGENT ?= http://localhost:$(or $(AGENT_UI_PORT),8000)
ADMIN_TOKEN ?= dev-admin-token
ADMIN_HDR := -H "X-Admin-Token: $(ADMIN_TOKEN)" -H "Content-Type: application/json"
COMPOSE := docker compose

.PHONY: help up down restart rebuild ps logs logs-agent health seed load \
        fault-error fault-latency fault-clear remediate-flag remediate-cache state \
        incidents approve reject \
        scenario-errors scenario-traffic scenario-latency \
        test test-api test-agent

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# --- stack lifecycle ------------------------------------------------------
up: ## Build and start the whole stack (api scaled)
	$(COMPOSE) up --build -d --scale api=$(API_REPLICAS)

down: ## Stop and remove the stack + volumes
	$(COMPOSE) down -v --remove-orphans

restart: ## Restart the api service
	$(COMPOSE) restart api

rebuild: ## Rebuild api + agent images and recreate
	$(COMPOSE) up -d --build --scale api=$(API_REPLICAS) api agent

ps: ## Show running services
	$(COMPOSE) ps

logs: ## Tail api logs
	$(COMPOSE) logs -f api

logs-agent: ## Tail agent logs (detect / analyze / recommend / verify)
	$(COMPOSE) logs -f agent

health: ## Curl /healthz through Traefik and the agent
	curl -fsS $(BASE)/healthz && echo && curl -fsS $(AGENT)/healthz && echo

# --- workload -----------------------------------------------------------
seed: ## Create a handful of orders
	@for i in $$(seq 1 20); do \
		curl -fsS -X POST $(BASE)/orders -H "Content-Type: application/json" \
			-d "{\"item\":\"widget-$$i\",\"quantity\":$$((RANDOM % 5 + 1)),\"price_cents\":$$((RANDOM % 5000))}" >/dev/null; \
	done; echo "seeded 20 orders"

load: ## Start a steady baseline load (headless locust, background)
	$(COMPOSE) run -d --rm -e LOCUST_SCENARIO=baseline locust \
		-f /mnt/locust/locustfile.py --headless -u 20 -r 5 \
		--host http://traefik:80 --run-time 60m
	@echo "baseline load running — locust web UI: http://localhost:$(or $(LOCUST_WEB_PORT),8089)"

# --- manual fault / remediation poking ---------------------------------
fault-error: ## Inject a 30% error rate
	curl -fsS -X POST $(BASE)/admin/fault $(ADMIN_HDR) -d '{"type":"error","magnitude":0.3}' && echo

fault-latency: ## Inject 800ms DB latency
	curl -fsS -X POST $(BASE)/admin/fault $(ADMIN_HDR) -d '{"type":"latency","ms":800,"target":"db"}' && echo

fault-clear: ## Clear all injected faults
	curl -fsS -X POST $(BASE)/admin/fault $(ADMIN_HDR) -d '{"type":"clear"}' && echo

remediate-flag: ## Roll back the feature flag (stops injected errors)
	curl -fsS -X POST $(BASE)/admin/remediation $(ADMIN_HDR) -d '{"action":"disable_feature_flag"}' && echo

remediate-cache: ## Enable the response cache
	curl -fsS -X POST $(BASE)/admin/remediation $(ADMIN_HDR) -d '{"action":"enable_cache"}' && echo

state: ## Show current fault + remediation state
	curl -fsS $(BASE)/admin/state $(ADMIN_HDR) && echo

# --- incidents / approval --------------------------------------------
incidents: ## List incidents from the agent
	curl -fsS $(AGENT)/incidents | python3 -m json.tool

approve: ## Approve an incident:  make approve INC=INC-0001
	@test -n "$(INC)" || { echo "usage: make approve INC=INC-0001"; exit 1; }
	curl -fsS -X POST $(AGENT)/incidents/$(INC)/approve | python3 -m json.tool

reject: ## Reject an incident:  make reject INC=INC-0001
	@test -n "$(INC)" || { echo "usage: make reject INC=INC-0001"; exit 1; }
	curl -fsS -X POST $(AGENT)/incidents/$(INC)/reject | python3 -m json.tool

# --- scripted scenarios (detect -> analyze -> recommend -> act) --------
scenario-errors: ## Error-spike scenario end to end
	AUTO_APPROVE=$(or $(AUTO_APPROVE),1) BASE=$(BASE) AGENT=$(AGENT) ADMIN_TOKEN=$(ADMIN_TOKEN) \
		bash load/scenarios/error_spike.sh

scenario-traffic: ## Traffic-spike scenario end to end
	AUTO_APPROVE=$(or $(AUTO_APPROVE),1) BASE=$(BASE) AGENT=$(AGENT) ADMIN_TOKEN=$(ADMIN_TOKEN) \
		bash load/scenarios/traffic_spike.sh

scenario-latency: ## Latency-spike scenario end to end
	AUTO_APPROVE=$(or $(AUTO_APPROVE),1) BASE=$(BASE) AGENT=$(AGENT) ADMIN_TOKEN=$(ADMIN_TOKEN) \
		bash load/scenarios/latency_spike.sh

# --- tests -------------------------------------------------------------
test: test-api test-agent ## Run all unit tests

test-api: ## Run the api unit tests
	cd api && python -m pytest -q

test-agent: ## Run the agent unit tests
	cd agent && python -m pytest -q
