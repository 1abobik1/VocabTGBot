SHELL := /bin/bash
ENV := set -a; [ -f .env ] && . ./.env; set +a
LOCAL_WRANGLER := $(CURDIR)/.tools/node_modules/.bin/wrangler
WRANGLER := $$( [ -x $(LOCAL_WRANGLER) ] && echo $(LOCAL_WRANGLER) || echo "npx --yes wrangler@4" )
CONFIG := wrangler.deploy.toml

.DEFAULT_GOAL := help
.PHONY: help test deploy dev tools secrets webhook health tail kv-list kv-get clean

help: ## показать это меню
	@grep -hE '^[a-z-]+:.*##' $(MAKEFILE_LIST) | sed 's/:.*## /\t/' | expand -t22

test: ## прогнать все тесты
	python3 -m unittest discover -s tests -t . -v

deploy: test ## тесты, затем деплой воркера в Cloudflare и проверка ответа
	@$(ENV); cd worker && ./build_config.sh >/dev/null && $(WRANGLER) deploy -c $(CONFIG)
	@$(ENV); sleep 3; printf 'health: '; curl -s -w ' [%{http_code}]\n' "$$WORKER_URL"

dev: ## локальный запуск воркера (KV эмулируется; Workers AI недоступен, задай AI_HTTP_URL в .dev.vars)
	@cd worker && [ -f .dev.vars ] || { echo "нет worker/.dev.vars — скопируй worker/.dev.vars.example"; exit 1; }
	@cd worker && CF_KV_NAMESPACE_ID=local-dev ./build_config.sh >/dev/null && sed -i '/^\[ai\]/,/^binding/d' $(CONFIG)
	@cd worker && $(WRANGLER) dev -c $(CONFIG) --test-scheduled

tools: ## поставить wrangler локально в .tools/ (нужен один раз)
	@mkdir -p .tools && echo '{"name":"vocab-tools","private":true}' > .tools/package.json && \
		cd .tools && npm_config_prefix=$(CURDIR)/.tools npm i wrangler@4 >/dev/null && \
		echo "wrangler: $$(node_modules/.bin/wrangler --version | tail -1)"

secrets: ## записать секреты воркера из .env (TELEGRAM_BOT_TOKEN, TELEGRAM_WEBHOOK_SECRET, OWNER_USERNAME)
	@$(ENV); cd worker && ./build_config.sh >/dev/null && \
		for name in TELEGRAM_BOT_TOKEN TELEGRAM_WEBHOOK_SECRET OWNER_USERNAME; do \
			printenv $$name | $(WRANGLER) secret put $$name -c $(CONFIG); \
		done

webhook: ## направить вебхук Telegram на воркер и обновить меню команд
	@$(ENV); python3 scripts/set_webhook.py "$$WORKER_URL"

health: ## проверить, что воркер отвечает
	@$(ENV); curl -s -w ' [%{http_code}]\n' "$$WORKER_URL"

tail: ## живые логи воркера (Ctrl+C для выхода)
	@$(ENV); cd worker && ./build_config.sh >/dev/null && $(WRANGLER) tail -c $(CONFIG) --format pretty

kv-list: ## список ключей в KV
	@$(ENV); cd worker && ./build_config.sh >/dev/null && $(WRANGLER) kv key list --binding VOCAB_KV -c $(CONFIG) --remote

kv-get: ## показать значение ключа: make kv-get KEY=queue:username
	@$(ENV); cd worker && ./build_config.sh >/dev/null && $(WRANGLER) kv key get "$(KEY)" --binding VOCAB_KV -c $(CONFIG) --remote

clean: ## удалить сгенерированные файлы сборки
	rm -rf worker/$(CONFIG) worker/src/shared worker/.wrangler $$(find . -name __pycache__ -type d)
