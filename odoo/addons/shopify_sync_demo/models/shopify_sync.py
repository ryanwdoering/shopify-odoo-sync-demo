"""Native Odoo models for the Shopify Sync demo.

The module keeps the integration inside Odoo: catalog publishing, inventory
projection, Shopify order intake, line validation, paid-order automation, and
fulfillment all live as Odoo models and actions.
"""

import hashlib
import json
import logging
import uuid

import requests

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools.float_utils import float_compare

_logger = logging.getLogger(__name__)

DEFAULT_DEMO_PRODUCTS = [
    {"name": "Demo Hoodie", "sku": "DEMO-HOODIE-001", "price": 64.0, "quantity": 12.0},
    {"name": "Demo Canvas Tote", "sku": "DEMO-TOTE-002", "price": 24.0, "quantity": 20.0},
    {"name": "Demo Ceramic Mug", "sku": "DEMO-MUG-003", "price": 18.0, "quantity": 30.0},
    {"name": "Demo Field Cap", "sku": "DEMO-CAP-004", "price": 22.0, "quantity": 3.0},
    {"name": "Demo Low Stock Item", "sku": "DEMO-LOW-005", "price": 40.0, "quantity": 1.0},
]
PAID_SHOPIFY_FINANCIAL_STATUSES = {"PAID"}


