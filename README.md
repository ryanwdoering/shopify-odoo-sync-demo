# Shopify Sync Demo

Native Odoo 19 module that demonstrates an Odoo-led Shopify integration.

Odoo is the business system of record. Shopify is the commerce edge. The module
publishes Odoo catalog and inventory to Shopify, ingests Shopify orders,
validates those orders against Odoo, creates Odoo sales records for accepted
orders, submits Shopify cancel/refund requests for rejected orders, and carries
accepted orders through payment and fulfillment.

The operator experience is the native Odoo app named **Shopify Sync**.

## What It Demonstrates

- Native Odoo models, views, menus, access rules, scheduled actions, install
  hooks, and HTTP controllers.
- Shopify Admin GraphQL integration for products, inventory, orders, refunds,
  webhooks, and fulfillment.
- Odoo as source of truth for SKUs, sellable state, prices, available stock,
  customers, sales orders, invoices, payments, deliveries, and audit events.
- Signed Shopify `orders/create` webhook intake with manual order pull as a
  recovery path.
- Line-level order validation for SKU, active/sellable product state, quantity,
  and price warnings.
- Tested happy paths and failure paths without mutating the live Shopify dev
  store during automated tests.

## Runtime

| Service | Purpose |
| --- | --- |
| `odoo` | Odoo 19 web server plus the custom Shopify Sync module |
| `odoo-db` | Postgres 16 database for Odoo |

Default Odoo URL:

```text
http://localhost:8069
```

## Project Map

```text
odoo/
  odoo.conf
  README.md
  addons/shopify_sync_demo/
    __manifest__.py
    controllers/shopify_webhooks.py
    hooks.py
    models/shopify_sync.py
    views/shopify_sync_views.xml
    data/ir_cron.xml
    security/ir.model.access.csv
    tests/test_shopify_sync_demo.py

tests/test_static_contracts.py
docker-compose.yml
Makefile
Guide.md
```

## Setup

### 1. Start Odoo

```bash
make up
```

Open:

```text
http://localhost:8069
```

### 2. Configure Shopify

Create a Shopify custom app and enable these Admin API scopes:

```text
read_products
write_products
read_inventory
write_inventory
read_orders
write_orders
read_locations
read_merchant_managed_fulfillment_orders
write_merchant_managed_fulfillment_orders
```

In Odoo, open **Shopify Sync** and set:

- shop domain
- Admin API access token, or client credentials if supported
- webhook secret or app client secret
- webhook callback URL if showing live webhooks

You can also copy `.env.example` to `.env` before module installation to prefill
the initial sync instance from Docker Compose environment variables.

### 3. Install Or Upgrade The Module

```bash
make upgrade
```

For a fresh local database with Odoo demo data disabled:

```bash
make clean-init
```

This removes local Docker volumes, recreates the database, installs the required
Odoo apps, installs `shopify_sync_demo`, and starts Odoo.

## Webhook Setup

Shopify cannot call `localhost`. For live Shopify order ingestion, expose Odoo
through a public HTTPS tunnel and use this callback shape:

```text
https://your-public-host/shopify_sync_demo/webhooks/orders_create?db=shopify_odoo_demo
```

Then in Odoo:

1. Open **Shopify Sync**.
2. Open the sync instance.
3. Paste the callback into **Webhook Callback URL**.
4. Set **Webhook Secret** or ensure **Client Secret** is set.
5. Click **Register Order Webhook**.

The Odoo controller verifies Shopify's `X-Shopify-Hmac-Sha256` signature against
the raw request body before writing order data.

## Demo Flow

### 1. Seed Odoo Catalog

In **Shopify Sync**, open the sync instance and run:

1. **Test Connection**
2. **Reset Demo Data** for a clean local walkthrough, or **Seed Demo Data** to
   restore the canonical products
3. **Refresh Inventory Catalog**

Seed products:

| Product | SKU | Price | Stock |
| --- | --- | ---: | ---: |
| Demo Hoodie | `DEMO-HOODIE-001` | 64.00 | 12 |
| Demo Canvas Tote | `DEMO-TOTE-002` | 24.00 | 20 |
| Demo Ceramic Mug | `DEMO-MUG-003` | 18.00 | 30 |
| Demo Field Cap | `DEMO-CAP-004` | 22.00 | 3 |
| Demo Low Stock Item | `DEMO-LOW-005` | 40.00 | 1 |

