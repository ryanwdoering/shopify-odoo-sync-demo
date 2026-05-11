"""Fast repository-level tests for the Odoo-only Shopify Sync demo.

These tests intentionally avoid importing Odoo. They validate the module's
static contract: manifest dependencies, XML wiring, access rules, documentation,
and the source-level safeguards that make the demo credible in code review.

SpecOps evidence: REQ-ODOO-001, REQ-CATALOG-001, REQ-ORDER-001,
REQ-REFUND-001, REQ-AUTO-001, REQ-MAINT-001, REQ-SHOPIFYAPI-001,
REQ-SHOPIFYAPI-002, REQ-SHOPIFYAPI-003, REQ-SHOPIFYAPI-004,
REQ-DATA-001, REQ-DATA-002, REQ-DATA-003, REQ-DATA-004.
"""

import ast
import csv
import tomllib
from pathlib import Path
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "odoo" / "addons" / "shopify_sync_demo"


def _manifest() -> dict:
    """Read the Odoo manifest dict without importing the module."""
    tree = ast.parse((MODULE / "__manifest__.py").read_text())
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Dict):
            return ast.literal_eval(node.value)
    raise AssertionError("No manifest dictionary found")


def _xml(path: Path) -> ElementTree.Element:
    """Parse one Odoo XML data/view file."""
    return ElementTree.parse(path).getroot()


def test_repo_is_odoo_only() -> None:
    """The old standalone dashboard/service paths should not come back by accident."""
    removed_paths = [
        ROOT / "backend",
        ROOT / "frontend",
        ROOT / "data",
        ROOT / "shopify.app.toml",
    ]
    assert all(not path.exists() for path in removed_paths)

    compose = (ROOT / "docker-compose.yml").read_text()
    assert "backend:" not in compose
    assert "frontend:" not in compose
    assert "sqlite" not in compose.lower()
    assert "5173" not in compose
    assert "8000" not in compose


def test_manifest_declares_business_app_dependencies() -> None:
    """The module must install everything needed for catalog, stock, sales, and paid orders."""
    manifest = _manifest()
    assert manifest["application"] is True
    assert manifest["installable"] is True
    assert manifest["category"] == "Sales"
    assert {"base", "product", "sale", "stock", "account"}.issubset(set(manifest["depends"]))
    for relative_path in manifest["data"]:
        assert (MODULE / relative_path).exists(), relative_path


def test_odoo_xml_files_are_well_formed_and_wire_expected_actions() -> None:
    """Views and cron XML should parse and expose the demo's main operator actions."""
    views = _xml(MODULE / "views" / "shopify_sync_views.xml")
    cron = _xml(MODULE / "data" / "ir_cron.xml")

    button_names = {node.attrib.get("name") for node in views.iter("button")}
    assert {
        "action_test_connection",
        "action_refresh_catalog",
        "action_publish_products",
        "action_publish_inventory",
        "action_publish_all",
        "action_pull_orders",
        "action_register_orders_create_webhook",
        "action_confirm_new_quotations",
        "action_refund_failed_in_shopify",
        "action_mark_fulfilled",
        "action_reset_and_seed_demo_data",
    }.issubset(button_names)

    menu_names = {node.attrib.get("name") for node in views.iter("menuitem")}
    assert {"Shopify Sync", "Dashboard", "Catalog", "Orders", "Events"}.issubset(menu_names)

    cron_fields = {field.attrib.get("name"): (field.text or "") for field in cron.iter("field")}
    assert cron_fields["name"] == "Shopify Sync: Confirm validated quotations"
    assert cron_fields["code"] == "model._cron_confirm_new_shopify_quotations()"
    assert cron_fields["interval_number"] == "1"
    assert cron_fields["interval_type"] == "minutes"


def test_access_rules_cover_every_native_model() -> None:
    """Every custom model should have user and system access rules."""
    rows = list(csv.DictReader((MODULE / "security" / "ir.model.access.csv").open()))
    model_to_groups: dict[str, set[str]] = {}
    for row in rows:
        model_to_groups.setdefault(row["model_id:id"], set()).add(row["group_id:id"])

    expected_models = {
        "model_shopify_sync_instance",
        "model_shopify_sync_product_mapping",
        "model_shopify_sync_order",
        "model_shopify_sync_order_line",
        "model_shopify_sync_event",
    }
    assert expected_models == set(model_to_groups)
    for groups in model_to_groups.values():
        assert {"base.group_user", "base.group_system"}.issubset(groups)


def test_python_sources_have_docstrings() -> None:
    """Keep business automation logic explainable to the next maintainer."""
    missing: list[str] = []
    for path in MODULE.rglob("*.py"):
        tree = ast.parse(path.read_text())
        if path.name != "__manifest__.py" and not ast.get_docstring(tree):
            missing.append(f"{path.relative_to(ROOT)}:<module>")
        for node in ast.walk(tree):
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and not ast.get_docstring(node):
                missing.append(f"{path.relative_to(ROOT)}:{node.lineno}:{node.name}")
    assert missing == []