class ShopifySyncInstance(models.Model):
    """Connection record and workflow entrypoint for the native Odoo Shopify console."""

    _name = "shopify.sync.instance"
    _description = "Shopify Sync Instance"

    name = fields.Char(required=True, default="Odoo Sync Demo")
    active = fields.Boolean(default=True)
    shop_domain = fields.Char(required=True, help="Example: my-store.myshopify.com")
    api_version = fields.Char(required=True, default="2026-04")
    access_token = fields.Char(groups="base.group_system")
    client_id = fields.Char(groups="base.group_system")
    client_secret = fields.Char(groups="base.group_system")
    webhook_secret = fields.Char(
        groups="base.group_system",
        help="Optional Shopify webhook signing secret. When blank, the app client secret is used.",
    )
    webhook_callback_url = fields.Char(
        help="Public HTTPS endpoint for Shopify orders/create webhooks, usually a tunnel URL ending in /shopify_sync_demo/webhooks/orders_create.",
    )
    orders_create_webhook_gid = fields.Char(string="Orders/Create Webhook ID", readonly=True)
    last_webhook_at = fields.Datetime(readonly=True)
    auto_confirm_quotations = fields.Boolean(
        string="Auto-confirm Shopify Quotations",
        default=True,
        help="Automatically validate Shopify sync orders, create quotations, and confirm them as sales orders.",
    )
    last_connection_status = fields.Selection(
        [("unknown", "Unknown"), ("ok", "Connected"), ("failed", "Failed")],
        default="unknown",
        readonly=True,
    )
    last_error = fields.Text(readonly=True)
    last_synced_at = fields.Datetime(readonly=True)
    mapping_ids = fields.One2many("shopify.sync.product.mapping", "instance_id", string="Catalog Mappings")
    order_ids = fields.One2many("shopify.sync.order", "instance_id", string="Orders")
    event_ids = fields.One2many("shopify.sync.event", "instance_id", string="Events")
    product_count = fields.Integer(compute="_compute_counts")
    published_product_count = fields.Integer(compute="_compute_counts")
    order_count = fields.Integer(compute="_compute_counts")
    failed_order_count = fields.Integer(compute="_compute_counts")
    event_count = fields.Integer(compute="_compute_counts")

    @api.depends("mapping_ids.product_status", "order_ids.status", "event_ids")
    def _compute_counts(self):
        """Compute dashboard counters shown on the sync instance list/form."""
        for instance in self:
            instance.product_count = len(instance.mapping_ids)
            instance.published_product_count = len(instance.mapping_ids.filtered(lambda item: item.product_status == "published"))
            instance.order_count = len(instance.order_ids)
            instance.failed_order_count = len(instance.order_ids.filtered(lambda item: item.status == "failed"))
            instance.event_count = len(instance.event_ids)

    def action_open_catalog(self):
        """Open catalog mappings filtered to this Shopify instance."""
        self.ensure_one()
        return self._window_action(_("Catalog"), "shopify.sync.product.mapping", [("instance_id", "=", self.id)])

    def action_open_orders(self):
        """Open pulled Shopify orders filtered to this Shopify instance."""
        self.ensure_one()
        return self._window_action(_("Orders"), "shopify.sync.order", [("instance_id", "=", self.id)])

    def action_open_events(self):
        """Open audit events filtered to this Shopify instance."""
        self.ensure_one()
        return self._window_action(_("Events"), "shopify.sync.event", [("instance_id", "=", self.id)])

    def action_test_connection(self):
        """Verify Shopify credentials without mutating catalog or order state."""
        self.ensure_one()
        try:
            data = self._graphql(
                """
                query ShopInfo {
                  shop {
                    name
                    myshopifyDomain
                  }
                }
                """
            )
        except Exception as exc:
            self.write({"last_connection_status": "failed", "last_error": str(exc)})
            self._log_event("connection_test", "failed", str(exc))
            raise

        shop = data.get("shop") or {}
        self.write({"last_connection_status": "ok", "last_error": False})
        self._log_event("connection_test", "success", _("Connected to %s") % (shop.get("myshopifyDomain") or self.shop_domain))
        return self._display_notification(_("Shopify connection succeeded"), "success")

    def action_refresh_catalog(self):
        """Mirror the active Odoo Inventory SKU catalog into native mapping records."""
        for instance in self:
            products = instance._inventory_catalog_products()
            all_mappings = self.env["shopify.sync.product.mapping"].with_context(active_test=False).search(
                [("instance_id", "=", instance.id)]
            )
            stale_mappings = all_mappings.filtered(lambda mapping: mapping.product_template_id.id not in products.ids)
            stale_mappings.unlink()
            for product in products:
                instance._ensure_mapping(product)
            instance.last_synced_at = fields.Datetime.now()
            instance._log_event("refresh_catalog", "success", _("Refreshed %s Inventory products") % len(products))
        return self._display_notification(_("Inventory catalog mappings refreshed"), "success")

    def action_publish_products(self):
        """Publish Odoo product names/SKUs/prices into Shopify productSet."""
        for instance in self:
            instance.action_refresh_catalog()
            for mapping in instance.mapping_ids.filtered(lambda item: item.product_template_id.active):
                mapping.action_publish_product()
            instance.last_synced_at = fields.Datetime.now()
        return self._display_notification(_("Product publish finished"), "success")

    def action_publish_inventory(self):
        """Publish Odoo available quantity into Shopify inventory levels."""
        for instance in self:
            location_gid = instance._primary_location_gid()
            if not location_gid:
                raise UserError(_("Shopify did not return an inventory location."))
            for mapping in instance.mapping_ids.filtered(lambda item: item.shopify_inventory_item_gid):
                mapping.action_publish_inventory(location_gid)
            instance.last_synced_at = fields.Datetime.now()
        return self._display_notification(_("Inventory publish finished"), "success")

    def action_publish_all(self):
        """Run catalog publish first, then inventory publish using the resulting inventory item IDs."""
        self.action_publish_products()
        self.action_publish_inventory()
        return self._display_notification(_("Catalog and inventory publish finished"), "success")

    def action_pull_orders(self):
        """Read Shopify orders, validate them against Odoo, and create draft Odoo sale orders."""
        for instance in self:
            orders = instance._fetch_orders()
            for order_payload in orders:
                instance._ingest_normalized_order(order_payload, source_action="pull_order")
            instance.last_synced_at = fields.Datetime.now()
            instance._log_event("pull_orders", "success", _("Pulled %s Shopify orders") % len(orders))
        return self._display_notification(_("Order pull finished"), "success")

    def action_register_orders_create_webhook(self):
        """Register Shopify's orders/create webhook against this Odoo instance."""
        for instance in self:
            if not instance.webhook_callback_url:
                raise UserError(_("Set a public webhook callback URL before registering the Shopify webhook."))
            result = instance._graphql(
                """
                mutation RegisterOrderWebhook($topic: WebhookSubscriptionTopic!, $webhookSubscription: WebhookSubscriptionInput!) {
                  webhookSubscriptionCreate(topic: $topic, webhookSubscription: $webhookSubscription) {
                    webhookSubscription {
                      id
                      topic
                      uri
                    }
                    userErrors {
                      field
                      message
                    }
                  }
                }
                """,
                {
                    "topic": "ORDERS_CREATE",
                    "webhookSubscription": {"uri": instance.webhook_callback_url},
                },
            )["webhookSubscriptionCreate"]
            if result.get("userErrors"):
                raise UserError(_("Shopify webhook registration errors: %s") % result["userErrors"])
            subscription = result.get("webhookSubscription") or {}
            instance.write({"orders_create_webhook_gid": subscription.get("id")})
            instance._log_event(
                "register_order_webhook",
                "success",
                _("Registered orders/create webhook at %s") % subscription.get("uri"),
                subscription,
            )
        return self._display_notification(_("Shopify orders/create webhook registered"), "success")

    def action_confirm_new_quotations(self):
        """Validate Shopify order records and confirm their draft Odoo quotations."""
        confirmed_count = 0
        for instance in self:
            confirmed_count += instance._confirm_new_quotations()
        return self._display_notification(
            _("Confirmed %s Shopify quotation(s)") % confirmed_count,
            "success",
        )

    def action_seed_demo_data(self):
        """Create the canonical interview catalog and inventory quantities in Odoo."""
        for instance in self:
            instance._ensure_shopify_salesperson()
            instance._seed_demo_products()
            instance.action_refresh_catalog()
            instance._log_event("seed_demo_data", "success", _("Seeded %s demo products") % len(DEFAULT_DEMO_PRODUCTS))
        return self._display_notification(_("Demo catalog seeded"), "success")

    def action_reset_and_seed_demo_data(self):
        """Reset visible demo business records, then recreate only the canonical seed set.

        This action is intended for local demo sandboxes. A full Docker volume reset is
        still the cleanest way to remove all historical stock/accounting rows, but this
        button gives operators a native Odoo recovery path between walkthroughs.
        """
        for instance in self:
            instance._clear_sync_state()
            instance._clear_demo_business_records()
            instance._seed_demo_products()
            instance.action_refresh_catalog()
            instance._log_event("reset_and_seed_demo_data", "success", _("Reset local demo data and seeded catalog"))
        return self._display_notification(_("Demo data reset and seeded"), "success")

    def _ensure_mapping(self, product_template):
        """Find or create the local Odoo-to-Shopify mapping for one product template."""
        self.ensure_one()
        sku = (product_template.default_code or "").strip()
        if not sku:
            raise ValidationError(_("Product %s needs an Internal Reference/SKU.") % product_template.display_name)
        mapping = self.env["shopify.sync.product.mapping"].search(
            ["|", ("sku", "=", sku), ("product_template_id", "=", product_template.id), ("instance_id", "=", self.id)],
            limit=1,
        )
        variant = self._product_variant_for_template(product_template)
        values = {
            "instance_id": self.id,
            "sku": sku,
            "product_template_id": product_template.id,
            "product_variant_id": variant.id,
        }
        if mapping:
            mapping.write(values)
        else:
            mapping = self.env["shopify.sync.product.mapping"].create(values)
        return mapping

    def _inventory_catalog_products(self):
        """Return the active stock-bearing SKU products visible to the Inventory app.

        Shopify Sync deliberately treats Odoo Inventory as catalog truth. The
        sync catalog is therefore derived from active `product.product` variants
        that have SKUs and are configured as stockable goods, then mapped back to
        their templates for Odoo's product form and Shopify's productSet API.
        """
        self.ensure_one()
        product_model = self.env["product.product"]
        domain = [
            ("active", "=", True),
            ("default_code", "!=", False),
            ("product_tmpl_id.active", "=", True),
        ]
        if "is_storable" in product_model._fields:
            domain.append(("is_storable", "=", True))
        elif "type" in product_model._fields:
            domain.append(("type", "in", ["product", "consu"]))
        variants = product_model.search(domain)
        return variants.mapped("product_tmpl_id")

    def _product_variant_for_template(self, product_template):
        """Return the active concrete variant Odoo uses for stock and order lines.

        Demo resets archive product templates, and Odoo archives their product
        variants with them. Re-seeding has to reactivate both layers; otherwise
        the Inventory app shows the template but reports zero on-hand quantity.
        """
        self.ensure_one()
        variant = self.env["product.product"].with_context(active_test=False).search(
            [("product_tmpl_id", "=", product_template.id)],
            limit=1,
        )
        if not variant and hasattr(product_template, "_create_variant_ids"):
            product_template._create_variant_ids()
            variant = self.env["product.product"].with_context(active_test=False).search(
                [("product_tmpl_id", "=", product_template.id)],
                limit=1,
            )
        if variant and not variant.active:
            variant.write({"active": True})
        return variant

    def _demo_stock_location(self):
        """Prefer the warehouse stock location that Odoo's Inventory screens summarize."""
        self.ensure_one()
        warehouse = self.env["stock.warehouse"].search([], limit=1)
        if warehouse and warehouse.lot_stock_id:
            return warehouse.lot_stock_id
        return self.env["stock.location"].search([("usage", "=", "internal")], limit=1)

    def _seed_demo_products(self):
        """Upsert the canonical SKU set and apply stock counts through stock.quant."""
        self.ensure_one()
        stock_location = self._demo_stock_location()
        if not stock_location:
            raise UserError(_("No internal stock location is available for inventory seeding."))

        seeded_skus = {item["sku"] for item in DEFAULT_DEMO_PRODUCTS}
        self.env["product.template"].with_context(active_test=False).search(
            [("default_code", "!=", False), ("default_code", "not in", list(seeded_skus))]
        ).write({"active": False})

        for item in DEFAULT_DEMO_PRODUCTS:
            product = self.env["product.template"].with_context(active_test=False).search(
                [("default_code", "=", item["sku"])],
                limit=1,
            )
            values = {
                "name": item["name"],
                "default_code": item["sku"],
                "list_price": item["price"],
                "sale_ok": True,
                "purchase_ok": False,
                "active": True,
            }
            if "is_storable" in self.env["product.template"]._fields:
                values["is_storable"] = True
            if product:
                product.write(values)
            else:
                product = self.env["product.template"].create(values)
            variant = self._product_variant_for_template(product)
            self._set_product_quantity(variant, stock_location, item["quantity"])

    def _set_product_quantity(self, product_variant, location, quantity):
        """Apply a physical inventory adjustment for one seeded product variant."""
        if not product_variant:
            raise UserError(_("Cannot seed stock because the product variant was not created."))
        quant_model = self.env["stock.quant"].with_context(inventory_mode=True)
        quant = quant_model.search([("product_id", "=", product_variant.id), ("location_id", "=", location.id)], limit=1)
        if not quant:
            quant = quant_model.create(
                {
                    "product_id": product_variant.id,
                    "location_id": location.id,
                    "inventory_quantity": float(quantity),
                }
            )
        else:
            quant.inventory_quantity = float(quantity)
        quant.action_apply_inventory()

    def _clear_sync_state(self):
        """Delete native sync rows so the Odoo console starts from a blank queue."""
        self.ensure_one()
        self.order_ids.unlink()
        self.mapping_ids.unlink()
        self.event_ids.unlink()

    def _clear_demo_business_records(self):
        """Hide or cancel business records that would pollute the local demo surface."""
        sale_orders = self.env["sale.order"].search([])
        for order in sale_orders:
            if order.state not in {"cancel"}:
                try:
                    order.action_cancel()
                except Exception as exc:
                    _logger.info("Could not cancel sale order %s during demo reset: %s", order.name, exc)
        try:
            sale_orders.unlink()
        except Exception as exc:
            _logger.info("Could not delete all sale orders during demo reset: %s", exc)

        pickings = self.env["stock.picking"].search([])
        for picking in pickings.filtered(lambda item: item.state not in {"done", "cancel"}):
            try:
                picking.action_cancel()
            except Exception as exc:
                _logger.info("Could not cancel picking %s during demo reset: %s", picking.name, exc)
        for picking in pickings.filtered(lambda item: item.state == "cancel"):
            try:
                picking.unlink()
            except Exception as exc:
                _logger.info("Could not delete picking %s during demo reset: %s", picking.name, exc)

        self.env["product.template"].with_context(active_test=False).search([]).write({"active": False})

    def _ensure_shopify_salesperson(self):
        """Return the internal Odoo user used as salesperson for Shopify orders."""
        self.ensure_one()
        user = self.env["res.users"].with_context(active_test=False).search([("login", "=", "shopify")], limit=1)
        group_user = self.env.ref("base.group_user", raise_if_not_found=False)
        if user:
            values = {"name": "Shopify", "active": True}
            if group_user:
                values["group_ids"] = [(4, group_user.id)]
            user.write(values)
            return user
        values = {"name": "Shopify", "login": "shopify", "active": True}
        if group_user:
            values["group_ids"] = [(6, 0, [group_user.id])]
        return self.env["res.users"].with_context(
            no_reset_password=True,
            mail_create_nosubscribe=True,
        ).create(values)

    def _confirm_new_quotations(self):
        """Confirm only Shopify sync orders that have passed Odoo validation."""
        self.ensure_one()
        confirmed_count = 0
        sync_orders = self.order_ids.filtered(
            lambda order: order.status in {"new", "validated", "warning", "published", "confirmed"}
            and order.status != "fulfilled"
        )
        for order in sync_orders:
            try:
                if order.status == "new":
                    order.action_validate()
                if order.status in {"validated", "warning"} and not order.sale_order_id:
                    order.action_create_sale_order()
                confirmed_count += order._confirm_linked_sale_order()
            except Exception as exc:
                message = str(exc)
                order.write({"last_error": message, "validation_message": message})
                self._log_event("confirm_quotation", "failed", "%s confirmation failed: %s" % (order.order_number, message))
        return confirmed_count

    def process_order_create_webhook(self, payload, headers=None):
        """Ingest and validate one Shopify orders/create webhook payload immediately."""
        self.ensure_one()
        order_payload = self._normalize_order_webhook_payload(payload)
        order = self._ingest_normalized_order(order_payload, source_action="orders_create_webhook")
        self.write({"last_webhook_at": fields.Datetime.now(), "last_synced_at": fields.Datetime.now()})
        self._log_event(
            "orders_create_webhook",
            "success" if order.status != "failed" else "warning",
            _("%s ingested from Shopify webhook with status %s") % (order.order_number, order.status),
            {
                "shopify_webhook_id": (headers or {}).get("X-Shopify-Webhook-Id"),
                "shopify_topic": (headers or {}).get("X-Shopify-Topic"),
                "order_number": order.order_number,
                "status": order.status,
            },
        )
        return order

    def _ingest_normalized_order(self, order_payload, source_action="order_ingest"):
        """Run the shared order-intake state transition for pull and webhook paths."""
        self.ensure_one()
        order = self.env["shopify.sync.order"].create_or_update_from_shopify(self, order_payload)
        order.action_validate()
        if order.status in {"validated", "warning"} and not order.sale_order_id:
            order.action_create_sale_order()
        if self.auto_confirm_quotations and order.sale_order_id and order.status in {"validated", "warning", "published", "confirmed"}:
            order._confirm_linked_sale_order()
        self._log_event(source_action, "success" if order.status != "failed" else "warning", "%s -> %s" % (order.order_number, order.status))
        return order

    def _graphql(self, query, variables=None):
        """Call Shopify Admin GraphQL with static token or client-credentials fallback."""
        self.ensure_one()
        token = self._get_access_token()
        endpoint = "https://%s/admin/api/%s/graphql.json" % (self._normalized_shop_domain(), self.api_version)
        response = requests.post(
            endpoint,
            headers={"Content-Type": "application/json", "X-Shopify-Access-Token": token},
            json={"query": query, "variables": variables or {}},
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("errors"):
            raise UserError(_("Shopify GraphQL error: %s") % payload["errors"])
        return payload.get("data") or {}

    def _webhook_signing_secret(self):
        """Return the secret used to verify Shopify webhook HMAC signatures."""
        self.ensure_one()
        secret = self.webhook_secret or self.client_secret
        if not secret:
            raise UserError(_("Set a Shopify webhook secret or app client secret before receiving webhooks."))
        return secret

    def _get_access_token(self):
        """Resolve the Shopify token source from the native Odoo instance fields."""
        self.ensure_one()
        if self.access_token:
            return self.access_token
        if not self.client_id or not self.client_secret:
            raise UserError(_("Set a Shopify access token or client credentials on the sync instance."))
        response = requests.post(
            "https://%s/admin/oauth/access_token" % self._normalized_shop_domain(),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
            timeout=30,
        )
        response.raise_for_status()
        token = response.json().get("access_token")
        if not token:
            raise UserError(_("Shopify token response did not include access_token."))
        return token

    def _fetch_orders(self):
        """Fetch recent Shopify orders and normalize them for native order records."""
        self.ensure_one()
        query = """
        query DemoOrders($first: Int!) {
          orders(first: $first, sortKey: CREATED_AT, reverse: true) {
            edges {
              node {
                id
                legacyResourceId
                name
                email
                displayFinancialStatus
                displayFulfillmentStatus
                totalPriceSet { shopMoney { amount currencyCode } }
                customer {
                  id
                  firstName
                  lastName
                  email
                  defaultAddress { address1 city country }
                }
                lineItems(first: 50) {
                  edges {
                    node {
                      id
                      title
                      quantity
                      sku
                      originalUnitPriceSet { shopMoney { amount currencyCode } }
                      variant {
                        id
                        sku
                        product { id title }
                      }
                    }
                  }
                }
              }
            }
          }
        }
        """
        data = self._graphql(query, {"first": 50})
        return [self._normalize_order(edge["node"]) for edge in data.get("orders", {}).get("edges", [])]

    def _normalize_order(self, node):
        """Convert Shopify order JSON into the fields used by shopify.sync.order."""
        customer = node.get("customer") or {}
        address = customer.get("defaultAddress") or {}
        total_money = ((node.get("totalPriceSet") or {}).get("shopMoney")) or {}
        customer_name = " ".join(part for part in [customer.get("firstName"), customer.get("lastName")] if part).strip()
        lines = []
        for edge in ((node.get("lineItems") or {}).get("edges")) or []:
            line = edge["node"]
            variant = line.get("variant") or {}
            product = variant.get("product") or {}
            price_money = ((line.get("originalUnitPriceSet") or {}).get("shopMoney")) or {}
            lines.append(
                {
                    "shopify_line_gid": line.get("id"),
                    "title": line.get("title") or product.get("title") or "Order line",
                    "sku": line.get("sku") or variant.get("sku") or "",
                    "quantity": float(line.get("quantity") or 0),
                    "price": float(price_money.get("amount") or 0),
                    "currency": price_money.get("currencyCode") or total_money.get("currencyCode") or "USD",
                }
            )
        return {
            "shopify_gid": node["id"],
            "shopify_id": str(node.get("legacyResourceId") or node["id"]),
            "order_number": node.get("name") or str(node.get("legacyResourceId") or node["id"]),
            "customer_name": customer_name or node.get("email") or "Shopify customer",
            "customer_email": customer.get("email") or node.get("email") or "",
            "street": address.get("address1") or "",
            "city": address.get("city") or "",
            "country": address.get("country") or "",
            "total_amount": float(total_money.get("amount") or 0),
            "currency": total_money.get("currencyCode") or "USD",
            "financial_status": node.get("displayFinancialStatus") or "UNKNOWN",
            "fulfillment_status": node.get("displayFulfillmentStatus") or "UNFULFILLED",
            "line_items": lines,
            "source_payload": node,
        }

    def _normalize_order_webhook_payload(self, payload):
        """Convert Shopify's REST-shaped orders/create webhook JSON into native order fields."""
        self.ensure_one()
        customer = payload.get("customer") or {}
        address = payload.get("shipping_address") or customer.get("default_address") or {}
        customer_name = " ".join(
            part for part in [customer.get("first_name"), customer.get("last_name")] if part
        ).strip()
        line_items = []
        for line in payload.get("line_items") or []:
            line_id = line.get("admin_graphql_api_id")
            if not line_id and line.get("id"):
                line_id = "gid://shopify/LineItem/%s" % line["id"]
            line_items.append(
                {
                    "shopify_line_gid": line_id,
                    "title": line.get("title") or line.get("name") or "Order line",
                    "sku": line.get("sku") or "",
                    "quantity": float(line.get("quantity") or 0),
                    "price": float(line.get("price") or 0),
                    "currency": payload.get("currency") or "USD",
                }
            )
        order_gid = payload.get("admin_graphql_api_id")
        if not order_gid and payload.get("id"):
            order_gid = "gid://shopify/Order/%s" % payload["id"]
        if not order_gid:
            raise UserError(_("Shopify order webhook did not include an order ID."))
        if not line_items:
            raise UserError(_("Shopify order webhook did not include line items."))
        financial_status = (payload.get("financial_status") or "UNKNOWN").replace("-", "_").upper()
        fulfillment_status = (payload.get("fulfillment_status") or "UNFULFILLED").replace("-", "_").upper()
        return {
            "shopify_gid": order_gid,
            "shopify_id": str(payload.get("id") or order_gid),
            "order_number": payload.get("name") or payload.get("order_number") or str(payload.get("id") or order_gid),
            "customer_name": customer_name or payload.get("email") or "Shopify customer",
            "customer_email": customer.get("email") or payload.get("email") or "",
            "street": address.get("address1") or "",
            "city": address.get("city") or "",
            "country": address.get("country") or "",
            "total_amount": float(payload.get("total_price") or 0),
            "currency": payload.get("currency") or "USD",
            "financial_status": financial_status,
            "fulfillment_status": fulfillment_status,
            "line_items": line_items,
            "source_payload": payload,
        }

    def _find_variant_by_sku(self, sku):
        """Find an existing Shopify variant by SKU so publishing stays idempotent."""
        self.ensure_one()
        data = self._graphql(
            """
            query VariantBySku($query: String!) {
              productVariants(first: 1, query: $query) {
                nodes {
                  id
                  sku
                  product { id }
                  inventoryItem { id }
                }
              }
            }
            """,
            {"query": "sku:%s" % sku},
        )
        nodes = data.get("productVariants", {}).get("nodes") or []
        return nodes[0] if nodes else None

    def _primary_location_gid(self):
        """Use the first Shopify location as the demo inventory target."""
        self.ensure_one()
        data = self._graphql("query DemoLocations { locations(first: 1) { nodes { id } } }")
        nodes = data.get("locations", {}).get("nodes") or []
        return nodes[0]["id"] if nodes else None

    def fulfill_shopify_order(self, order_gid):
        """Create Shopify fulfillments for all open fulfillment orders on a synced order."""
        self.ensure_one()
        data = self._graphql(
            """
            query OrderFulfillmentOrders($id: ID!) {
              order(id: $id) {
                id
                name
                fulfillmentOrders(first: 10) {
                  nodes {
                    id
                    status
                    requestStatus
                  }
                }
              }
            }
            """,
            {"id": order_gid},
        )
        order = data.get("order")
        if not order:
            raise UserError(_("Shopify order was not found."))
        fulfillment_orders = [
            item
            for item in (order.get("fulfillmentOrders") or {}).get("nodes", [])
            if item.get("status") not in {"CLOSED", "CANCELLED", "INCOMPLETE"}
        ]
        if not fulfillment_orders:
            return {"message": "No open Shopify fulfillment orders remain"}

        result = self._graphql(
            """
            mutation FulfillFromOdoo($fulfillment: FulfillmentInput!, $message: String) {
              fulfillmentCreate(fulfillment: $fulfillment, message: $message) {
                fulfillment { id status }
                userErrors { field message }
              }
            }
            """,
            {
                "message": "Fulfilled from native Odoo Shopify Sync Demo",
                "fulfillment": {
                    "notifyCustomer": False,
                    "lineItemsByFulfillmentOrder": [
                        {"fulfillmentOrderId": item["id"]} for item in fulfillment_orders
                    ],
                },
            },
        )["fulfillmentCreate"]
        if result.get("userErrors"):
            raise UserError(_("Shopify fulfillment errors: %s") % result["userErrors"])
        return result

    def _normalized_shop_domain(self):
        """Return the bare myshopify domain expected by Shopify Admin endpoints."""
        return (self.shop_domain or "").replace("https://", "").replace("http://", "").strip("/")

    def _log_event(self, action, status, message, payload=None):
        """Append one native event row for interviewer-visible auditability."""
        self.ensure_one()
        return self.env["shopify.sync.event"].create(
            {
                "instance_id": self.id,
                "action": action,
                "status": status,
                "message": message,
                "payload_excerpt": payload or {},
            }
        )

    def _window_action(self, name, model, domain):
        """Build a reusable Odoo window action for instance-scoped child records."""
        return {
            "type": "ir.actions.act_window",
            "name": name,
            "res_model": model,
            "view_mode": "list,form",
            "domain": domain,
            "context": {"default_instance_id": self.id},
        }

    def _display_notification(self, title, notification_type="info"):
        """Build a lightweight Odoo client notification response."""
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {"title": title, "type": notification_type, "sticky": False},
        }


