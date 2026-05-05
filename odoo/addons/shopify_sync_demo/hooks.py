"""Install hooks for the Shopify Sync demo module."""

import os


def post_init_hook(env):
    """Create a default instance from Docker/.env values when the demo module is installed."""
    if env["shopify.sync.instance"].search_count([]):
        return

    shop_domain = os.environ.get("SHOPIFY_SHOP_DOMAIN")
    if not shop_domain:
        return

    instance = env["shopify.sync.instance"].sudo().create(
        {
            "name": "Odoo Sync Demo",
            "shop_domain": shop_domain,
            "api_version": os.environ.get("SHOPIFY_ADMIN_API_VERSION") or "2026-04",
            "access_token": os.environ.get("SHOPIFY_ACCESS_TOKEN") or "",
            "client_id": os.environ.get("SHOPIFY_CLIENT_ID") or "",
            "client_secret": os.environ.get("SHOPIFY_CLIENT_SECRET") or "",
            "webhook_secret": os.environ.get("SHOPIFY_WEBHOOK_SECRET") or "",
            "webhook_callback_url": os.environ.get("SHOPIFY_WEBHOOK_CALLBACK_URL") or "",
        }
    )
    instance.action_seed_demo_data()
