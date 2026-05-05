# Shopify Sync Demo Odoo Module

Native Odoo module for publishing the Odoo Inventory catalog and stock to Shopify,
receiving or pulling Shopify orders into Odoo, validating them, and managing the
accepted order lifecycle inside Odoo.

## Models

| Model | Purpose |
| --- | --- |
| `shopify.sync.instance` | Shopify connection, action buttons, counters, settings. |
| `shopify.sync.product.mapping` | Odoo product to Shopify product/variant/inventory item mapping. |
| `shopify.sync.order` | Shopify order intake record and linked Odoo sale order. |
| `shopify.sync.order.line` | Line-level SKU, stock, sellable-state, and price validation. |
| `shopify.sync.event` | Native audit log for publish, validation, payment, and fulfillment actions. |

The module also extends `sale.order` with Shopify payment and inventory republish
helpers.

## Main Menu

After installation, open **Shopify Sync** in Odoo.

Menus:

- **Dashboard**: sync instance, counters, credentials, primary action buttons
- **Catalog**: Inventory product to Shopify product/variant/inventory item mappings
- **Orders**: webhook-ingested or pulled Shopify orders and validation details
- **Events**: audit trail

## Catalog Flow

1. Odoo Inventory products need an Internal Reference/SKU and must be active stockable goods.
2. **Refresh Inventory Catalog** creates or updates mapping rows from active `product.product` Inventory variants.
3. **Publish Products** writes Odoo name, SKU, price, active state, and inventory
   tracking configuration to Shopify through `productSet`.
4. **Publish Inventory** writes Odoo available-to-sell stock to Shopify through
   `inventorySetQuantities`.

Inventory publishing uses Odoo `product.product.free_qty`, not raw on-hand stock.
That means confirmed Odoo sales orders reduce the quantity Shopify sees.
Published Shopify variants are configured with inventory tracking enabled and
`inventoryPolicy` set to `DENY`. When Odoo publishes zero available quantity,
Shopify's native checkout should treat the variant as sold out instead of
allowing oversell orders.

## Order Flow

1. Shopify sends `orders/create` to
   `/shopify_sync_demo/webhooks/orders_create`, or **Pull Shopify Orders** reads
   recent Shopify orders as a manual fallback.
2. Each line is validated against Odoo:
   - SKU exists
   - product is active
   - product is sellable
   - requested quantity is available
   - price differences are stored as warnings
3. Failed validation keeps the Odoo sync order in `Failed` status and submits
   Shopify `orderCancel` with refund-to-original-payment-method enabled.
4. Valid orders create Odoo quotations.
5. The scheduled action **Shopify Sync: Confirm validated quotations** confirms
   valid Shopify quotations.
6. If Shopify reports financial status `PAID`, the module creates/posts the
   invoice and registers payment in Odoo.
7. Paid orders republish affected Shopify inventory from Odoo `free_qty`.
8. **Mark Fulfilled** validates Odoo delivery operations where possible and then
   creates Shopify fulfillment records.

Failed-order cancellation uses `restock: false` because Odoo remains inventory
truth; after the cancel/refund submission, the module republishes Odoo inventory
for any mapped order lines instead of letting Shopify restore stock blindly.
Shopify processes the cancellation/refund asynchronously, so the Odoo sync order
stores the returned job ID rather than claiming final settlement immediately.

## Webhook Intake

The Odoo controller in `controllers/shopify_webhooks.py` receives Shopify
`orders/create` webhooks, verifies `X-Shopify-Hmac-Sha256` against the raw HTTP
request body, normalizes Shopify's REST-shaped order payload, and calls the same
model-level ingestion workflow used by manual pulls.

Localhost is not reachable from Shopify. For live demos, expose Odoo with a
public HTTPS tunnel and set the sync instance's webhook callback URL to:

```text
https://your-public-host/shopify_sync_demo/webhooks/orders_create?db=shopify_odoo_demo
```

Then click **Register Order Webhook**.

## Scheduled Automation

`data/ir_cron.xml` installs an active scheduled action:

```text
Shopify Sync: Confirm validated quotations
```

It runs every minute and processes only `shopify.sync.order` rows. The
automation validates new orders first, creates the linked quotation only after
validation succeeds, and confirms that linked quotation before any Odoo stock
reservation or Shopify inventory republish can happen.

Direct `sale.order` quotations whose `origin` is `Shopify` are intentionally
ignored unless they are linked to a validated sync order, so inventory cannot be
decremented by bypassing the Odoo validation gate. The automation assigns
salesperson `Shopify` to accepted Shopify orders.

## Seed Data

When a sync instance exists, **Seed Demo Data** creates or updates:

| Product | SKU | Price | Stock |
| --- | --- | ---: | ---: |
| Demo Hoodie | `DEMO-HOODIE-001` | 64.00 | 12 |
| Demo Canvas Tote | `DEMO-TOTE-002` | 24.00 | 20 |
| Demo Ceramic Mug | `DEMO-MUG-003` | 18.00 | 30 |
| Demo Field Cap | `DEMO-CAP-004` | 22.00 | 3 |
| Demo Low Stock Item | `DEMO-LOW-005` | 40.00 | 1 |

**Reset Demo Data** clears native sync rows, cancels/removes visible demo sales
orders when Odoo allows it, hides non-seed products, and reseeds the canonical
catalog. A full `docker compose down -v` reset is still the cleanest way to
remove every historical Odoo row.

## Shopify Requirements

Required Admin API scopes:

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

If scopes change, regenerate the Admin API token and update the sync instance.

## Tests

Fast static tests live in the repo-level `tests/` folder. Native Odoo tests live
in this module's `tests/` folder.

Run the Odoo tests from the repo root:

```bash
make test-odoo
```

The native tests patch Shopify GraphQL calls, so they verify Odoo behavior
without mutating the live Shopify dev store.

## Installation Hook

`hooks.py` creates one default `shopify.sync.instance` when
`SHOPIFY_SHOP_DOMAIN` is available in the Odoo container environment. It also
seeds the canonical demo catalog for that instance.

Credentials can be edited later from the Odoo instance form by system users.
