# Test Suite

The repo has two test layers.

## Static Contract Tests

These run without Odoo and check the repository contract:

- no standalone dashboard/service paths
- Odoo manifest dependencies
- XML view and cron wiring
- access rules
- Python docstrings
- inventory publish safeguards
- job-relevant demo documentation

Run:

```bash
python -m pytest tests
```

## Native Odoo Tests

The module also includes Odoo `TransactionCase` tests in:

```text
odoo/addons/shopify_sync_demo/tests/
```

Run inside the container:

```bash
docker compose exec -T odoo odoo \
  -c /etc/odoo/odoo.conf \
  -d shopify_odoo_demo \
  --test-enable \
  --test-tags /shopify_sync_demo \
  --http-port=8070 \
  --stop-after-init \
  --db_host=odoo-db \
  --db_user=odoo \
  --db_password=odoo
docker compose restart odoo
```

Those tests exercise native Odoo records and patched Shopify API calls without
mutating the live dev store.

## Claimed Demo Path Coverage

| Demo claim | Coverage |
| --- | --- |
| Odoo-only repo, no standalone FastAPI/React dashboard | `test_repo_is_odoo_only` |
| Odoo module dependencies, menus, actions, cron, access rules | static contract tests |
| Connection test reports success/failure | `test_connection_action_records_success_and_failure` |
| Client credentials fallback works when no token is stored | `test_client_credentials_token_fallback_is_supported` |
| Seed creates the canonical Odoo demo catalog | `test_seed_demo_data_creates_only_canonical_active_demo_catalog` |
| Reset clears sync rows and restores the canonical catalog | `test_reset_and_seed_demo_data_clears_sync_state_and_restores_seed_catalog` |
| Catalog refresh creates SKU mappings | `test_refresh_catalog_creates_mapping_for_sku_product` |
| Product publish uses `productSet` and stores Shopify IDs | `test_product_publish_calls_product_set_and_stores_shopify_ids` |
| Product publish errors remain visible on mappings | `test_product_publish_records_user_errors_without_deleting_mapping` |
| Published variants track inventory and deny overselling | `test_product_publish_payload_blocks_shopify_overselling` |
| Webhook registration creates an `orders/create` subscription | `test_register_orders_create_webhook_stores_subscription_id` |
| Webhook HMAC validates the raw request body | `test_webhook_hmac_helper_accepts_only_signed_raw_bodies` |
| Shopify `orders/create` webhook validates and confirms orders | `test_orders_create_webhook_ingests_validates_and_confirms_order` |
| Manual Pull Shopify Orders uses the same validation path | `test_manual_order_pull_reuses_validation_and_sale_order_flow` |
| Missing SKU, non-sellable product, insufficient stock fail validation | order validation tests |
| Price mismatch is a warning, not a blocker | `test_order_validation_blocks_non_sellable_products_and_warns_on_price_drift` |
| Failed validation cancels/refunds in Shopify without restocking | `test_failed_validation_submits_shopify_refund_without_restocking` |
| Valid orders create Odoo sale orders assigned to salesperson `shopify` | `test_valid_order_creates_sale_order_with_shopify_salesperson` |
| One-minute automation confirms Shopify quotations | `test_cron_confirms_published_shopify_quotations` |
| Paid Shopify orders create posted/paid Odoo invoices | `test_paid_shopify_order_creates_posted_paid_odoo_invoice` |
| Inventory publish uses Odoo `free_qty` and fresh idempotency keys | `test_inventory_publish_uses_free_qty_and_fresh_idempotency_keys` |
| Fulfillment pushes open Shopify fulfillment orders only | `test_shopify_fulfillment_helper_uses_open_fulfillment_orders` |
| Mark Fulfilled updates Odoo and Shopify-visible status | `test_mark_fulfilled_updates_odoo_and_shopify_status` |
