"""Public Shopify webhook endpoints for the native Odoo sync app.

Shopify webhook verification must be performed against the raw HTTP request
body. The controller keeps that responsibility at the edge and hands verified
payloads to the same Odoo model workflow used by manual order pulls.

SpecOps evidence: REQ-ORDER-001.
"""

import base64
import hashlib
import hmac
import json
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class ShopifyWebhookController(http.Controller):
    """Receive Shopify webhooks and dispatch them into native Odoo models."""

    @http.route(
        "/shopify_sync_demo/webhooks/orders_create",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
    )
    def orders_create(self, **_kwargs):
        """Verify and process Shopify's orders/create webhook."""
        raw_body = request.httprequest.get_data() or b""
        headers = self._shopify_headers()
        instance = self._find_instance(headers.get("X-Shopify-Shop-Domain"))
        if not instance:
            _logger.warning("Rejected Shopify webhook because no active instance matched shop %s", headers.get("X-Shopify-Shop-Domain"))
            return request.make_response("Unknown shop", status=404)
        if headers.get("X-Shopify-Topic") not in {None, "orders/create"}:
            instance._log_event("orders_create_webhook", "failed", "Rejected unexpected Shopify topic %s" % headers.get("X-Shopify-Topic"))
            return request.make_response("Unexpected topic", status=400)
        if not self._valid_hmac(instance, raw_body, headers.get("X-Shopify-Hmac-Sha256")):
            instance._log_event("orders_create_webhook", "failed", "Rejected webhook with invalid HMAC signature")
            return request.make_response("Invalid signature", status=401)
        try:
            payload = json.loads(raw_body.decode("utf-8"))
            instance.process_order_create_webhook(payload, headers=headers)
        except Exception as exc:
            _logger.exception("Shopify orders/create webhook failed")
            instance._log_event("orders_create_webhook", "failed", str(exc))
            return request.make_response("Webhook failed", status=500)
        return request.make_response("OK", status=200)

    def _shopify_headers(self):
        """Extract Shopify headers with canonical names for logging and verification."""
        incoming = request.httprequest.headers
        return {
            "X-Shopify-Hmac-Sha256": incoming.get("X-Shopify-Hmac-Sha256"),
            "X-Shopify-Shop-Domain": incoming.get("X-Shopify-Shop-Domain"),
            "X-Shopify-Topic": incoming.get("X-Shopify-Topic"),
            "X-Shopify-Webhook-Id": incoming.get("X-Shopify-Webhook-Id"),
        }

    def _find_instance(self, shop_domain):
        """Resolve the active Odoo sync instance for the webhook's Shopify shop."""
        normalized_shop = self._normalize_shop_domain(shop_domain)
        instances = request.env["shopify.sync.instance"].sudo().search([("active", "=", True)])
        for instance in instances:
            if instance._normalized_shop_domain() == normalized_shop:
                return instance
        return instances[:1]

    def _valid_hmac(self, instance, raw_body, provided_hmac):
        """Compare Shopify's base64 HMAC signature against the raw body digest."""
        if not provided_hmac:
            return False
        try:
            secret = instance._webhook_signing_secret()
        except Exception as exc:
            _logger.warning("Cannot verify Shopify webhook for %s: %s", instance.display_name, exc)
            return False
        digest = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).digest()
        expected = base64.b64encode(digest).decode("utf-8")
        return hmac.compare_digest(expected, provided_hmac)

    @staticmethod
    def _normalize_shop_domain(shop_domain):
        """Return Shopify shop domains in the same bare form stored on the instance."""
        return (shop_domain or "").replace("https://", "").replace("http://", "").strip("/")
