# Shopify Sync Demo Guide

This guide frames the repository for a Business Systems & Automation Engineer
conversation. The useful story is not "here is a Shopify connector"; it is "here
is an Odoo customization that protects business operations while automating an
e-commerce edge."

## Talk Track

1. Odoo is the system of record for SKUs, sellable state, available inventory,
   customer records, quotations, invoices, payments, deliveries, and audit
   events.
2. Shopify is the commerce edge. The Odoo module publishes catalog and inventory
   state outward, then accepts Shopify orders only after validation.
3. The native Odoo app gives operators a supportable workflow: test connection,
   seed/reset demo data, refresh catalog, publish products, publish inventory,
   pull orders, register webhooks, confirm quotations, refund failed orders, and
   mark accepted orders fulfilled.
4. Operational support is visible through native records and events, so failed
   validation, Shopify user errors, webhook intake, payment registration, and
   fulfillment warnings can be inspected without reading logs first.

## Role-Relevant Signals

- Odoo customization: models, views, menus, access rules, scheduled actions,
  install hooks, controllers, and native tests.
- Shopify/e-commerce automation: Admin GraphQL product publishing, inventory
  publishing, signed order webhooks, order cancellation/refund submission, and
  fulfillment creation.
- Business systems judgment: Odoo remains inventory truth, failed orders do not
  create sale orders, Shopify cancellation uses `restock: false`, and direct
  sale orders cannot bypass the validation gate.

## Demo Close

The repo shows the shape of a maintainable integration: explicit requirements,
static contracts, native Odoo tests, and SpecOps traceability keep the behavior
auditable as the workflow grows.