class ShopifySyncProductMapping(models.Model):
    """Native mapping from one Odoo SKU to Shopify product, variant, and inventory IDs."""

    _name = "shopify.sync.product.mapping"
    _description = "Shopify Product Mapping"
    _rec_name = "sku"
    _instance_sku_unique = models.Constraint(
        "unique(instance_id, sku)",
        "A SKU can only be mapped once per Shopify instance.",
    )
    _instance_template_unique = models.Constraint(
        "unique(instance_id, product_template_id)",
        "An Odoo product can only be mapped once per Shopify instance.",
    )

    instance_id = fields.Many2one("shopify.sync.instance", required=True, ondelete="cascade")
    product_template_id = fields.Many2one("product.template", required=True, ondelete="cascade")
    product_variant_id = fields.Many2one("product.product", ondelete="cascade")
    sku = fields.Char(required=True, index=True)
    shopify_product_gid = fields.Char(readonly=True)
    shopify_variant_gid = fields.Char(readonly=True)
    shopify_inventory_item_gid = fields.Char(readonly=True)
    product_status = fields.Selection(
        [("not_published", "Not Published"), ("published", "Published"), ("failed", "Failed")],
        default="not_published",
        readonly=True,
    )
    inventory_status = fields.Selection(
        [("not_published", "Not Published"), ("published", "Published"), ("failed", "Failed")],
        default="not_published",
        readonly=True,
    )
    last_product_hash = fields.Char(readonly=True)
    last_inventory_hash = fields.Char(readonly=True)
    last_product_published_at = fields.Datetime(readonly=True)
    last_inventory_published_at = fields.Datetime(readonly=True)
    last_error = fields.Text(readonly=True)
    list_price = fields.Float(related="product_template_id.list_price", readonly=True)
    qty_available = fields.Float(related="product_variant_id.qty_available", readonly=True)
    available_to_sell = fields.Float(related="product_variant_id.free_qty", readonly=True)
    active = fields.Boolean(related="product_template_id.active", readonly=True)
    sale_ok = fields.Boolean(related="product_template_id.sale_ok", readonly=True)

    def action_publish_product(self):
        """Upsert this Odoo product into Shopify with productSet."""
        for mapping in self:
            payload = mapping._product_payload()
            try:
                existing = mapping.instance_id._find_variant_by_sku(mapping.sku)
                identifier = None
                if mapping.shopify_product_gid:
                    identifier = {"id": mapping.shopify_product_gid}
                elif existing:
                    identifier = {"id": existing["product"]["id"]}
                data = mapping.instance_id._graphql(
                    """
                    mutation OdooProductSet(
                      $productSet: ProductSetInput!,
                      $identifier: ProductSetIdentifiers,
                      $synchronous: Boolean!
                    ) {
                      productSet(input: $productSet, identifier: $identifier, synchronous: $synchronous) {
                        product {
                          id
                          title
                          variants(first: 10) {
                            nodes {
                              id
                              sku
                              inventoryItem { id }
                            }
                          }
                        }
                        userErrors { field message code }
                      }
                    }
                    """,
                    {"productSet": payload, "identifier": identifier, "synchronous": True},
                )
                result = data["productSet"]
                if result.get("userErrors"):
                    raise UserError(_("Shopify productSet errors: %s") % result["userErrors"])
                product = result.get("product") or {}
                variant = mapping._select_variant(product)
                mapping.write(
                    {
                        "shopify_product_gid": product.get("id"),
                        "shopify_variant_gid": variant.get("id"),
                        "shopify_inventory_item_gid": (variant.get("inventoryItem") or {}).get("id"),
                        "product_status": "published",
                        "last_product_hash": mapping._hash_payload(payload),
                        "last_product_published_at": fields.Datetime.now(),
                        "last_error": False,
                    }
                )
                mapping.instance_id._log_event("publish_product", "success", _("Published %s") % mapping.sku)
            except Exception as exc:
                mapping.write({"product_status": "failed", "last_error": str(exc)})
                mapping.instance_id._log_event("publish_product", "failed", "%s: %s" % (mapping.sku, exc))
        return self.instance_id._display_notification(_("Product publish finished"), "success")

    def action_publish_inventory(self, location_gid=None):
        """Set Shopify available quantity from Odoo stock available to sell.

        Shopify idempotency keys identify a write operation, not the desired
        inventory state. A fresh key lets the demo publish valid transitions such
        as 3 -> 2 -> 3 while `last_inventory_hash` still records the stable state
        hash for audit visibility.
        """
        for mapping in self:
            if not mapping.shopify_inventory_item_gid:
                raise UserError(_("Publish product %s before publishing inventory.") % mapping.sku)
            location = location_gid or mapping.instance_id._primary_location_gid()
            quantity = max(int(mapping.available_to_sell or 0), 0)
            payload = {
                "name": "available",
                "reason": "correction",
                "referenceDocumentUri": "odoo://product.template/%s/inventory" % mapping.product_template_id.id,
                "quantities": [
                    {
                        "inventoryItemId": mapping.shopify_inventory_item_gid,
                        "locationId": location,
                        "quantity": quantity,
                        "changeFromQuantity": None,
                    }
                ],
            }
            try:
                mapping._ensure_inventory_item_tracked()
                result = mapping.instance_id._graphql(
                    """
                    mutation InventorySet($input: InventorySetQuantitiesInput!, $idempotencyKey: String!) {
                      inventorySetQuantities(input: $input) @idempotent(key: $idempotencyKey) {
                        inventoryAdjustmentGroup { createdAt reason }
                        userErrors { field message code }
                      }
                    }
                    """,
                    {"input": payload, "idempotencyKey": "odoo-inventory-%s" % uuid.uuid4().hex},
                )["inventorySetQuantities"]
                if result.get("userErrors"):
                    raise UserError(_("Shopify inventory errors: %s") % result["userErrors"])
                mapping.write(
                    {
                        "inventory_status": "published",
                        "last_inventory_hash": mapping._hash_payload(payload),
                        "last_inventory_published_at": fields.Datetime.now(),
                        "last_error": False,
                    }
                )
                mapping.instance_id._log_event("publish_inventory", "success", _("Published stock for %s") % mapping.sku)
            except Exception as exc:
                mapping.write({"inventory_status": "failed", "last_error": str(exc)})
                mapping.instance_id._log_event("publish_inventory", "failed", "%s: %s" % (mapping.sku, exc))
        return self.instance_id._display_notification(_("Inventory publish finished"), "success")

    def _product_payload(self):
        """Convert one Odoo product mapping into Shopify's productSet input."""
        product = self.product_template_id
        return {
            "title": product.name,
            "status": "ACTIVE" if product.active and product.sale_ok else "DRAFT",
            "vendor": "Odoo",
            "productOptions": [{"name": "Title", "position": 1, "values": [{"name": "Default Title"}]}],
            "metafields": [
                {
                    "namespace": "odoo",
                    "key": "product_template_id",
                    "type": "single_line_text_field",
                    "value": str(product.id),
                },
                {"namespace": "odoo", "key": "sku", "type": "single_line_text_field", "value": self.sku},
            ],
            "variants": [
                {
                    "sku": self.sku,
                    "price": str(product.list_price or 0),
                    "inventoryItem": {"sku": self.sku, "tracked": True},
                    "inventoryPolicy": "DENY",
                    "optionValues": [{"optionName": "Title", "name": "Default Title"}],
                }
            ],
        }

    def _select_variant(self, product):
        """Select the Shopify variant matching this SKU from a productSet response."""
        variants = ((product.get("variants") or {}).get("nodes")) or []
        return next((item for item in variants if item.get("sku") == self.sku), variants[0] if variants else {})

    def _ensure_inventory_item_tracked(self):
        """Enable Shopify inventory tracking before publishing available quantities."""
        self.ensure_one()
        result = self.instance_id._graphql(
            """
            mutation InventoryItemUpdate($id: ID!, $input: InventoryItemInput!) {
              inventoryItemUpdate(id: $id, input: $input) {
                inventoryItem { id tracked }
                userErrors { field message }
              }
            }
            """,
            {"id": self.shopify_inventory_item_gid, "input": {"tracked": True}},
        )["inventoryItemUpdate"]
        if result.get("userErrors"):
            raise UserError(_("Shopify inventoryItemUpdate errors: %s") % result["userErrors"])
        return result.get("inventoryItem") or {}

    @staticmethod
    def _hash_payload(payload):
        """Create a stable hash for audit/drift fields from a JSON-like payload."""
        return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


