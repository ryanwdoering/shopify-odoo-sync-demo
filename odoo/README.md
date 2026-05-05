# Odoo Service

Local Odoo 19 sandbox for the native Shopify Sync demo.

## Responsibility

This is the only application runtime in the repo. Odoo owns:

- product catalog and SKU identity
- list prices
- active/sellable product state
- stock and available-to-sell quantities
- Shopify sync mappings
- Shopify order validation state
- Shopify `orders/create` webhook intake
- customer creation
- quotations and sales orders
- invoices and payment registration for Shopify-paid orders
- delivery and fulfillment operations
- audit events

## Compose Services

| Service | Purpose |
| --- | --- |
| `odoo` | Odoo web server and native Shopify Sync module |
| `odoo-db` | Postgres 16 database for Odoo |

There is no separate backend, frontend, or SQLite service.

## Mounted Files And Volumes

| Path or volume | Purpose |
| --- | --- |
| `odoo/odoo.conf` | Odoo config mounted at `/etc/odoo/odoo.conf` |
| `odoo/addons` | Custom add-ons mounted at `/mnt/extra-addons` |
| `odoo-web-data` | Odoo filestore/data volume |
| `odoo-db-data` | Postgres database volume |

## Environment

Compose reads `.env` automatically when present and passes Shopify values to the
Odoo container:

```text
SHOPIFY_SHOP_DOMAIN
SHOPIFY_ACCESS_TOKEN
SHOPIFY_CLIENT_ID
SHOPIFY_CLIENT_SECRET
SHOPIFY_WEBHOOK_CALLBACK_URL
SHOPIFY_WEBHOOK_SECRET
SHOPIFY_ADMIN_API_VERSION
```

These values are only used to create the initial `shopify.sync.instance` during
module installation. Credentials can also be entered or edited directly in Odoo.
If `SHOPIFY_WEBHOOK_SECRET` is blank, the module uses `SHOPIFY_CLIENT_SECRET` to
verify Shopify webhook HMAC signatures.

## Start

```bash
docker compose up -d --remove-orphans
```

Open:

```text
http://localhost:8069
```

## Install Or Upgrade The Module

```bash
docker compose exec -T odoo odoo \
  -c /etc/odoo/odoo.conf \
  -d shopify_odoo_demo \
  -u shopify_sync_demo \
  --stop-after-init \
  --db_host=odoo-db \
  --db_user=odoo \
  --db_password=odoo
docker compose restart odoo
```

## Clean Reinitialization

This removes the local Odoo database and filestore volumes:

```bash
docker compose down -v
docker compose up -d odoo-db
docker compose run --rm odoo odoo \
  -c /etc/odoo/odoo.conf \
  -d shopify_odoo_demo \
  --without-demo=all \
  -i base,mail,account,stock,sale,sale_management,shopify_sync_demo \
  --stop-after-init \
  --db_host=odoo-db \
  --db_user=odoo \
  --db_password=odoo
docker compose up -d
```

Use this when you want only the project seed data and no Odoo sample products,
orders, receipts, or deliveries.

## Debugging

Check services:

```bash
docker compose ps
```

Check Odoo logs:

```bash
docker compose logs odoo --tail=100
```

Open an Odoo shell:

```bash
docker compose exec -T odoo odoo shell \
  -c /etc/odoo/odoo.conf \
  -d shopify_odoo_demo \
  --db_host=odoo-db \
  --db_user=odoo \
  --db_password=odoo
```

Validate XML:

```bash
make validate
```

Run native Odoo tests:

```bash
make test-odoo
```

## Webhook Endpoint

Shopify can call this Odoo route when an order is created:

```text
/shopify_sync_demo/webhooks/orders_create?db=shopify_odoo_demo
```

Because Shopify requires a public HTTPS destination, local demos need a tunnel
or the manual **Pull Shopify Orders** fallback. The route verifies
`X-Shopify-Hmac-Sha256` against the raw request body before it writes to Odoo.

## Notes

- A first-boot log line about database `odoo` not existing can be harmless during
  initialization.
- Extra stock, receipt, delivery, or product rows usually mean the database was
  created with Odoo demo data enabled.
- The Shopify Sync app is available from the Odoo app menu after the module is
  installed.
