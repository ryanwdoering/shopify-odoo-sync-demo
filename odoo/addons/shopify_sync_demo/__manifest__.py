{
    "name": "Shopify Sync Demo",
    "summary": "Native Odoo operator console for Shopify catalog publishing and order validation.",
    "version": "19.0.1.0.0",
    "category": "Sales",
    "author": "Ryan Doering",
    "license": "LGPL-3",
    "depends": ["base", "product", "sale", "stock", "account"],
    "data": [
        "security/ir.model.access.csv",
        "data/ir_cron.xml",
        "views/shopify_sync_views.xml",
    ],
    "post_init_hook": "post_init_hook",
    "application": True,
    "installable": True,
}