class ShopifySyncOrder(models.Model):
    """Shopify order intake record that validates against Odoo before creating sale.order."""

    _name = "shopify.sync.order"
    _description = "Shopify Sync Order"
    _order = "create_date desc"
    _instance_shopify_order_unique = models.Constraint(
        "unique(instance_id, shopify_gid)",
        "A Shopify order can only be pulled once per instance.",
    )

    instance_id = fields.Many2one("shopify.sync.instance", required=True, ondelete="cascade")
    shopify_gid = fields.Char(required=True, index=True)
    shopify_id = fields.Char(index=True)
    order_number = fields.Char(required=True, index=True)
    customer_name = fields.Char(required=True)
    customer_email = fields.Char(index=True)
    street = fields.Char()
    city = fields.Char()
    country = fields.Char()
    total_amount = fields.Float()
    currency = fields.Char(default="USD")
    financial_status = fields.Char()
    fulfillment_status = fields.Char()
    status = fields.Selection(
        [
            ("new", "New"),
            ("validated", "Validated"),
            ("warning", "Validated With Warnings"),
            ("failed", "Failed"),
            ("published", "Published To Odoo"),
            ("confirmed", "Confirmed Sales Order"),
            ("paid", "Paid"),
            ("fulfilled", "Fulfilled"),
        ],
        default="new",
        readonly=True,
    )
    partner_id = fields.Many2one("res.partner", readonly=True)
    sale_order_id = fields.Many2one("sale.order", readonly=True)
    line_ids = fields.One2many("shopify.sync.order.line", "order_id", string="Lines")
    source_payload = fields.Json(readonly=True)
    shopify_refund_status = fields.Selection(
        [
            ("not_required", "Not Required"),
            ("submitted", "Submitted"),
            ("failed", "Failed"),
        ],
        default="not_required",
        readonly=True,
    )
    shopify_cancel_job_gid = fields.Char(readonly=True)
    shopify_refunded_at = fields.Datetime(readonly=True)
    validation_message = fields.Text(readonly=True)
    last_error = fields.Text(readonly=True)

    @api.model
    def create_or_update_from_shopify(self, instance, payload):
        """Idempotently upsert a Shopify order and replace its line snapshot."""
        order = self.search([("instance_id", "=", instance.id), ("shopify_gid", "=", payload["shopify_gid"])], limit=1)
        values = {
            "instance_id": instance.id,
            "shopify_gid": payload["shopify_gid"],
            "shopify_id": payload["shopify_id"],
            "order_number": payload["order_number"],
            "customer_name": payload["customer_name"],
            "customer_email": payload["customer_email"],
            "street": payload.get("street"),
            "city": payload.get("city"),
            "country": payload.get("country"),
            "total_amount": payload.get("total_amount"),
            "currency": payload.get("currency"),
            "financial_status": payload.get("financial_status"),
            "fulfillment_status": payload.get("fulfillment_status"),
            "source_payload": payload.get("source_payload"),
        }
        if order:
            order.write(values)
            order.line_ids.unlink()
        else:
            order = self.create(values)
        for line in payload.get("line_items") or []:
            self.env["shopify.sync.order.line"].create(
                {
                    "order_id": order.id,
                    "shopify_line_gid": line.get("shopify_line_gid"),
                    "title": line.get("title"),
                    "sku": line.get("sku"),
                    "quantity": line.get("quantity"),
                    "price": line.get("price"),
                    "currency": line.get("currency"),
                }
            )
        return order

    def action_validate(self):
        """Validate SKU, sellable state, stock, and price snapshot against Odoo."""
        precision = self.env["decimal.precision"].precision_get("Product Unit of Measure")
        for order in self:
            failures = []
            warnings = []
            for line in order.line_ids:
                line_failures, line_warnings = line.validate_against_odoo(precision)
                failures.extend(line_failures)
                warnings.extend(line_warnings)
            if failures:
                order.write({"status": "failed", "validation_message": "\n".join(failures), "last_error": "\n".join(failures)})
                order.instance_id._log_event("validate_order", "failed", "%s failed validation" % order.order_number, {"errors": failures})
                order.action_refund_failed_in_shopify()
            else:
                status = "warning" if warnings else "validated"
                order.write(
                    {
                        "status": status,
                        "shopify_refund_status": "not_required",
                        "validation_message": "\n".join(warnings) if warnings else _("Order passed validation."),
                        "last_error": False,
                    }
                )
                order.instance_id._log_event("validate_order", "success", "%s passed validation" % order.order_number, {"warnings": warnings})
        return self.instance_id._display_notification(_("Order validation finished"), "success")

    def action_refund_failed_in_shopify(self):
        """Cancel/refund Shopify orders that Odoo rejected during validation."""
        for order in self:
            if order.status != "failed" or order.shopify_refund_status == "submitted":
                continue
            try:
                staff_note = _("Odoo validation failed: %s") % (order.validation_message or order.last_error or "Unknown validation failure")
                result = order.instance_id._graphql(
                    """
                    mutation CancelRejectedShopifyOrder(
                      $orderId: ID!,
                      $notifyCustomer: Boolean,
                      $refundMethod: OrderCancelRefundMethodInput!,
                      $restock: Boolean!,
                      $reason: OrderCancelReason!,
                      $staffNote: String
                    ) {
                      orderCancel(
                        orderId: $orderId,
                        notifyCustomer: $notifyCustomer,
                        refundMethod: $refundMethod,
                        restock: $restock,
                        reason: $reason,
                        staffNote: $staffNote
                      ) {
                        job { id done }
                        orderCancelUserErrors { field message code }
                        userErrors { field message }
                      }
                    }
                    """,
                    {
                        "orderId": order.shopify_gid,
                        "notifyCustomer": True,
                        "refundMethod": {"originalPaymentMethodsRefund": True},
                        "restock": False,
                        "reason": "INVENTORY",
                        "staffNote": staff_note[:255],
                    },
                )["orderCancel"]
                errors = result.get("orderCancelUserErrors") or result.get("userErrors") or []
                if errors:
                    raise UserError(_("Shopify orderCancel errors: %s") % errors)
                job = result.get("job") or {}
                message = _("Shopify cancel/refund submitted for failed order %s.") % order.order_number
                order.write(
                    {
                        "shopify_refund_status": "submitted",
                        "shopify_cancel_job_gid": job.get("id"),
                        "shopify_refunded_at": fields.Datetime.now(),
                        "validation_message": "%s\n%s" % (order.validation_message or "", message),
                    }
                )
                order._publish_inventory_for_order_lines()
                order.instance_id._log_event(
                    "refund_failed_order",
                    "success",
                    "%s cancel/refund submitted in Shopify" % order.order_number,
                    {"job": job, "restock": False},
                )
            except Exception as exc:
                message = str(exc)
                order.write({"shopify_refund_status": "failed", "last_error": message})
                order.instance_id._log_event("refund_failed_order", "failed", "%s refund failed: %s" % (order.order_number, message))
        return self.instance_id._display_notification(_("Failed-order refund processing finished"), "success")

    def action_create_sale_order(self):
        """Create a draft Odoo sale order after successful validation."""
        for order in self:
            if order.sale_order_id:
                continue
            if order.status not in {"validated", "warning"}:
                raise UserError(_("Validate %s before creating the Odoo sale order.") % order.order_number)
            partner = order._ensure_partner()
            order_lines = []
            for line in order.line_ids:
                if not line.product_id:
                    raise UserError(_("Line %s does not have a validated Odoo product.") % line.title)
                order_lines.append(
                    (
                        0,
                        0,
                        {
                            "product_id": line.product_id.id,
                            "name": line.title or line.product_id.display_name,
                            "product_uom_qty": line.quantity,
                            "price_unit": line.price,
                        },
                    )
                )
            sale_order = self.env["sale.order"].create(
                {
                    "partner_id": partner.id,
                    "client_order_ref": order.order_number,
                    "origin": "Shopify",
                    "user_id": order.instance_id._ensure_shopify_salesperson().id,
                    "order_line": order_lines,
                }
            )
            order.write({"partner_id": partner.id, "sale_order_id": sale_order.id, "status": "published"})
            order.instance_id._log_event("create_sale_order", "success", "%s became %s" % (order.order_number, sale_order.name))
        return self.instance_id._display_notification(_("Draft sale order created"), "success")

    def action_confirm_quotation(self):
        """Confirm the linked Odoo quotation as a sales order."""
        confirmed_count = sum(order._confirm_linked_sale_order() for order in self)
        return self.instance_id._display_notification(_("Confirmed %s quotation(s)") % confirmed_count, "success")

    def _confirm_linked_sale_order(self):
        """Confirm one linked sale.order and mark the native Shopify order accordingly."""
        self.ensure_one()
        if not self.sale_order_id:
            return 0
        sale_order = self.sale_order_id
        if sale_order.state in {"draft", "sent"}:
            sale_order.write({"user_id": self.instance_id._ensure_shopify_salesperson().id})
            sale_order.action_confirm()
            status = "confirmed"
            message = _("Confirmed as Odoo sales order %s.") % sale_order.name
            if self._shopify_reports_paid():
                payment_result = sale_order.action_shopify_mark_paid()[0]
                status = "paid" if payment_result.get("payment_state") == "paid" else "confirmed"
                message = _("Confirmed and registered Shopify payment for %s.") % sale_order.name
                self.instance_id._log_event("mark_paid", "success", "%s payment state: %s" % (self.order_number, payment_result.get("payment_state")))
            self.write({"status": status, "last_error": False, "validation_message": message})
            self.instance_id._log_event("confirm_quotation", "success", "%s confirmed as %s" % (self.order_number, sale_order.name))
            return 1
        if sale_order.state in {"sale", "done"}:
            if self._shopify_reports_paid():
                payment_result = sale_order.action_shopify_mark_paid()[0]
                status = "paid" if payment_result.get("payment_state") == "paid" else "confirmed"
                self.write(
                    {
                        "status": status,
                        "last_error": False,
                        "validation_message": _("Shopify payment state in Odoo: %s.") % payment_result.get("payment_state"),
                    }
                )
                return 1 if status == "paid" else 0
            if self.status == "published":
                self.write({"status": "confirmed", "last_error": False})
        return 0

    def _shopify_reports_paid(self):
        """Return whether Shopify says the order has been fully paid."""
        self.ensure_one()
        return (self.financial_status or "").upper() in PAID_SHOPIFY_FINANCIAL_STATUSES

    def _publish_inventory_for_order_lines(self):
        """Republish Shopify inventory for SKUs whose Odoo free quantity changed."""
        self.ensure_one()
        location_gid = None
        for line in self.line_ids.filtered("product_id"):
            mapping = self.env["shopify.sync.product.mapping"].search(
                [
                    ("instance_id", "=", self.instance_id.id),
                    ("product_variant_id", "=", line.product_id.id),
                    ("shopify_inventory_item_gid", "!=", False),
                ],
                limit=1,
            )
            if not mapping:
                continue
            if not location_gid:
                location_gid = self.instance_id._primary_location_gid()
            mapping.action_publish_inventory(location_gid)

    @api.model
    def _cron_confirm_new_shopify_quotations(self):
        """Scheduled entrypoint that turns validated Shopify quotations into sales orders."""
        confirmed_count = 0
        instances = self.env["shopify.sync.instance"].search(
            [("active", "=", True), ("auto_confirm_quotations", "=", True)]
        )
        for instance in instances:
            confirmed_count += instance._confirm_new_quotations()
        return confirmed_count

    def action_mark_fulfilled(self):
        """Confirm the Odoo sale order, validate deliveries, then fulfill the Shopify order."""
        for order in self:
            if not order.sale_order_id:
                raise UserError(_("Create the Odoo sale order before fulfillment."))
            if order.status not in {"published", "confirmed", "paid"}:
                raise UserError(_("Validate and publish %s before fulfillment can change inventory.") % order.order_number)
            sale_order = order.sale_order_id
            if sale_order.state in {"draft", "sent"}:
                sale_order.action_confirm()
            warnings = []
            for picking in sale_order.picking_ids.filtered(lambda item: item.state not in {"done", "cancel"}):
                try:
                    picking.button_validate()
                except Exception as exc:
                    warnings.append("%s: %s" % (picking.name, exc))
            try:
                order.instance_id.fulfill_shopify_order(order.shopify_gid)
            except Exception as exc:
                warnings.append(_("Shopify fulfillment warning: %s") % exc)
            order.write(
                {
                    "status": "fulfilled",
                    "fulfillment_status": "FULFILLED",
                    "validation_message": "\n".join(warnings) if warnings else _("Fulfilled in Odoo and Shopify."),
                }
            )
            order.instance_id._log_event("mark_fulfilled", "success", "%s marked fulfilled" % order.order_number, {"warnings": warnings})
        return self.instance_id._display_notification(_("Order marked fulfilled"), "success")

    def action_open_sale_order(self):
        """Open the linked Odoo sale order from the native sync order form."""
        self.ensure_one()
        if not self.sale_order_id:
            raise UserError(_("No Odoo sale order has been created for this Shopify order."))
        return {
            "type": "ir.actions.act_window",
            "name": self.sale_order_id.display_name,
            "res_model": "sale.order",
            "res_id": self.sale_order_id.id,
            "view_mode": "form",
            "target": "current",
        }

    def _ensure_partner(self):
        """Find or create the Odoo customer used by the generated sale order."""
        self.ensure_one()
        domain = [("email", "=", self.customer_email)] if self.customer_email else [("name", "=", self.customer_name)]
        partner = self.env["res.partner"].search(domain, limit=1)
        if partner:
            return partner
        return self.env["res.partner"].create(
            {
                "name": self.customer_name,
                "email": self.customer_email or False,
                "street": self.street or False,
                "city": self.city or False,
                "customer_rank": 1,
            }
        )


