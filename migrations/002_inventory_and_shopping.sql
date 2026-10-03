ALTER TABLE receipts ADD COLUMN inventory_offer_status TEXT NOT NULL DEFAULT 'pending'
    CHECK (inventory_offer_status IN ('pending', 'accepted', 'declined'));

ALTER TABLE inventory_changes ADD COLUMN old_quantity_milli INTEGER;
ALTER TABLE inventory_changes ADD COLUMN new_quantity_milli INTEGER;
ALTER TABLE inventory_changes ADD COLUMN old_status TEXT;
ALTER TABLE inventory_changes ADD COLUMN new_status TEXT;
ALTER TABLE inventory_changes ADD COLUMN details TEXT;

CREATE UNIQUE INDEX idx_home_inventory_product ON home_inventory(product_id);

CREATE TABLE shopping_items (
    id TEXT PRIMARY KEY,
    product_id TEXT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK (status IN ('need', 'soon', 'do_not_buy', 'optional')),
    source TEXT NOT NULL CHECK (source IN ('manual', 'inventory_rule')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(product_id)
);

CREATE INDEX idx_shopping_items_status ON shopping_items(status);
