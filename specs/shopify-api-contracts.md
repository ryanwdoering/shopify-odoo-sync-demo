# Shopify API Contracts

These requirements define the external Shopify Admin GraphQL and webhook
contracts used by the native Odoo Shopify Sync module. They intentionally focus
on operation shape, ownership boundaries, and failure handling rather than
implementation internals.

## Catalog Product Publish Contract

```yaml
id: REQ-SHOPIFYAPI-001
status: active
priority: high
component: shopify_sync_demo
kind: api
tags:
  - shopify
  - catalog
  - productSet
acceptance:
  - Product publishing uses Shopify Admin GraphQL `productSet`.
  - The request carries Odoo product name, SKU, price, active/sellable state, tracked inventory configuration, and deny-oversell policy.
  - Existing Shopify products are matched by stored Shopify product GID or by SKU lookup before a new product is created.
  - Shopify product, variant, and inventory item GIDs returned by the API are stored on the mapping record.
  - Shopify `userErrors` leave the mapping visible in failed state with the returned error message.
```

## Inventory Publish Contract

```yaml
id: REQ-SHOPIFYAPI-002
status: active
priority: high
component: shopify_sync_demo
kind: api
tags:
  - shopify
  - inventory
  - stock
acceptance:
  - Inventory publishing resolves a Shopify location before writing stock.
  - The module calls `inventoryItemUpdate` to ensure the Shopify inventory item is tracked.
  - The module calls `inventorySetQuantities` using Odoo `product.product.free_qty` as available-to-sell quantity.
  - Each Shopify inventory write uses a fresh idempotency key for the operation.
  - The mapping stores a stable inventory payload hash and publish timestamp for audit visibility.
```

## Order Intake API Contract

```yaml
id: REQ-SHOPIFYAPI-003
status: active
priority: high
component: shopify_sync_demo
kind: api
tags:
  - shopify
  - orders
  - webhooks
acceptance:
  - Manual order recovery reads recent Shopify orders through Admin GraphQL.
  - Live order intake uses a registered Shopify `ORDERS_CREATE` webhook.
  - The webhook controller verifies `X-Shopify-Hmac-Sha256` against the exact raw HTTP body before dispatching to Odoo models.
  - Manual pull payloads and REST-shaped webhook payloads are normalized into the same native order schema.
  - Automated tests patch Shopify calls and do not mutate the live Shopify dev store.
```

## Reversal And Fulfillment API Contract

```yaml
id: REQ-SHOPIFYAPI-004
status: active
priority: high
component: shopify_sync_demo
kind: api
tags:
  - shopify
  - refund
  - fulfillment
acceptance:
  - Rejected orders submit Shopify `orderCancel` with refund-to-original-payment-method enabled.
  - "Rejected-order cancellation sends `restock: false` because Odoo remains inventory truth."
  - The returned Shopify cancel job GID is stored instead of claiming final refund settlement synchronously.
  - Fulfillment reads Shopify fulfillment orders and creates fulfillments only for open fulfillment orders.
  - Fulfillment API user errors are surfaced to the operator instead of being silently ignored.
```
