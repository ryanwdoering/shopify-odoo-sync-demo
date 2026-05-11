# Architecture And Workflow Diagrams

These Mermaid diagrams capture the maintained shape of the Odoo-led Shopify Sync
demo. They are written as Markdown so code review can catch changes alongside
SpecOps requirements.

SpecOps evidence: REQ-ODOO-001, REQ-CATALOG-001, REQ-ORDER-001,
REQ-REFUND-001, REQ-AUTO-001, REQ-FULFILL-001, REQ-MAINT-001.

## System Context

```mermaid
flowchart LR
    Operator["Odoo operator"]
    OdooApp["Odoo 19 Shopify Sync app"]
    OdooModels["Native Odoo models\nproducts, stock, sale orders, invoices, events"]
    Cron["Scheduled action\nconfirm validated quotations"]
    Webhook["Public Odoo webhook controller\n/orders_create"]
    ShopifyAdmin["Shopify Admin GraphQL"]
    ShopifyStore["Shopify dev store"]

    Operator -->|"uses native menus and buttons"| OdooApp
    OdooApp -->|"reads and writes"| OdooModels
    Cron -->|"processes validated sync orders"| OdooModels
    ShopifyStore -->|"signed orders/create webhook"| Webhook
    Webhook -->|"verified payload"| OdooApp
    OdooApp -->|"productSet, inventorySetQuantities,\norderCancel, fulfillmentCreate"| ShopifyAdmin
    ShopifyAdmin -->|"mutates commerce edge"| ShopifyStore
```

## Catalog And Inventory Publish

```mermaid
sequenceDiagram
    actor Operator
    participant Odoo as Odoo Shopify Sync
    participant Product as Odoo Product And Stock
    participant Shopify as Shopify Admin GraphQL

    Operator->>Odoo: Refresh Inventory Catalog
    Odoo->>Product: Find active stockable SKU products
    Product-->>Odoo: Product variants with free_qty
    Odoo->>Odoo: Upsert product mapping rows

    Operator->>Odoo: Publish Products
    Odoo->>Shopify: productSet with SKU, price, tracked inventory, DENY oversell
    Shopify-->>Odoo: Product, variant, inventory item GIDs
    Odoo->>Odoo: Store Shopify IDs and product publish audit event

    Operator->>Odoo: Publish Inventory
    Odoo->>Product: Read product_variant.free_qty
    Odoo->>Shopify: inventoryItemUpdate tracked=true
    Odoo->>Shopify: inventorySetQuantities with fresh idempotency key
    Shopify-->>Odoo: Inventory adjustment result
    Odoo->>Odoo: Store inventory hash and audit event
```

## Order Intake And Validation State

```mermaid
stateDiagram-v2
    [*] --> New: webhook or manual pull
    New --> Validating: normalize Shopify order
    Validating --> Failed: missing SKU, inactive SKU,\nnon-sellable product, or insufficient free_qty
    Validating --> Warning: price drift only
    Validating --> Validated: all lines pass

    Failed --> RefundSubmitted: orderCancel refund original payment\nrestock false
    RefundSubmitted --> [*]

    Warning --> QuotationCreated: create Odoo quotation
    Validated --> QuotationCreated: create Odoo quotation
    QuotationCreated --> Confirmed: cron or button confirms sale
    Confirmed --> Paid: Shopify financial status PAID\nposts invoice and registers payment
    Confirmed --> Fulfilled: Mark Fulfilled
    Paid --> Fulfilled: Mark Fulfilled
    Fulfilled --> [*]
```

## Data Model

```mermaid
erDiagram
    SHOPIFY_SYNC_INSTANCE ||--o{ SHOPIFY_SYNC_PRODUCT_MAPPING : owns
    SHOPIFY_SYNC_INSTANCE ||--o{ SHOPIFY_SYNC_ORDER : ingests
    SHOPIFY_SYNC_INSTANCE ||--o{ SHOPIFY_SYNC_EVENT : records
    SHOPIFY_SYNC_ORDER ||--o{ SHOPIFY_SYNC_ORDER_LINE : contains
    PRODUCT_TEMPLATE ||--o{ PRODUCT_PRODUCT : has
    PRODUCT_PRODUCT ||--o{ SHOPIFY_SYNC_PRODUCT_MAPPING : maps
    PRODUCT_PRODUCT ||--o{ SHOPIFY_SYNC_ORDER_LINE : validates
    RES_PARTNER ||--o{ SHOPIFY_SYNC_ORDER : customer
    SALE_ORDER ||--o| SHOPIFY_SYNC_ORDER : linked
    SALE_ORDER ||--o{ ACCOUNT_MOVE : invoices
    SALE_ORDER ||--o{ STOCK_PICKING : delivers

    SHOPIFY_SYNC_INSTANCE {
        char shop_domain
        char api_version
        char orders_create_webhook_gid
        boolean auto_confirm_quotations
    }

    SHOPIFY_SYNC_PRODUCT_MAPPING {
        char sku
        char shopify_product_gid
        char shopify_variant_gid
        char shopify_inventory_item_gid
        float available_to_sell
    }

    SHOPIFY_SYNC_ORDER {
        char shopify_gid
        char order_number
        selection status
        selection shopify_refund_status
        char shopify_cancel_job_gid
    }

    SHOPIFY_SYNC_ORDER_LINE {
        char sku
        float quantity
        float price
        selection validation_status
    }

    SHOPIFY_SYNC_EVENT {
        char action
        char status
        text message
    }
```

## Failure Handling

```mermaid
flowchart TD
    Intake["Shopify order intake"]
    Validate["Validate every order line against Odoo"]
    MissingSku["Missing SKU"]
    Inactive["No active Odoo product"]
    NotSellable["Product not sellable"]
    NoStock["Requested quantity exceeds free_qty"]
    PriceDrift["Price differs from Odoo"]
    Failed["Failed sync order"]
    Cancel["Submit Shopify orderCancel"]
    NoRestock["restock=false\nOdoo remains inventory truth"]
    Job["Store async cancel job ID"]
    Republish["Republish Odoo inventory"]
    Accepted["Validated or warning order"]

    Intake --> Validate
    Validate --> MissingSku --> Failed
    Validate --> Inactive --> Failed
    Validate --> NotSellable --> Failed
    Validate --> NoStock --> Failed
    Validate --> PriceDrift --> Accepted
    Validate --> Accepted
    Failed --> Cancel --> NoRestock --> Job --> Republish
```

## SpecOps Maintenance Loop

```mermaid
flowchart LR
    Spec["specs/specops.md\nactive requirements"]
    Code["Odoo module and docs\nREQ evidence comments"]
    Tests["Static and native tests\nREQ evidence docstrings"]
    Audit["make specops"]
    Validate["make validate"]
    Findings["Fix missing or stale evidence"]

    Spec --> Code
    Spec --> Tests
    Code --> Audit
    Tests --> Audit
    Audit -->|"no findings"| Validate
    Audit -->|"errors, warnings, advisories"| Findings
    Findings --> Spec
    Findings --> Code
    Findings --> Tests
```