### 2. Publish To Shopify

Run:

1. **Publish Products**
2. **Publish Inventory**

The module uses:

- `productSet` for catalog publish
- `inventoryItemUpdate` to ensure inventory tracking is enabled
- `inventorySetQuantities` to publish Odoo `free_qty`

Published variants use tracked inventory and Shopify's deny-oversell inventory
policy. After inventory publish reaches Shopify, sold-out tracked variants
should be rejected by Shopify checkout.

### 3. Ingest A Valid Order

Create a Shopify order for an in-stock SKU.

Odoo can ingest it by:

- signed Shopify `orders/create` webhook, or
- **Pull Shopify Orders** manual fallback

For valid orders, Odoo:

- validates each line
- creates or reuses the customer
- creates a quotation/sales order
- assigns salesperson `Shopify`
- confirms the sale through the one-minute scheduled action or **Confirm Quotations**
- if Shopify reports `PAID`, posts the invoice and registers payment
- republishes affected Shopify inventory from Odoo `free_qty`

### 4. Reject A Bad Order

Bad order examples:

- missing SKU
- inactive SKU
- non-sellable product
- quantity above Odoo available stock

For rejected orders, Odoo:

- stores the failed sync order
- records exact line-level validation messages
- submits Shopify `orderCancel` with original-payment-method refund enabled
- uses `restock: false` because Odoo remains inventory truth
- stores Shopify's returned cancel job ID
- logs the event in **Events**

Shopify cancellation/refund settlement is asynchronous, so the Odoo record shows
submission status and job ID rather than claiming final settlement immediately.

### 5. Fulfill An Accepted Order

Use **Mark Fulfilled** on an accepted sync order.

The module:

- confirms the linked Odoo sale order if needed
- attempts to validate Odoo delivery operations
- creates Shopify fulfillment records for open fulfillment orders
- marks the sync order fulfilled
- records an audit event

## Automation

The module installs one active scheduled action:

```text
Shopify Sync: Confirm validated quotations
```

It runs every minute and confirms Shopify-owned quotations. If Shopify reported
the order as paid, it also runs Odoo's invoice/post/payment flow.

## Tests

Run the full local verification set:

```bash
make validate
make test-static
make test-odoo
```

What the tests cover:

- XML and Python syntax
- Odoo-only repo contract
- module dependencies, menus, buttons, access rules, and cron
- connection success/failure
- client-credentials token fallback
- seed/reset
- product publish success/failure
- inventory publish from Odoo `free_qty`
- webhook registration
- webhook HMAC verification
- manual order pull
- valid order creation
- failed validation and Shopify cancel/refund submission
- price warnings
- scheduled quotation confirmation
- paid invoice/payment flow
- fulfillment

The native Odoo tests patch Shopify GraphQL calls, so they verify Odoo behavior
without mutating the live Shopify dev store.

## Useful Commands

```bash
make up           # start Odoo and Postgres
make logs         # tail Odoo logs
make upgrade      # upgrade the custom Odoo module
make clean-init   # rebuild a clean no-demo Odoo database
make validate     # XML and Python syntax checks
make test-static  # fast repo-level contract tests
make test-odoo    # native Odoo TransactionCase tests
```

## Known Limits

- This is a local technical demo, not a production connector.
- Odoo validation happens at order intake time, not inside Shopify checkout.
- Webhook handling is synchronous for demo clarity.
- Shopify cancellation/refund processing is asynchronous.
- Shopify products are not deleted automatically.
- Tax, shipping, reconciliation, returns, partial refunds, and partial
  fulfillment are intentionally narrow.
- Secrets are stored in local Odoo fields for demo use.
- Multi-store support is limited to multiple sync instance records.

## More Docs

- [Technical demo guide](Guide.md)
- [Odoo service notes](odoo/README.md)
- [Native module notes](odoo/addons/shopify_sync_demo/README.md)
- [Test suite notes](tests/README.md)
