.PHONY: up logs specops test-static test-odoo validate upgrade clean-init

DB ?= shopify_odoo_demo

up:
	docker compose up -d --remove-orphans

logs:
	docker compose logs odoo --tail=100

# REQ-MAINT-001: keep SpecOps available as a first-class maintenance check.
specops:
	@if [ -x .venv/bin/codex-specops ]; then \
		.venv/bin/codex-specops audit; \
	else \
		codex-specops audit; \
	fi

validate:
	$(MAKE) specops
	xmllint --noout \
		odoo/addons/shopify_sync_demo/views/shopify_sync_views.xml \
		odoo/addons/shopify_sync_demo/data/ir_cron.xml
	python3 -m py_compile \
		odoo/addons/shopify_sync_demo/controllers/shopify_webhooks.py \
		odoo/addons/shopify_sync_demo/models/shopify_sync.py \
		odoo/addons/shopify_sync_demo/hooks.py

test-static:
	@if [ -x .venv/bin/python ]; then \
		.venv/bin/python -m pytest tests; \
	else \
		python3 -m pytest tests; \
	fi

test-odoo:
	docker compose exec -T odoo odoo \
		-c /etc/odoo/odoo.conf \
		-d $(DB) \
		--test-enable \
		--test-tags /shopify_sync_demo \
		--http-port=8070 \
		--stop-after-init \
		--db_host=odoo-db \
		--db_user=odoo \
		--db_password=odoo
	docker compose restart odoo

upgrade:
	docker compose exec -T odoo odoo \
		-c /etc/odoo/odoo.conf \
		-d $(DB) \
		-u shopify_sync_demo \
		--stop-after-init \
		--db_host=odoo-db \
		--db_user=odoo \
		--db_password=odoo
	docker compose restart odoo

clean-init:
	docker compose down -v
	docker compose up -d odoo-db
	docker compose run --rm odoo odoo \
		-c /etc/odoo/odoo.conf \
		-d $(DB) \
		--without-demo=all \
		-i base,mail,account,stock,sale,sale_management,shopify_sync_demo \
		--stop-after-init \
		--db_host=odoo-db \
		--db_user=odoo \
		--db_password=odoo
	docker compose up -d