class ShopifySyncOrderLine(models.Model):
    """Validated Shopify order line with the resolved Odoo product."""

    _name = "shopify.sync.order.line"
    _description = "Shopify Sync Order Line"

    order_id = fields.Many2one("shopify.sync.order", required=True, ondelete="cascade")
    shopify_line_gid = fields.Char()
    title = fields.Char()
    sku = fields.Char(index=True)
    quantity = fields.Float(default=1)
    price = fields.Float()
    currency = fields.Char(default="USD")
    product_id = fields.Many2one("product.product", readonly=True)
    validation_status = fields.Selection(
        [("new", "New"), ("valid", "Valid"), ("warning", "Warning"), ("failed", "Failed")],
        default="new",
        readonly=True,
    )
    validation_message = fields.Text(readonly=True)

    def validate_against_odoo(self, precision):
        """Resolve SKU against Odoo and return blocking failures plus non-blocking warnings."""
        self.ensure_one()
        failures = []
        warnings = []
        sku = (self.sku or "").strip()
        if not sku:
            failures.append(_("%s is missing a SKU.") % (self.title or "Order line"))
            self.write({"validation_status": "failed", "validation_message": failures[-1]})
            return failures, warnings

        product = self.env["product.product"].search([("default_code", "=", sku), ("active", "=", True)], limit=1)
        if not product:
            failures.append(_("SKU %s does not exist as an active Odoo product.") % sku)
            self.write({"validation_status": "failed", "validation_message": failures[-1]})
            return failures, warnings
        if not product.product_tmpl_id.sale_ok:
            failures.append(_("SKU %s is not sellable in Odoo.") % sku)

        compare_stock = float_compare(product.free_qty, self.quantity, precision_digits=precision)
        if compare_stock < 0:
            failures.append(_("SKU %s has %.2f available to sell but Shopify requested %.2f.") % (sku, product.free_qty, self.quantity))

        compare_price = float_compare(product.lst_price or 0.0, self.price or 0.0, precision_digits=2)
        if compare_price != 0:
            warnings.append(_("SKU %s price differs: Odoo %.2f vs Shopify %.2f.") % (sku, product.lst_price, self.price))

        status = "failed" if failures else "warning" if warnings else "valid"
        self.write(
            {
                "product_id": product.id,
                "validation_status": status,
                "validation_message": "\n".join(failures or warnings or [_("Line passed validation.")]),
            }
        )
        return failures, warnings


