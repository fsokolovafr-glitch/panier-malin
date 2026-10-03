CREATE TABLE receipts (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL CHECK (source IN ('manual', 'receipt')),
    status TEXT NOT NULL CHECK (status IN ('draft', 'confirmed')),
    idempotency_key TEXT NOT NULL UNIQUE,
    fingerprint TEXT UNIQUE,
    store TEXT,
    purchase_date TEXT,
    currency TEXT NOT NULL CHECK (length(currency) = 3),
    included_in_budget INTEGER NOT NULL DEFAULT 1 CHECK (included_in_budget IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE products (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL COLLATE NOCASE UNIQUE,
    category TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE product_aliases (
    id TEXT PRIMARY KEY,
    product_id TEXT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    store_name TEXT NOT NULL,
    store TEXT,
    UNIQUE(product_id, store_name, store)
);

CREATE TABLE purchase_items (
    id TEXT PRIMARY KEY,
    receipt_id TEXT NOT NULL REFERENCES receipts(id) ON DELETE CASCADE,
    product_id TEXT REFERENCES products(id),
    description TEXT NOT NULL,
    quantity_milli INTEGER,
    unit TEXT,
    total_cents INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE purchase_item_changes (
    id TEXT PRIMARY KEY,
    item_id TEXT NOT NULL REFERENCES purchase_items(id) ON DELETE CASCADE,
    old_total_cents INTEGER,
    new_total_cents INTEGER,
    changed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE home_inventory (
    id TEXT PRIMARY KEY,
    product_id TEXT NOT NULL REFERENCES products(id),
    quantity_milli INTEGER,
    unit TEXT,
    quantity_is_approximate INTEGER NOT NULL DEFAULT 0 CHECK (quantity_is_approximate IN (0, 1)),
    status TEXT NOT NULL CHECK (status IN ('enough', 'running_low', 'out', 'check')),
    location TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE inventory_changes (
    id TEXT PRIMARY KEY,
    inventory_id TEXT NOT NULL REFERENCES home_inventory(id) ON DELETE CASCADE,
    change_type TEXT NOT NULL,
    quantity_delta_milli INTEGER,
    from_location TEXT,
    to_location TEXT,
    idempotency_key TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE budget_settings (
    id TEXT PRIMARY KEY,
    month TEXT NOT NULL,
    currency TEXT NOT NULL CHECK (length(currency) = 3),
    limit_cents INTEGER CHECK (limit_cents IS NULL OR limit_cents >= 0),
    UNIQUE(month, currency)
);

CREATE INDEX idx_receipts_purchase_date ON receipts(purchase_date);
CREATE INDEX idx_purchase_items_description ON purchase_items(description);
CREATE INDEX idx_product_aliases_store_name ON product_aliases(store_name);
