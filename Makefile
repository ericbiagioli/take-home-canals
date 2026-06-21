ROOT_DIR:=$(shell dirname $(realpath $(lastword $(MAKEFILE_LIST))))
ENV_FILE=$(ROOT_DIR)/.env

# Read the env file, and source it inside this Makefile
ifeq (,$(wildcard $(ENV_FILE)))
$(shell cp .env.example $(ENV_FILE))
$(info    )
$(info      a .env file has been generated for you; check its content)
$(info   )
endif

include $(ENV_FILE)
export $(shell sed -e 's/=.*//' -e 's/^\#.*//' $(ENV_FILE)) 

ifndef DOCKER_COMPOSE_USER
export DOCKER_COMPOSE_USER=$(shell id -un)
export DOCKER_COMPOSE_GROUP=$(shell id -gn)
export DOCKER_COMPOSE_UID=$(shell id -u)
export DOCKER_COMPOSE_GID=$(shell id -g)
endif

.DEFAULT_GOAL := help

.PHONY: help build

help: ## Display available commands in Makefile
	@grep -hE '^[a-zA-Z_0-9-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-30s\033[0m %s\n", $$1, $$2}'

build: ## Build the images required by the application
	@docker compose build # note: to call this stand-alone from command line, it is necessary to define in some way the environment that is defined here

up: ## Creates and starts the application
	@docker compose up -d

stop: ## Stops the application
	@docker compose $@

start: ## Starts the application (must has been created before using the up target)
	@docker compose $@

ps: ## Show the running docker processes
	@docker compose $(DC_CONF) $@

logs: ## Show the logs of the running docker processes
	@docker compose $@

shell_postgres: ## Open a bash shell in the PostgreSQL container
	@docker compose exec db bash

shell_app: ## Open a bash shell in the application container
	@docker compose exec app bash

rm: ## Stop and remove containers
	@docker compose rm -sfv

clean: ## Remove all (including the database volume)
	@docker compose rm -sfv
	@docker compose down -v
