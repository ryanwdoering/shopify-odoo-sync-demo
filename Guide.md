# Technical Demo Guide

Use this as the live walkthrough guide for the current Odoo-only Shopify Sync
demo.

## Positioning

This is a native Odoo 19 integration module. Odoo is the business system of
record, and Shopify is the commerce edge that receives published product and
inventory state.

Core thesis:

```text
Odoo owns product, price, stock, customer, order, payment, and fulfillment truth.
Shopify receives an operational projection of catalog and inventory.
Shopify orders enter Odoo only after Odoo validates them.
```

Current runtime:

| Service | Role |
| --- | --- |
| `odoo` | Odoo 19 web server plus the native `shopify_sync_demo` module |
| `odoo-db` | Postgres database for Odoo |

There is no FastAPI service, React dashboard, SQLite metadata store, or embedded
Shopify admin app in the current implementation.

## Architecture

```text
Odoo operator
  |
  v
Shopify Sync Odoo app
  |
  |-- Standard Odoo records
  |     product.template / product.product
  |     stock.quant / stock.location
  |     res.partner / res.users
  |     sale.order / sale.order.line
  |     account.move / account.payment.register
  |     stock.picking
  |
  |-- Custom sync records
  |     shopify.sync.instance
  |     shopify.sync.product.mapping
  |     shopify.sync.order
  |     shopify.sync.order.line
  |     shopify.sync.event
  |
  `-- Shopify Admin GraphQL
        productSet
        inventoryItemUpdate
        inventorySetQuantities
        webhookSubscriptionCreate
        orders query
        orderCancel
        fulfillmentOrders
        fulfillmentCreate

Shopify orders/create webhook
  |
  v
Odoo HTTP controller
  |
  v
Shared Odoo order ingestion and validation workflow
```

## Demo Setup Checklist

Before presenting:

1. Start the local stack with `make up`.
2. Open Odoo at `http://localhost:8069`.
3. Confirm the **Shopify Sync** app is installed.
4. Open the sync instance and confirm:
   - shop domain is set
   - Admin API token or client credentials are set
   - required Shopify scopes are present
   - webhook callback URL is set if showing live webhook intake
5. Run **Test Connection**.
6. Run **Reset Demo Data** for a clean local walkthrough.
7. Run **Refresh Odoo Catalog**.

If Shopify webhooks are part of the demo, expose Odoo with a public HTTPS tunnel
and use this callback shape:

```text
https://your-public-host/shopify_sync_demo/webhooks/orders_create?db=shopify_odoo_demo
```

Then click **Register Order Webhook** in Odoo.

## Demo Run Of Show

### 1. Show Odoo Business Truth

Open **Shopify Sync > Dashboard** and show the sync instance:

- connection status
- catalog count
- order count
- failed order count
- last sync/webhook timestamps
- action buttons

Then open **Catalog** and show:

- Odoo product
- SKU
- price
- on-hand quantity
- available-to-sell quantity
- Shopify product, variant, and inventory item IDs
- product publish status
- inventory publish status

Talk track:

```text
The integration starts from Odoo, not Shopify. Products, SKUs, prices, and
available-to-sell inventory live in Odoo first.
```

### 2. Publish Catalog And Inventory

From the sync instance:

1. Click **Publish Products**.
2. Click **Publish Inventory**.
3. Open Shopify and show the matching SKUs/prices if live credentials are ready.

What this proves:

- Odoo products are published through Shopify `productSet`.
- Shopify inventory tracking is enabled through `inventoryItemUpdate`.
- Available inventory is published through `inventorySetQuantities`.
- Variants are published with overselling denied.

Use careful wording:

```text
After Odoo publishes inventory, Shopify should reject checkout for sold-out
tracked variants through its native inventory policy.
```

### 3. Ingest A Valid Shopify Order

Create a Shopify order for an in-stock SKU, then either:

- let the signed `orders/create` webhook deliver it to Odoo, or
- click **Pull Shopify Orders** as the manual recovery path.

Open the sync order in Odoo and show:

- SKU validation passed
- product is active/sellable
- quantity is available
- price is accepted or recorded as a warning
- linked Odoo quotation or sales order
- salesperson is `Shopify`

Then confirm it:

- wait for the scheduled action, or
- click **Confirm Quotations**

For a paid Shopify order, show:

- Odoo sale order is confirmed
- invoice is posted
- payment is registered
- inventory is republished from Odoo `free_qty`

### 4. Reject A Bad Shopify Order

Use a bad order scenario:

- missing SKU
- inactive SKU
- non-sellable product
- requested quantity above Odoo available stock

Open the failed sync order and show:

- line-level failure reason
- `Failed` order status
- Shopify refund status
- Shopify cancel job ID
- `refund_failed_order` event

Talk track:

```text
Rejected orders do not become Odoo sale orders. Odoo submits a Shopify
cancel/refund request and keeps the failure visible for operations.
```

### 5. Fulfill An Accepted Order

Open an accepted order and click **Mark Fulfilled**.

What this does:

- confirms the linked Odoo sale order if needed
- attempts to validate Odoo delivery operations
- creates Shopify fulfillment records for open fulfillment orders
- marks the sync order fulfilled
- records an event

## Code Hotspots