def test_inventory_publish_uses_available_to_sell_and_fresh_operation_keys() -> None:
    """Shopify inventory must reflect Odoo free quantity and not stale idempotent writes."""
    source = (MODULE / "models" / "shopify_sync.py").read_text()
    assert 'available_to_sell = fields.Float(related="product_variant_id.free_qty"' in source
    assert "quantity = max(int(mapping.available_to_sell or 0), 0)" in source
    assert "uuid.uuid4().hex" in source
    assert '"idempotencyKey": mapping._hash_payload(payload)' not in source


def test_product_publish_blocks_shopify_overselling() -> None:
    """Published variants must use Shopify's native sold-out enforcement controls."""
    source = (MODULE / "models" / "shopify_sync.py").read_text()
    assert '"inventoryPolicy": "DENY"' in source
    assert '"inventoryItem": {"sku": self.sku, "tracked": True}' in source
    assert "inventoryItemUpdate" in source
    assert '"input": {"tracked": True}' in source


def test_order_webhook_endpoint_verifies_hmac_and_reuses_ingestion() -> None:
    """The event-driven path should verify Shopify signatures before Odoo writes."""
    controller = (MODULE / "controllers" / "shopify_webhooks.py").read_text()
    model_source = (MODULE / "models" / "shopify_sync.py").read_text()
    assert "/shopify_sync_demo/webhooks/orders_create" in controller
    assert 'auth="public"' in controller
    assert "csrf=False" in controller
    assert "request.httprequest.get_data()" in controller
    assert "X-Shopify-Hmac-Sha256" in controller
    assert "hmac.compare_digest" in controller
    assert "process_order_create_webhook" in model_source
    assert "_ingest_normalized_order" in model_source


def test_failed_orders_are_canceled_and_refunded_in_shopify() -> None:
    """Rejected orders should be immediately reversed through Shopify orderCancel."""
    source = (MODULE / "models" / "shopify_sync.py").read_text()
    views = (MODULE / "views" / "shopify_sync_views.xml").read_text()
    assert "action_refund_failed_in_shopify" in source
    assert "orderCancel(" in source
    assert '"refundMethod": {"originalPaymentMethodsRefund": True}' in source
    assert '"restock": False' in source
    assert '"reason": "INVENTORY"' in source
    assert "shopify_refund_status" in views


def test_docs_include_job_relevant_talk_track() -> None:
    """The guide should explicitly frame the demo for business systems automation roles."""
    guide = (ROOT / "Guide.md").read_text()
    assert "Business Systems & Automation Engineer" in guide
    assert "Odoo customization" in guide
    assert "Shopify/e-commerce automation" in guide
    assert "operational support" in guide.lower()


def test_docs_include_maintained_mermaid_diagrams() -> None:
    """REQ-MAINT-001: architecture diagrams should stay linked and reviewable."""
    readme = (ROOT / "README.md").read_text()
    diagrams = (ROOT / "docs" / "diagrams.md").read_text()

    assert "[Architecture and workflow diagrams](docs/diagrams.md)" in readme
    assert "docs/diagrams.md" in readme
    assert diagrams.count("```mermaid") >= 5
    assert "REQ-ODOO-001" in diagrams
    assert "REQ-CATALOG-001" in diagrams
    assert "REQ-ORDER-001" in diagrams
    assert "REQ-REFUND-001" in diagrams
    assert "REQ-AUTO-001" in diagrams
    assert "REQ-FULFILL-001" in diagrams
    assert "REQ-MAINT-001" in diagrams


def test_specops_contract_tracks_the_odoo_module() -> None:
    """REQ-MAINT-001: SpecOps should audit the real Odoo module and active requirements."""
    config = tomllib.loads((ROOT / "specops.toml").read_text())
    assert "docs/**/*.md" in config["paths"]["code"]
    assert "odoo/addons/**/*.py" in config["paths"]["code"]
    assert "odoo/addons/**/*.xml" in config["paths"]["code"]
    assert "odoo/addons/**/tests/**/*.py" in config["paths"]["tests"]
    assert "shopify_sync_demo" in config["components"]
    assert "repo_operations" in config["components"]

    spec = (ROOT / "specs" / "specops.md").read_text()
    assert "Replace this starter requirement" not in spec
    assert spec.count("status: active") >= 7
    assert "REQ-MAINT-001" in spec


def test_specops_api_and_data_model_contracts_are_defined() -> None:
    """SpecOps should include Shopify API and native Odoo data-model contracts."""
    api_spec = (ROOT / "specs" / "shopify-api-contracts.md").read_text()
    data_spec = (ROOT / "specs" / "data-model.md").read_text()
    model_source = (MODULE / "models" / "shopify_sync.py").read_text()

    for requirement_id in [
        "REQ-SHOPIFYAPI-001",
        "REQ-SHOPIFYAPI-002",
        "REQ-SHOPIFYAPI-003",
        "REQ-SHOPIFYAPI-004",
    ]:
        assert requirement_id in api_spec

    for requirement_id in [
        "REQ-DATA-001",
        "REQ-DATA-002",
        "REQ-DATA-003",
        "REQ-DATA-004",
    ]:
        assert requirement_id in data_spec

    assert "productSet" in api_spec
    assert "inventoryItemUpdate" in api_spec
    assert "inventorySetQuantities" in api_spec
    assert "orderCancel" in api_spec
    assert "fulfillmentCreate" in model_source
    assert "not_published" in data_spec
    assert "validated" in data_spec
    assert "fulfilled" in data_spec
    assert "success" in data_spec
