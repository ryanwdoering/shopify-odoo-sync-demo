"""Native Odoo workflow tests for the Shopify Sync Demo app.

These tests avoid live Shopify calls by patching the module's GraphQL helper.
They focus on the Odoo business behavior that matters in the demo: catalog
mapping, catalog publishing, webhook registration, order intake, validation,
refunds, payment, fulfillment, and inventory publish payloads.
"""

import base64
import hashlib
import hmac
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from odoo.addons.shopify_sync_demo.controllers.shopify_webhooks import ShopifyWebhookController
from odoo.addons.shopify_sync_demo.models.shopify_sync import (
    DEFAULT_DEMO_PRODUCTS,
    ShopifySyncInstance,
    ShopifySyncProductMapping,
)


@tagged("post_install", "-at_install")
class TestShopifySyncDemo(TransactionCase):
    """Exercise the native module without depending on live Shopify credentials."""

    @classmethod
    def setUpClass(cls):
        """Create one isolated Shopify instance for all transaction-scoped tests."""
        super().setUpClass()
        cls.instance = cls.env["shopify.sync.instance"].create(
            {
                "name": "Test Shopify",
                "shop_domain": "example.myshopify.com",
                "api_version": "2026-04",
                "access_token": "test-token",
            }
        )
        cls.stock_location = cls.instance._demo_stock_location()

    def _product(self, sku="TEST-SKU", name="Test Product", price=10.0, quantity=5.0, sale_ok=True):
        """Create a stockable product and apply a deterministic on-hand quantity."""
        values = {
            "name": name,
            "default_code": sku,
            "list_price": price,
            "sale_ok": sale_ok,
            "purchase_ok": False,
        }
        if "is_storable" in self.env["product.template"]._fields:
            values["is_storable"] = True
        product = self.env["product.template"].create(values)
        self.instance._set_product_quantity(product.product_variant_id, self.stock_location, quantity)
        product.product_variant_id.invalidate_recordset()
        return product

    def _shopify_order(self, sku, quantity=1.0, price=10.0):
        """Create a normalized Shopify order payload for native intake tests."""
        return {
            "shopify_gid": "gid://shopify/Order/test-%s" % sku,
            "shopify_id": "test-%s" % sku,
            "order_number": "#TEST-%s" % sku,
            "customer_name": "Test Buyer",
            "customer_email": "buyer@example.com",
            "street": "1 Test Way",
            "city": "Redmond",
            "country": "United States",
            "total_amount": quantity * price,
            "currency": "USD",
            "financial_status": "PENDING",
            "fulfillment_status": "UNFULFILLED",
            "line_items": [
                {
                    "shopify_line_gid": "gid://shopify/LineItem/test-%s" % sku,
                    "title": "Line for %s" % sku,
                    "sku": sku,
                    "quantity": quantity,
                    "price": price,
                    "currency": "USD",
                }
            ],
            "source_payload": {"name": "#TEST-%s" % sku},
        }

    def _shopify_graphql_order_node(self, sku, quantity=1.0, price=10.0, financial_status="PENDING"):
        """Create a Shopify GraphQL order node as returned by the manual orders query."""
        return {
            "id": "gid://shopify/Order/graphql-%s" % sku,
            "legacyResourceId": "graphql-%s" % sku,
            "name": "#GRAPHQL-%s" % sku,
            "email": "graphql-buyer@example.com",
            "displayFinancialStatus": financial_status,
            "displayFulfillmentStatus": "UNFULFILLED",
            "totalPriceSet": {"shopMoney": {"amount": str(quantity * price), "currencyCode": "USD"}},
            "customer": {
                "id": "gid://shopify/Customer/graphql",
                "firstName": "Graphql",
                "lastName": "Buyer",
                "email": "graphql-buyer@example.com",
                "defaultAddress": {"address1": "1 Pull Way", "city": "Austin", "country": "United States"},
            },
            "lineItems": {
                "edges": [
                    {
                        "node": {
                            "id": "gid://shopify/LineItem/graphql-%s" % sku,
                            "title": "Pulled %s" % sku,
                            "quantity": quantity,
                            "sku": sku,
                            "originalUnitPriceSet": {"shopMoney": {"amount": str(price), "currencyCode": "USD"}},
                            "variant": {
                                "id": "gid://shopify/ProductVariant/graphql-%s" % sku,
                                "sku": sku,
                                "product": {"id": "gid://shopify/Product/graphql-%s" % sku, "title": "Pulled Product"},
                            },
                        }
                    }
                ]
            },
        }

    def _shopify_refund_response(self):
        """Return the successful Shopify orderCancel shape used by failed validation paths."""
        return {"orderCancel": {"job": {"id": "gid://shopify/Job/test", "done": False}, "orderCancelUserErrors": []}}

    def test_connection_action_records_success_and_failure(self):
        """The Test Connection button should surface both healthy and broken Shopify credentials."""
        with patch.object(
            ShopifySyncInstance,
            "_graphql",
            return_value={"shop": {"name": "Demo Shop", "myshopifyDomain": "example.myshopify.com"}},
        ):
            result = self.instance.action_test_connection()

        self.assertEqual(result["tag"], "display_notification")
        self.assertEqual(self.instance.last_connection_status, "ok")
        self.assertFalse(self.instance.last_error)
        self.assertTrue(
            self.env["shopify.sync.event"].search(
                [
                    ("instance_id", "=", self.instance.id),
                    ("action", "=", "connection_test"),
                    ("status", "=", "success"),
                ]
            )
        )

        with patch.object(ShopifySyncInstance, "_graphql", side_effect=UserError("bad token")):
            try:
                self.instance.action_test_connection()
            except UserError:
                pass
            else:
                self.fail("action_test_connection should raise when Shopify rejects the credentials")

        self.assertEqual(self.instance.last_connection_status, "failed")
        self.assertIn("bad token", self.instance.last_error)
        self.assertTrue(
            self.env["shopify.sync.event"].search(
                [
                    ("instance_id", "=", self.instance.id),
                    ("action", "=", "connection_test"),
                    ("status", "=", "failed"),
                ]
            )
        )

    def test_client_credentials_token_fallback_is_supported(self):
        """Instances can use Shopify client credentials when no static Admin token is stored."""
        instance = self.env["shopify.sync.instance"].create(
            {
                "name": "Client Credentials Instance",
                "shop_domain": "https://credentials-test.myshopify.com/",
                "api_version": "2026-04",
                "client_id": "client-id",
                "client_secret": "client-secret",
            }
        )

        class FakeResponse:
            """Minimal requests response with the methods used by the adapter."""

            def raise_for_status(self):
                """Simulate an HTTP 2xx response."""

            def json(self):
                """Return a Shopify OAuth token payload."""
                return {"access_token": "client-credentials-token"}

        captured = {}

        def fake_post(url, headers=None, data=None, timeout=None):
            """Capture token request inputs without making a network call."""
            captured.update({"url": url, "headers": headers, "data": data, "timeout": timeout})
            return FakeResponse()

        with patch("odoo.addons.shopify_sync_demo.models.shopify_sync.requests.post", fake_post):
            token = instance._get_access_token()

        self.assertEqual(token, "client-credentials-token")
        self.assertEqual(captured["url"], "https://credentials-test.myshopify.com/admin/oauth/access_token")
        self.assertEqual(captured["data"]["grant_type"], "client_credentials")

    def test_seed_demo_data_creates_only_canonical_active_demo_catalog(self):
        """Seed buttons should create the interview SKU set and hide unrelated SKU products."""
        unrelated = self._product(sku="UNRELATED-SKU", name="Unrelated Product", quantity=8.0)

        self.instance.action_seed_demo_data()

        expected_skus = {item["sku"] for item in DEFAULT_DEMO_PRODUCTS}
        products = self.env["product.template"].with_context(active_test=False).search(
            [("default_code", "in", list(expected_skus))]
        )
        self.assertEqual(set(products.mapped("default_code")), expected_skus)
        for item in DEFAULT_DEMO_PRODUCTS:
            product = products.filtered(lambda product: product.default_code == item["sku"])
            variant = self.env["product.product"].search([("product_tmpl_id", "=", product.id)], limit=1)
            self.assertEqual(product.name, item["name"])
            self.assertEqual(product.list_price, item["price"])
            self.assertTrue(variant)
            self.assertTrue(variant.active)
            self.assertEqual(variant.qty_available, item["quantity"])

        unrelated.invalidate_recordset()
        self.assertFalse(unrelated.active)
        self.assertTrue(
            self.env["shopify.sync.product.mapping"].search(
                [("instance_id", "=", self.instance.id), ("sku", "in", list(expected_skus))]
            )
        )

    def test_reset_and_seed_demo_data_clears_sync_state_and_restores_seed_catalog(self):
        """Reset Demo Data should clear old sync rows and restore only the canonical SKU set."""
        old_product = self._product(sku="OLD-DEMO-SKU", name="Old Demo Product", quantity=2.0)
        old_mapping = self.instance._ensure_mapping(old_product)
        old_order = self.env["shopify.sync.order"].create_or_update_from_shopify(
            self.instance,
            self._shopify_order("OLD-DEMO-SKU", quantity=1.0, price=old_product.list_price),
        )
        self.instance._log_event("old_event", "success", "event to clear")

        self.instance.action_reset_and_seed_demo_data()

        expected_skus = {item["sku"] for item in DEFAULT_DEMO_PRODUCTS}
        old_product.invalidate_recordset()
        self.assertFalse(old_product.active)
        self.assertFalse(self.env["shopify.sync.product.mapping"].browse(old_mapping.id).exists())
        self.assertFalse(self.env["shopify.sync.order"].browse(old_order.id).exists())
        self.assertEqual(
            set(
                self.env["shopify.sync.product.mapping"]
                .search([("instance_id", "=", self.instance.id)])
                .mapped("sku")
            ),
            expected_skus,
        )
        for item in DEFAULT_DEMO_PRODUCTS:
            variant = self.env["product.product"].search([("default_code", "=", item["sku"])], limit=1)
            self.assertTrue(variant)
            self.assertEqual(variant.qty_available, item["quantity"])
        self.assertTrue(
            self.env["shopify.sync.event"].search(
                [
                    ("instance_id", "=", self.instance.id),
                    ("action", "=", "reset_and_seed_demo_data"),
                    ("status", "=", "success"),
                ]
            )
        )

    def test_refresh_catalog_creates_mapping_for_sku_product(self):
        """Refreshing catalog should create a native mapping for each active Odoo SKU."""
        product = self._product(sku="MAP-SKU", name="Mapped Product")

        self.instance.action_refresh_catalog()

        mapping = self.env["shopify.sync.product.mapping"].search(
            [("instance_id", "=", self.instance.id), ("sku", "=", "MAP-SKU")],
            limit=1,
        )
        self.assertEqual(mapping.product_template_id, product)
        self.assertEqual(mapping.product_variant_id, product.product_variant_id)

    def test_refresh_catalog_uses_inventory_products_and_removes_stale_rows(self):
        """Shopify Sync catalog should mirror active stockable Inventory app SKUs."""
        inventory_product = self._product(sku="INV-CATALOG-SKU", name="Inventory Catalog Product")
        service_product = self.env["product.template"].create(
            {
                "name": "Service With SKU",
                "default_code": "SERVICE-SKU",
                "list_price": 99.0,
                "sale_ok": True,
                "purchase_ok": False,
                "is_storable": False,
            }
        )
        stale_product = self._product(sku="STALE-CATALOG-SKU", name="Stale Catalog Product")
        stale_mapping = self.instance._ensure_mapping(stale_product)
        stale_product.write({"active": False})

        self.instance.action_refresh_catalog()

        mapped_skus = set(
            self.env["shopify.sync.product.mapping"]
            .search([("instance_id", "=", self.instance.id)])
            .mapped("sku")
        )
        self.assertIn(inventory_product.default_code, mapped_skus)
        self.assertNotIn(service_product.default_code, mapped_skus)
        self.assertFalse(stale_mapping.exists())

    def test_product_publish_calls_product_set_and_stores_shopify_ids(self):
        """Publish Products should upsert Shopify catalog data and persist returned Shopify IDs."""
        product = self._product(sku="PUBLISH-SKU", name="Published Product", price=32.0)
        mapping = self.env["shopify.sync.product.mapping"].create(
            {
                "instance_id": self.instance.id,
                "product_template_id": product.id,
                "product_variant_id": product.product_variant_id.id,
                "sku": "PUBLISH-SKU",
            }
        )
        captured = []

        def fake_graphql(record, query, variables=None):
            """Return the productSet response while capturing Shopify variables."""
            captured.append({"query": query, "variables": variables})
            return {
                "productSet": {
                    "product": {
                        "id": "gid://shopify/Product/publish",
                        "title": "Published Product",
                        "variants": {
                            "nodes": [
                                {
                                    "id": "gid://shopify/ProductVariant/publish",
                                    "sku": "PUBLISH-SKU",
                                    "inventoryItem": {"id": "gid://shopify/InventoryItem/publish"},
                                }
                            ]
                        },
                    },
                    "userErrors": [],
                }
            }

        with patch.object(ShopifySyncInstance, "_find_variant_by_sku", return_value=None):
            with patch.object(ShopifySyncInstance, "_graphql", fake_graphql):
                mapping.action_publish_product()

        self.assertEqual(mapping.product_status, "published")
        self.assertEqual(mapping.shopify_product_gid, "gid://shopify/Product/publish")
        self.assertEqual(mapping.shopify_variant_gid, "gid://shopify/ProductVariant/publish")
        self.assertEqual(mapping.shopify_inventory_item_gid, "gid://shopify/InventoryItem/publish")
        variant = captured[0]["variables"]["productSet"]["variants"][0]
        self.assertEqual(variant["inventoryPolicy"], "DENY")
        self.assertEqual(variant["inventoryItem"], {"sku": "PUBLISH-SKU", "tracked": True})

    def test_product_publish_records_user_errors_without_deleting_mapping(self):
        """Shopify productSet user errors should leave a visible failed mapping for operators."""
        product = self._product(sku="PUBLISH-FAIL-SKU", name="Publish Failure Product")
        mapping = self.env["shopify.sync.product.mapping"].create(
            {
                "instance_id": self.instance.id,
                "product_template_id": product.id,
                "product_variant_id": product.product_variant_id.id,
                "sku": "PUBLISH-FAIL-SKU",
            }
        )

        def fake_graphql(record, query, variables=None):
            """Return a Shopify productSet user error."""
            return {"productSet": {"product": None, "userErrors": [{"field": ["title"], "message": "Invalid title"}]}}

        with patch.object(ShopifySyncInstance, "_find_variant_by_sku", return_value=None):
            with patch.object(ShopifySyncInstance, "_graphql", fake_graphql):
                mapping.action_publish_product()

        self.assertEqual(mapping.product_status, "failed")
        self.assertIn("Invalid title", mapping.last_error)
        self.assertTrue(mapping.exists())

    def test_register_orders_create_webhook_stores_subscription_id(self):
        """Register Order Webhook should create Shopify's orders/create subscription."""
        self.instance.webhook_callback_url = "https://example-tunnel.test/shopify_sync_demo/webhooks/orders_create?db=test"
        captured = {}

        def fake_graphql(record, query, variables=None):
            """Capture webhook subscription variables without calling Shopify."""
            captured.update(variables)
            return {
                "webhookSubscriptionCreate": {
                    "webhookSubscription": {
                        "id": "gid://shopify/WebhookSubscription/orders-create",
                        "topic": "ORDERS_CREATE",
                        "uri": self.instance.webhook_callback_url,
                    },
                    "userErrors": [],
                }
            }

        with patch.object(ShopifySyncInstance, "_graphql", fake_graphql):
            self.instance.action_register_orders_create_webhook()

        self.assertEqual(self.instance.orders_create_webhook_gid, "gid://shopify/WebhookSubscription/orders-create")
        self.assertEqual(captured["topic"], "ORDERS_CREATE")
        self.assertEqual(captured["webhookSubscription"]["uri"], self.instance.webhook_callback_url)

    def test_order_validation_blocks_missing_sku_and_insufficient_stock(self):
        """Invalid Shopify lines should fail before an Odoo sale order can be created."""
        self._product(sku="LOW-STOCK", quantity=1.0)
        missing_sku_order = self.env["shopify.sync.order"].create_or_update_from_shopify(
            self.instance,
            self._shopify_order("", quantity=1.0),
        )
        low_stock_order = self.env["shopify.sync.order"].create_or_update_from_shopify(
            self.instance,
            self._shopify_order("LOW-STOCK", quantity=2.0),
        )

        with patch.object(ShopifySyncInstance, "_graphql", return_value=self._shopify_refund_response()):
            missing_sku_order.action_validate()
            low_stock_order.action_validate()

        self.assertEqual(missing_sku_order.status, "failed")
        self.assertIn("missing a SKU", missing_sku_order.validation_message)
        self.assertEqual(missing_sku_order.shopify_refund_status, "submitted")
        self.assertEqual(low_stock_order.status, "failed")
        self.assertIn("available to sell", low_stock_order.validation_message)
        self.assertEqual(low_stock_order.shopify_refund_status, "submitted")

    def test_order_validation_blocks_non_sellable_products_and_warns_on_price_drift(self):
        """Validation distinguishes blocking sellability failures from non-blocking price warnings."""
        self._product(sku="NO-SALE-SKU", quantity=5.0, sale_ok=False)
        price_product = self._product(sku="PRICE-WARN-SKU", price=15.0, quantity=5.0)
        no_sale_order = self.env["shopify.sync.order"].create_or_update_from_shopify(
            self.instance,
            self._shopify_order("NO-SALE-SKU", quantity=1.0, price=10.0),
        )
        warning_order = self.env["shopify.sync.order"].create_or_update_from_shopify(
            self.instance,
            self._shopify_order("PRICE-WARN-SKU", quantity=1.0, price=price_product.list_price + 2.0),
        )

        with patch.object(ShopifySyncInstance, "_graphql", return_value=self._shopify_refund_response()):
            no_sale_order.action_validate()
        warning_order.action_validate()

        self.assertEqual(no_sale_order.status, "failed")
        self.assertIn("not sellable", no_sale_order.validation_message)
        self.assertEqual(no_sale_order.shopify_refund_status, "submitted")
        self.assertEqual(warning_order.status, "warning")
        self.assertIn("price differs", warning_order.validation_message)
        self.assertEqual(warning_order.shopify_refund_status, "not_required")

    def test_failed_validation_submits_shopify_refund_without_restocking(self):
        """Rejected Shopify orders should be canceled/refunded through Shopify immediately."""
        product = self._product(sku="REFUND-SKU", quantity=1.0)
        order = self.env["shopify.sync.order"].create_or_update_from_shopify(
            self.instance,
            self._shopify_order("REFUND-SKU", quantity=2.0, price=10.0),
        )
        free_before = product.product_variant_id.free_qty
        captured_variables = []

        def fake_graphql(record, query, variables=None):
            """Capture orderCancel variables without mutating live Shopify."""
            captured_variables.append(variables)
            return {"orderCancel": {"job": {"id": "gid://shopify/Job/refund-test", "done": False}, "orderCancelUserErrors": []}}

        with patch.object(ShopifySyncInstance, "_graphql", fake_graphql):
            order.action_validate()

        refund_variables = captured_variables[0]
        self.assertEqual(order.status, "failed")
        self.assertEqual(order.shopify_refund_status, "submitted")
        self.assertEqual(order.shopify_cancel_job_gid, "gid://shopify/Job/refund-test")
        self.assertEqual(refund_variables["orderId"], order.shopify_gid)
        self.assertEqual(refund_variables["refundMethod"], {"originalPaymentMethodsRefund": True})
        self.assertEqual(refund_variables["reason"], "INVENTORY")
        self.assertFalse(refund_variables["restock"])
        self.assertFalse(order.sale_order_id)
        product.product_variant_id.invalidate_recordset()
        self.assertEqual(product.product_variant_id.free_qty, free_before)

    def test_valid_order_creates_sale_order_with_shopify_salesperson(self):
        """Accepted Shopify orders should become Odoo quotations owned by the Shopify user."""
        product = self._product(sku="ORDER-SKU", quantity=5.0)
        order = self.env["shopify.sync.order"].create_or_update_from_shopify(
            self.instance,
            self._shopify_order("ORDER-SKU", quantity=1.0, price=product.list_price),
        )

        order.action_validate()
        order.action_create_sale_order()

        self.assertEqual(order.status, "published")
        self.assertEqual(order.sale_order_id.origin, "Shopify")
        self.assertEqual(order.sale_order_id.client_order_ref, order.order_number)
        self.assertEqual(order.sale_order_id.user_id.login, "shopify")
        self.assertEqual(order.sale_order_id.order_line.product_id, product.product_variant_id)

    def test_manual_order_pull_reuses_validation_and_sale_order_flow(self):
        """Pull Shopify Orders should normalize GraphQL orders and run the same Odoo workflow."""
        product = self._product(sku="PULL-SKU", quantity=5.0, price=21.0)

        def fake_graphql(record, query, variables=None):
            """Return one recent Shopify order for the manual pull path."""
            return {
                "orders": {
                    "edges": [
                        {
                            "node": self._shopify_graphql_order_node(
                                "PULL-SKU",
                                quantity=1.0,
                                price=product.list_price,
                            )
                        }
                    ]
                }
            }

        with patch.object(ShopifySyncInstance, "_graphql", fake_graphql):
            self.instance.action_pull_orders()

        order = self.env["shopify.sync.order"].search(
            [("instance_id", "=", self.instance.id), ("order_number", "=", "#GRAPHQL-PULL-SKU")],
            limit=1,
        )
        self.assertTrue(order)
        self.assertEqual(order.status, "confirmed")
        self.assertEqual(order.sale_order_id.state, "sale")
        self.assertEqual(order.line_ids.product_id, product.product_variant_id)
        self.assertTrue(
            self.env["shopify.sync.event"].search(
                [
                    ("instance_id", "=", self.instance.id),
                    ("action", "=", "pull_orders"),
                    ("status", "=", "success"),
                ]
            )
        )

    def test_cron_confirms_published_shopify_quotations(self):
        """The 1-minute automation should confirm Shopify-owned draft quotations."""
        product = self._product(sku="CRON-SKU", quantity=5.0, price=19.0)
        order = self.env["shopify.sync.order"].create_or_update_from_shopify(
            self.instance,
            self._shopify_order("CRON-SKU", quantity=1.0, price=product.list_price),
        )
        order.action_validate()
        order.action_create_sale_order()

        confirmed_count = self.env["shopify.sync.order"]._cron_confirm_new_shopify_quotations()

        self.assertGreaterEqual(confirmed_count, 1)
        self.assertEqual(order.sale_order_id.state, "sale")
        self.assertEqual(order.status, "confirmed")

    def test_cron_does_not_confirm_unvalidated_direct_shopify_quotations(self):
        """Inventory should not reserve for Shopify-origin sale orders outside validation."""
        product = self._product(sku="DIRECT-QUOTE-SKU", quantity=5.0, price=11.0)
        partner = self.env["res.partner"].create({"name": "Direct Buyer"})
        direct_order = self.env["sale.order"].create(
            {
                "partner_id": partner.id,
                "origin": "Shopify",
                "client_order_ref": "#DIRECT-UNVALIDATED",
                "order_line": [
                    (
                        0,
                        0,
                        {
                            "product_id": product.product_variant_id.id,
                            "product_uom_qty": 1.0,
                            "price_unit": product.list_price,
                        },
                    )
                ],
            }
        )
        free_before = product.product_variant_id.free_qty

        confirmed_count = self.env["shopify.sync.order"]._cron_confirm_new_shopify_quotations()

        product.product_variant_id.invalidate_recordset()
        direct_order.invalidate_recordset()
        self.assertEqual(confirmed_count, 0)
        self.assertEqual(direct_order.state, "draft")
        self.assertEqual(product.product_variant_id.free_qty, free_before)

    def test_unvalidated_shopify_sale_order_cannot_mark_paid_or_publish_inventory(self):
        """Paid-order automation must require a validated Shopify Sync order link."""
        product = self._product(sku="DIRECT-PAID-SKU", quantity=5.0, price=13.0)
        partner = self.env["res.partner"].create({"name": "Direct Paid Buyer"})
        direct_order = self.env["sale.order"].create(
            {
                "partner_id": partner.id,
                "origin": "Shopify",
                "client_order_ref": "#DIRECT-PAID",
                "order_line": [
                    (
                        0,
                        0,
                        {
                            "product_id": product.product_variant_id.id,
                            "product_uom_qty": 1.0,
                            "price_unit": product.list_price,
                        },
                    )
                ],
            }
        )
        free_before = product.product_variant_id.free_qty

        with self.assertRaises(UserError):
            direct_order.action_shopify_mark_paid()

        product.product_variant_id.invalidate_recordset()
        direct_order.invalidate_recordset()
        self.assertEqual(direct_order.state, "draft")
        self.assertEqual(product.product_variant_id.free_qty, free_before)

    def test_paid_shopify_order_creates_posted_paid_odoo_invoice(self):
        """If Shopify reports paid, Odoo should confirm the sale and register payment."""
        product = self._product(sku="PAID-SKU", quantity=5.0, price=44.0)
        payload = self._shopify_order("PAID-SKU", quantity=1.0, price=product.list_price)
        payload["financial_status"] = "PAID"
        order = self.env["shopify.sync.order"].create_or_update_from_shopify(self.instance, payload)

        order.action_validate()
        order.action_create_sale_order()
        order._confirm_linked_sale_order()

        invoices = order.sale_order_id.invoice_ids.filtered(lambda move: move.move_type == "out_invoice")
        self.assertEqual(order.sale_order_id.state, "sale")
        self.assertEqual(order.status, "paid")
        self.assertTrue(invoices)
        self.assertTrue(all(invoice.state == "posted" for invoice in invoices))
        self.assertTrue(all(invoice.payment_state == "paid" for invoice in invoices))

    def test_product_publish_payload_blocks_shopify_overselling(self):
        """Published Shopify variants should track inventory and deny sold-out purchases."""
        product = self._product(sku="DENY-SKU", quantity=0.0)
        mapping = self.env["shopify.sync.product.mapping"].create(
            {
                "instance_id": self.instance.id,
                "product_template_id": product.id,
                "product_variant_id": product.product_variant_id.id,
                "sku": "DENY-SKU",
            }
        )

        variant_payload = mapping._product_payload()["variants"][0]

        self.assertEqual(variant_payload["inventoryPolicy"], "DENY")
        self.assertTrue(variant_payload["inventoryItem"]["tracked"])

    def test_webhook_hmac_helper_accepts_only_signed_raw_bodies(self):
        """The public controller should verify Shopify HMACs over the exact raw body."""
        self.instance.write({"webhook_secret": "webhook-secret"})
        controller = ShopifyWebhookController()
        raw_body = b'{"id":123,"name":"#1001"}'
        signature = base64.b64encode(hmac.new(b"webhook-secret", raw_body, hashlib.sha256).digest()).decode(
            "utf-8"
        )

        self.assertTrue(controller._valid_hmac(self.instance, raw_body, signature))
        self.assertFalse(controller._valid_hmac(self.instance, raw_body + b" ", signature))
        self.assertEqual(controller._normalize_shop_domain("https://example.myshopify.com/"), "example.myshopify.com")

    def test_orders_create_webhook_ingests_validates_and_confirms_order(self):
        """The webhook path should reuse the same Odoo validation and sale-order workflow."""
        product = self._product(sku="HOOK-SKU", quantity=5.0)
        order = self.instance.process_order_create_webhook(
            {
                "id": 12345,
                "admin_graphql_api_id": "gid://shopify/Order/12345",
                "name": "#HOOK-1001",
                "email": "hook-buyer@example.com",
                "currency": "USD",
                "financial_status": "pending",
                "fulfillment_status": None,
                "total_price": str(product.list_price),
                "customer": {
                    "first_name": "Hook",
                    "last_name": "Buyer",
                    "email": "hook-buyer@example.com",
                    "default_address": {"address1": "1 Webhook Way", "city": "Portland", "country": "United States"},
                },
                "line_items": [
                    {
                        "id": 67890,
                        "admin_graphql_api_id": "gid://shopify/LineItem/67890",
                        "title": "Hook Product",
                        "sku": "HOOK-SKU",
                        "quantity": 1,
                        "price": str(product.list_price),
                    }
                ],
            },
            headers={"X-Shopify-Topic": "orders/create", "X-Shopify-Webhook-Id": "test-webhook-id"},
        )

        self.assertEqual(order.order_number, "#HOOK-1001")
        self.assertEqual(order.status, "confirmed")
        self.assertEqual(order.sale_order_id.state, "sale")
        self.assertEqual(order.sale_order_id.user_id.login, "shopify")
        self.assertTrue(self.instance.last_webhook_at)
        self.assertTrue(
            self.env["shopify.sync.event"].search(
                [
                    ("instance_id", "=", self.instance.id),
                    ("action", "=", "orders_create_webhook"),
                    ("message", "ilike", "#HOOK-1001"),
                ]
            )
        )

    def test_shopify_fulfillment_helper_uses_open_fulfillment_orders(self):
        """Fulfillment should create Shopify fulfillment records for open fulfillment orders only."""
        captured = []

        def fake_graphql(record, query, variables=None):
            """Return fulfillmentOrders first, then capture fulfillmentCreate variables."""
            captured.append({"query": query, "variables": variables})
            if "OrderFulfillmentOrders" in query:
                return {
                    "order": {
                        "id": "gid://shopify/Order/fulfill",
                        "name": "#FULFILL",
                        "fulfillmentOrders": {
                            "nodes": [
                                {
                                    "id": "gid://shopify/FulfillmentOrder/open",
                                    "status": "OPEN",
                                    "requestStatus": "UNSUBMITTED",
                                },
                                {
                                    "id": "gid://shopify/FulfillmentOrder/closed",
                                    "status": "CLOSED",
                                    "requestStatus": "UNSUBMITTED",
                                },
                            ]
                        },
                    }
                }
            return {
                "fulfillmentCreate": {
                    "fulfillment": {"id": "gid://shopify/Fulfillment/created", "status": "SUCCESS"},
                    "userErrors": [],
                }
            }

        with patch.object(ShopifySyncInstance, "_graphql", fake_graphql):
            result = self.instance.fulfill_shopify_order("gid://shopify/Order/fulfill")

        self.assertEqual(result["fulfillment"]["id"], "gid://shopify/Fulfillment/created")
        fulfillment_variables = captured[-1]["variables"]["fulfillment"]
        self.assertEqual(
            fulfillment_variables["lineItemsByFulfillmentOrder"],
            [{"fulfillmentOrderId": "gid://shopify/FulfillmentOrder/open"}],
        )
        self.assertFalse(fulfillment_variables["notifyCustomer"])

    def test_mark_fulfilled_updates_odoo_and_shopify_status(self):
        """The Mark Fulfilled button should complete the native order state machine."""
        product = self._product(sku="FULFILL-SKU", quantity=5.0, price=12.0)
        order = self.env["shopify.sync.order"].create_or_update_from_shopify(
            self.instance,
            self._shopify_order("FULFILL-SKU", quantity=1.0, price=product.list_price),
        )
        order.action_validate()
        order.action_create_sale_order()
        order._confirm_linked_sale_order()

        with patch.object(
            ShopifySyncInstance,
            "fulfill_shopify_order",
            return_value={"fulfillment": {"id": "gid://shopify/Fulfillment/test"}},
        ):
            order.action_mark_fulfilled()

        self.assertEqual(order.status, "fulfilled")
        self.assertEqual(order.fulfillment_status, "FULFILLED")
        self.assertTrue(
            self.env["shopify.sync.event"].search(
                [
                    ("instance_id", "=", self.instance.id),
                    ("action", "=", "mark_fulfilled"),
                    ("status", "=", "success"),
                ]
            )
        )

    def test_inventory_publish_uses_free_qty_and_fresh_idempotency_keys(self):
        """Inventory publication should send Odoo free_qty and a new Shopify operation key each time."""
        product = self._product(sku="INV-SKU", quantity=4.0)
        mapping = self.env["shopify.sync.product.mapping"].create(
            {
                "instance_id": self.instance.id,
                "product_template_id": product.id,
                "product_variant_id": product.product_variant_id.id,
                "sku": "INV-SKU",
                "shopify_inventory_item_gid": "gid://shopify/InventoryItem/test",
            }
        )
        captured_variables = []

        def fake_graphql(record, query, variables=None):
            """Capture inventory mutation variables without calling Shopify."""
            captured_variables.append(variables)
            return {
                "inventorySetQuantities": {
                    "inventoryAdjustmentGroup": {"reason": "correction"},
                    "userErrors": [],
                }
            }

        with patch.object(ShopifySyncProductMapping, "_ensure_inventory_item_tracked", return_value={}):
            with patch.object(ShopifySyncInstance, "_graphql", fake_graphql):
                mapping.action_publish_inventory("gid://shopify/Location/test")
                mapping.action_publish_inventory("gid://shopify/Location/test")

        first, second = captured_variables
        self.assertEqual(first["input"]["quantities"][0]["quantity"], 4)
        self.assertEqual(first["input"]["quantities"][0]["changeFromQuantity"], None)
        self.assertNotEqual(first["idempotencyKey"], second["idempotencyKey"])