class SaleOrder(models.Model):
    """Small Shopify-specific extension on top of Odoo's standard sale.order."""

    _inherit = "sale.order"

    def _validated_shopify_sync_order(self):
        """Return the validated sync order that authorizes Shopify stock changes."""
        self.ensure_one()
        sync_order = self.env["shopify.sync.order"].search([("sale_order_id", "=", self.id)], limit=1)
        if not sync_order or sync_order.status not in {"validated", "warning", "published", "confirmed", "paid", "fulfilled"}:
            raise UserError(
                _("Shopify order %s must pass the Shopify Sync validation flow before Odoo inventory can change.")
                % (self.client_order_ref or self.name)
            )
        return sync_order

    def action_shopify_mark_paid(self):
        """Confirm, invoice, post, and register payment for Shopify-paid orders."""
        results = []
        for sale_order in self:
            if sale_order.origin != "Shopify":
                raise UserError(_("Only Shopify-origin sales orders can be marked paid by this automation."))
            sale_order._validated_shopify_sync_order()
            if sale_order.state in {"draft", "sent"}:
                sale_order.action_confirm()

            invoices = sale_order.invoice_ids.filtered(lambda move: move.move_type == "out_invoice" and move.state != "cancel")
            if not invoices:
                invoices = sale_order._create_invoices(final=True)
            if not invoices:
                raise UserError(_("No customer invoice could be created for %s.") % sale_order.name)

            for invoice in invoices.filtered(lambda move: move.state == "draft"):
                invoice.action_post()

            payable_invoices = invoices.filtered(
                lambda move: move.state == "posted"
                and move.payment_state not in {"paid", "reversed"}
                and move.amount_residual > 0
            )
            if payable_invoices:
                journal = self.env["account.journal"].search([("type", "in", ["bank", "cash"])], limit=1)
                if not journal:
                    raise UserError(_("No bank or cash journal is available to register the Shopify payment."))
                wizard = self.env["account.payment.register"].with_context(
                    active_model="account.move",
                    active_ids=payable_invoices.ids,
                ).create(
                    {
                        "journal_id": journal.id,
                        "communication": _("Shopify payment for %s") % (sale_order.client_order_ref or sale_order.name),
                    }
                )
                wizard.action_create_payments()

            sale_order._shopify_publish_reserved_inventory()
            invoices.invalidate_recordset()
            payment_state = "paid" if all(invoice.payment_state == "paid" for invoice in invoices) else ",".join(sorted(set(invoices.mapped("payment_state"))))
            results.append(
                {
                    "sale_order_id": sale_order.id,
                    "sale_order_name": sale_order.name,
                    "invoice_ids": invoices.ids,
                    "payment_state": payment_state,
                    "amount_residual": sum(invoices.mapped("amount_residual")),
                }
            )
        return results

    def _shopify_publish_reserved_inventory(self):
        """Push updated Odoo free quantities to Shopify after a paid order reserves stock."""
        for sale_order in self:
            sync_order = sale_order._validated_shopify_sync_order()
            sync_order._publish_inventory_for_order_lines()


class ShopifySyncEvent(models.Model):
    """Native append-only audit log for catalog publishing and order operations."""

    _name = "shopify.sync.event"
    _description = "Shopify Sync Event"
    _order = "create_date desc"

    instance_id = fields.Many2one("shopify.sync.instance", required=True, ondelete="cascade")
    action = fields.Char(required=True)
    status = fields.Selection([("success", "Success"), ("failed", "Failed"), ("warning", "Warning")], required=True)
    message = fields.Text(required=True)
    payload_excerpt = fields.Json()
