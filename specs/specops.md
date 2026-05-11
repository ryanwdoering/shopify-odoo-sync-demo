# SpecOps Requirements

These requirements define the maintained product contract for the native Odoo
Shopify Sync demo. Keep implementation and tests referencing the requirement IDs
when behavior changes, then run `make specops`.

## Native Odoo Runtime

```yaml
id: REQ-ODOO-001
status: active
priority: high
component: shopify_sync_demo
kind: feature
tags:
  - odoo
  - runtime
acceptance:
  - The repository runs as an Odoo 19 module, not as the removed standalone backend/frontend demo.
  - The module declares Odoo business app dependencies for products, stock, sales, and accounting.
  - Native menus, views, access rules, scheduled actions, install hooks, and HTTP controllers are part of the maintained surface.
```

## Odoo-Led Catalog And Inventory Publishing

```yaml
id: REQ-CATALOG-001
status: active
priority: high
component: shopify_sync_demo
kind: feature
tags:
  - catalog
  - inventory
  - shopify
acceptance:
  - Odoo Inventory products with SKUs are mapped to Shopify product, variant, and inventory item identifiers.
  - Product publish writes Odoo product name, SKU, active/sellable state, price, tracked inventory, and deny-oversell policy to Shopify.
  - Inventory publish sends Odoo available-to-sell quantity from `free_qty`, never raw on-hand quantity.
  - Repeated inventory publishes use fresh Shopify idempotency keys while preserving stable payload hashes for audit visibility.
```

## Signed Shopify Order Intake

```yaml
id: REQ-ORDER-001
status: active
priority: high
component: shopify_sync_demo
kind: security
tags:
  - webhook
  - orders
  - validation
acceptance:
  - Shopify `orders/create` webhooks are accepted only after raw-body HMAC verification.
  - Manual order pull and webhook intake normalize orders into the same Odoo validation workflow.
  - Odoo validates each order line for SKU presence, active product match, sellable state, available quantity, and price drift.
  - Accepted orders create Odoo quotations only after validation succeeds.
```

## Rejected Order Cancellation And Refund Submission

```yaml
id: REQ-REFUND-001
status: active
priority: high
component: shopify_sync_demo
kind: feature
tags:
  - refunds
  - cancellation
  - inventory
acceptance:
  - Orders that fail validation stay failed in Odoo and do not create Odoo sale orders.
  - Failed orders submit Shopify `orderCancel` with refund-to-original-payment-method enabled.
  - "Shopify cancellation uses `restock: false` because Odoo remains inventory truth."
  - The module stores the asynchronous Shopify cancel job ID and republishes Odoo inventory after failed-order handling.
```

## Validated Quotation Automation

```yaml
id: REQ-AUTO-001
status: active
priority: high
component: shopify_sync_demo
kind: feature
tags:
  - automation
  - sales
  - payments
acceptance:
  - The installed scheduled action runs every minute and processes only native Shopify Sync orders.
  - Validated Shopify quotations are confirmed through Odoo sales flow.
  - Shopify-paid orders create, post, and mark Odoo invoices paid through the guarded sync-order path.
  - Direct Shopify-origin sale orders that bypass validation are not confirmed, marked paid, or allowed to publish inventory.
```

## Fulfillment Closure

```yaml
id: REQ-FULFILL-001
status: active
priority: medium
component: shopify_sync_demo
kind: feature
tags:
  - fulfillment
  - delivery
acceptance:
  - Accepted orders can be marked fulfilled only after the Odoo sale order exists and the sync order is in an accepted state.
  - The fulfillment action confirms draft quotations if needed, validates Odoo deliveries where possible, and calls Shopify fulfillment creation for open fulfillment orders.
  - Fulfillment status and audit events are updated in native Odoo records.
```

## SpecOps Maintenance Gate

```yaml
id: REQ-MAINT-001
status: active
priority: medium
component: repo_operations
kind: quality
tags:
  - specops
  - maintenance
acceptance:
  - SpecOps scans the real Odoo module, root maintenance files, and both static and native Odoo test suites.
  - The repository exposes a `make specops` command that runs the local SpecOps audit.
  - Active requirements keep both code and test evidence references.
```
