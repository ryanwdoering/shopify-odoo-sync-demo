# Data Model And State Contracts

These requirements describe the native Odoo records that make the integration
supportable. Shopify IDs are external references; Odoo records own validation,
state transitions, audit events, and business-system truth.

## Sync Instance Ownership

```yaml
id: REQ-DATA-001
status: active
priority: high
component: shopify_sync_demo
kind: data
tags:
  - odoo
  - ownership
  - credentials
acceptance:
  - "`shopify.sync.instance` owns catalog mappings, Shopify order intake records, and audit events."
  - Shopify credentials and webhook signing secrets are stored on the sync instance and restricted to Odoo system users.
  - The sync instance stores Shopify shop domain, API version, webhook callback URL, webhook subscription GID, and connection status.
  - Sync instance action buttons are the operator entrypoints for connection testing, catalog publish, order intake, refund handling, and fulfillment.
```

## Product Mapping Identity

```yaml
id: REQ-DATA-002
status: active
priority: high
component: shopify_sync_demo
kind: data
tags:
  - catalog
  - mapping
  - inventory
acceptance:
  - "`shopify.sync.product.mapping` links exactly one Odoo product template and product variant to one Shopify SKU per sync instance."
  - Mappings enforce uniqueness for SKU and Odoo product template within the same sync instance.
  - Mappings store Shopify product, variant, and inventory item GIDs as durable external references.
  - Mappings expose Odoo price, active state, sellable state, on-hand quantity, and available-to-sell quantity as read-only related fields.
  - Product and inventory publish statuses are limited to `not_published`, `published`, and `failed`.
```

## Order Lifecycle State

```yaml
id: REQ-DATA-003
status: active
priority: high
component: shopify_sync_demo
kind: data
tags:
  - orders
  - state
  - validation
acceptance:
  - A Shopify order is unique per sync instance and Shopify order GID.
  - Order status values are limited to `new`, `validated`, `warning`, `failed`, `published`, `confirmed`, `paid`, and `fulfilled`.
  - Order line validation status values are limited to `new`, `valid`, `warning`, and `failed`.
  - Failed orders do not link to Odoo sale orders and carry validation messages plus refund submission state.
  - Accepted orders link to Odoo partner and sale order records only after validation succeeds.
```

## Audit Event Contract

```yaml
id: REQ-DATA-004
status: active
priority: medium
component: shopify_sync_demo
kind: data
tags:
  - audit
  - operations
acceptance:
  - "`shopify.sync.event` records action, status, message, and optional payload excerpt for operational support."
  - Event status values are limited to `success`, `failed`, and `warning`.
  - Catalog publishing, inventory publishing, order intake, validation, refund submission, payment, and fulfillment paths write events.
  - Events remain native Odoo records visible from the Shopify Sync app.
```