| File | What to show |
| --- | --- |
| `odoo/addons/shopify_sync_demo/models/shopify_sync.py` | Main business logic: publish, inventory, validation, refund submission, paid order handling, fulfillment. |
| `odoo/addons/shopify_sync_demo/controllers/shopify_webhooks.py` | Public Shopify webhook route, raw-body HMAC verification, handoff into Odoo models. |
| `odoo/addons/shopify_sync_demo/views/shopify_sync_views.xml` | Native Odoo menus, forms, lists, status badges, and object buttons. |
| `odoo/addons/shopify_sync_demo/data/ir_cron.xml` | One-minute automation that confirms Shopify quotations. |
| `odoo/addons/shopify_sync_demo/hooks.py` | Install-time default instance creation and seed hook. |
| `odoo/addons/shopify_sync_demo/security/ir.model.access.csv` | Access rules for custom Odoo models. |
| `odoo/addons/shopify_sync_demo/tests/test_shopify_sync_demo.py` | Native Odoo tests for connection, seed/reset, publishing, webhook registration, HMAC, order intake, validation, refunds, paid orders, inventory, and fulfillment. |
| `tests/test_static_contracts.py` | Fast repo-level contract checks that keep the project Odoo-only and documented. |

## Job-Fit Talk Track

For a Business Systems & Automation Engineer conversation, frame this as an
operational systems automation project rather than a web UI project.

| Role Signal | Demo Evidence |
| --- | --- |
| Odoo customization | Native Odoo models, menus, list/form views, security rules, scheduled actions, and `sale.order` extension. |
| Python automation | Shopify GraphQL integration, deterministic seed/reset actions, paid-order automation, and repeatable Makefile commands. |
| Shopify/e-commerce automation | Odoo-to-Shopify catalog publishing, inventory publishing, signed Shopify order webhooks, failed-order cancel/refund submission, manual recovery pulls, and fulfillment creation. |
| Database/system administration | Dockerized Odoo/Postgres, clean no-demo initialization, module upgrade commands, and access-rule tests. |
| Documentation and transparency | Root README, service docs, module docs, test docs, and operator-visible Odoo event records. |
| Operational support | Validation failures remain in Odoo with exact line-level messages and audit events instead of disappearing into logs. |

## Verification

Run:

```bash
make validate
make test-static
make test-odoo
```

What is covered:

- XML and Python syntax
- Odoo-only repo contract
- module dependencies, menus, buttons, access rules, and cron
- connection success/failure
- client credentials fallback
- seed/reset
- product publish success/failure
- inventory publish from Odoo `free_qty`
- Shopify webhook registration
- webhook HMAC verification
- manual order pull
- valid order creation
- failed validation and Shopify cancel/refund submission
- price warnings
- scheduled quotation confirmation
- paid invoice/payment flow
- fulfillment

## Failure Modes

### Shopify Connection Fails

Likely causes:

- wrong shop domain
- stale Admin API token
- Shopify scopes changed after token generation
- client credentials not supported by the app type

Fix:

Regenerate the Shopify token, update the Odoo sync instance, and click
**Test Connection** again.

### Inventory Does Not Update In Shopify

Likely causes:

- products were not published first
- mapping row has no Shopify inventory item ID
- token lacks `write_inventory`
- Shopify inventory item was not trackable or tracking update failed

Fix:

Run **Publish Products** first, then **Publish Inventory**, and inspect the
mapping row's last error plus the Events view.

### Fulfillment Orders Access Denied

The token needs:

```text
read_merchant_managed_fulfillment_orders
write_merchant_managed_fulfillment_orders
```

Regenerate the Shopify token after changing scopes.

### Odoo Shows Extra Demo Products

The database was probably initialized with Odoo demo data. Use:

```bash
make clean-init
```

For a quick local cleanup inside Odoo, use **Reset Demo Data**.

### Paid Order Does Not Become Paid In Odoo

Check that Accounting is installed and that Odoo has a bank or cash journal.
The module uses Odoo's standard invoice creation, posting, and payment
registration flow.

### Failed Order Does Not Refund In Shopify

Likely causes:

- token lacks `write_orders`
- Shopify order is already cancelled
- Shopify state blocks cancellation
- Shopify accepted the cancellation job but has not completed it yet

Fix:

Open the failed sync order and check **Shopify Refund Status**, **Shopify Cancel
Job ID**, and the `refund_failed_order` event.

### Shopify Webhook Does Not Arrive

Likely causes:

- Odoo is still only available at localhost
- public tunnel URL is not saved on the sync instance
- **Register Order Webhook** was not run after changing the URL
- webhook secret/client secret does not match the Shopify app

Fix:

Expose Odoo through HTTPS, save the callback URL, click **Register Order
Webhook**, and inspect Events for HMAC or topic rejection messages.

## Do Not Overclaim

- Do not call it production-ready.
- Do not claim Odoo validates inside Shopify checkout.
- Do not claim full tax, shipping, partial refund, reconciliation, or return
  management.
- Do not claim final Shopify refund settlement immediately. The module submits
  `orderCancel` and stores Shopify's returned job ID.
- Do not claim a separate dashboard exists. The current operator interface is
  the native Odoo app.

## Production Gaps

- Webhook processing is synchronous for demo clarity.
- A production connector should enqueue webhook work and retry asynchronously.
- There is no dead-letter queue.
- Secrets are stored in Odoo fields for local demo use.
- Multi-store support is limited to multiple sync instance records.
- Whole-order fulfillment is the happy-path demo.

## Closing Sentence

Use this with a technical team:

> This is an Odoo-native Shopify connector demo: Odoo publishes product and stock truth outward, accepts Shopify orders only after Odoo validation, and keeps the full operational trail inside Odoo.
