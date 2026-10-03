from uuid import uuid4

from app.services.purchases import ValidationError, parse_quantity


INVENTORY_STATUSES = {
    "enough": "Достаточно",
    "running_low": "Заканчивается",
    "out": "Закончился",
    "check": "Нужно проверить",
}

SHOPPING_STATUSES = {
    "need": "Нужно купить",
    "soon": "Скоро понадобится",
    "do_not_buy": "Не покупать",
    "optional": "Опционально",
}


def _id():
    return str(uuid4())


def _required_operation_key(values):
    key = (values.get("idempotency_key") or "").strip()
    if not key:
        raise ValidationError("Не удалось безопасно повторить операцию. Обновите страницу.")
    return key


def _validate_inventory_status(value):
    if value not in INVENTORY_STATUSES:
        raise ValidationError("Выберите статус запаса.")
    return value


def _validate_shopping_status(value):
    if value not in SHOPPING_STATUSES:
        raise ValidationError("Выберите группу списка покупок.")
    return value


def _ensure_product(connection, name):
    product_name = (name or "").strip()
    if not product_name:
        raise ValidationError("Укажите название продукта.")
    row = connection.execute(
        "SELECT id FROM products WHERE name = ? COLLATE NOCASE", (product_name,)
    ).fetchone()
    if row:
        return row["id"]
    product_id = _id()
    connection.execute(
        "INSERT INTO products (id, name) VALUES (?, ?)", (product_id, product_name)
    )
    return product_id


def _sync_shopping_from_inventory(connection, product_id, inventory_status):
    if inventory_status in ("running_low", "out"):
        connection.execute(
            """INSERT INTO shopping_items (id, product_id, status, source)
               VALUES (?, ?, 'need', 'inventory_rule')
               ON CONFLICT(product_id) DO UPDATE SET
                 status = 'need', source = 'inventory_rule', updated_at = CURRENT_TIMESTAMP""",
            (_id(), product_id),
        )
    else:
        connection.execute(
            "DELETE FROM shopping_items WHERE product_id = ? AND source = 'inventory_rule'",
            (product_id,),
        )


def _log_change(
    connection,
    inventory_id,
    change_type,
    operation_key,
    old_quantity,
    new_quantity,
    old_status,
    new_status,
    from_location,
    to_location,
    details=None,
):
    connection.execute(
        """INSERT INTO inventory_changes
           (id, inventory_id, change_type, quantity_delta_milli, from_location,
            to_location, idempotency_key, old_quantity_milli, new_quantity_milli,
            old_status, new_status, details)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            _id(),
            inventory_id,
            change_type,
            None if old_quantity is None or new_quantity is None else new_quantity - old_quantity,
            from_location,
            to_location,
            operation_key,
            old_quantity,
            new_quantity,
            old_status,
            new_status,
            details,
        ),
    )


def save_inventory(database, values):
    operation_key = _required_operation_key(values)
    status = _validate_inventory_status(values.get("status"))
    quantity = parse_quantity(values.get("quantity"))
    approximate = 1 if values.get("quantity_is_approximate") in (True, "1", "on", "true") else 0
    unit = (values.get("unit") or "").strip() or None
    location = (values.get("location") or "").strip() or None

    with database.transaction() as connection:
        repeated = connection.execute(
            "SELECT inventory_id FROM inventory_changes WHERE idempotency_key = ?",
            (operation_key,),
        ).fetchone()
        if repeated:
            return repeated["inventory_id"], False

        product_id = _ensure_product(connection, values.get("product_name"))
        current = connection.execute(
            "SELECT * FROM home_inventory WHERE product_id = ?", (product_id,)
        ).fetchone()
        if current:
            inventory_id = current["id"]
            connection.execute(
                """UPDATE home_inventory SET quantity_milli = ?, unit = ?,
                   quantity_is_approximate = ?, status = ?, location = ?,
                   updated_at = CURRENT_TIMESTAMP WHERE id = ?""",
                (quantity, unit, approximate, status, location, inventory_id),
            )
            _log_change(
                connection, inventory_id, "update", operation_key,
                current["quantity_milli"], quantity, current["status"], status,
                current["location"], location,
            )
        else:
            inventory_id = _id()
            connection.execute(
                """INSERT INTO home_inventory
                   (id, product_id, quantity_milli, unit, quantity_is_approximate, status, location)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (inventory_id, product_id, quantity, unit, approximate, status, location),
            )
            _log_change(
                connection, inventory_id, "create", operation_key,
                None, quantity, None, status, None, location,
            )
        _sync_shopping_from_inventory(connection, product_id, status)
        return inventory_id, True


def move_inventory(database, inventory_id, values):
    operation_key = _required_operation_key(values)
    new_location = (values.get("location") or "").strip() or None
    with database.transaction() as connection:
        repeated = connection.execute(
            "SELECT id FROM inventory_changes WHERE idempotency_key = ?", (operation_key,)
        ).fetchone()
        if repeated:
            return False
        current = connection.execute(
            "SELECT * FROM home_inventory WHERE id = ?", (inventory_id,)
        ).fetchone()
        if not current:
            raise ValidationError("Запас не найден.")
        if current["location"] == new_location:
            return False
        connection.execute(
            "UPDATE home_inventory SET location = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (new_location, inventory_id),
        )
        _log_change(
            connection, inventory_id, "move", operation_key,
            current["quantity_milli"], current["quantity_milli"],
            current["status"], current["status"], current["location"], new_location,
        )
        return True


def list_inventory(database):
    with database.connect() as connection:
        return connection.execute(
            """SELECT i.*, p.name AS product_name
               FROM home_inventory i JOIN products p ON p.id = i.product_id
               ORDER BY p.name COLLATE NOCASE"""
        ).fetchall()


def list_inventory_changes(database, limit=100):
    with database.connect() as connection:
        return connection.execute(
            """SELECT c.*, p.name AS product_name, i.unit
               FROM inventory_changes c
               JOIN home_inventory i ON i.id = c.inventory_id
               JOIN products p ON p.id = i.product_id
               ORDER BY c.created_at DESC, c.rowid DESC LIMIT ?""",
            (limit,),
        ).fetchall()


def get_purchase_suggestion(database, receipt_id):
    with database.connect() as connection:
        return connection.execute(
            """SELECT r.id AS receipt_id, r.inventory_offer_status, i.product_id,
                      i.description, i.quantity_milli, i.unit
               FROM receipts r JOIN purchase_items i ON i.receipt_id = r.id
               WHERE r.id = ?""",
            (receipt_id,),
        ).fetchone()


def add_purchase_to_inventory(database, receipt_id, values):
    status = _validate_inventory_status(values.get("status"))
    location = (values.get("location") or "").strip() or None
    operation_key = "purchase-home:{}".format(receipt_id)
    with database.transaction() as connection:
        repeated = connection.execute(
            "SELECT inventory_id FROM inventory_changes WHERE idempotency_key = ?",
            (operation_key,),
        ).fetchone()
        if repeated:
            connection.execute(
                "UPDATE receipts SET inventory_offer_status = 'accepted' WHERE id = ?",
                (receipt_id,),
            )
            return repeated["inventory_id"], False

        purchase = connection.execute(
            """SELECT r.id AS receipt_id, i.product_id, i.description,
                      i.quantity_milli, i.unit
               FROM receipts r JOIN purchase_items i ON i.receipt_id = r.id
               WHERE r.id = ?""",
            (receipt_id,),
        ).fetchone()
        if not purchase:
            raise ValidationError("Покупка не найдена.")
        current = connection.execute(
            "SELECT * FROM home_inventory WHERE product_id = ?", (purchase["product_id"],)
        ).fetchone()
        if current:
            old_quantity = current["quantity_milli"]
            bought_quantity = purchase["quantity_milli"]
            if old_quantity is None:
                new_quantity = bought_quantity
            elif bought_quantity is None:
                new_quantity = old_quantity
            else:
                new_quantity = old_quantity + bought_quantity
            inventory_id = current["id"]
            connection.execute(
                """UPDATE home_inventory SET quantity_milli = ?, unit = COALESCE(?, unit),
                   status = ?, location = COALESCE(?, location), updated_at = CURRENT_TIMESTAMP
                   WHERE id = ?""",
                (new_quantity, purchase["unit"], status, location, inventory_id),
            )
            _log_change(
                connection, inventory_id, "purchase_add", operation_key,
                old_quantity, new_quantity, current["status"], status,
                current["location"], location or current["location"],
                "Добавлено после явного подтверждения покупки",
            )
        else:
            inventory_id = _id()
            connection.execute(
                """INSERT INTO home_inventory
                   (id, product_id, quantity_milli, unit, quantity_is_approximate, status, location)
                   VALUES (?, ?, ?, ?, 0, ?, ?)""",
                (inventory_id, purchase["product_id"], purchase["quantity_milli"], purchase["unit"], status, location),
            )
            _log_change(
                connection, inventory_id, "purchase_add", operation_key,
                None, purchase["quantity_milli"], None, status, None, location,
                "Добавлено после явного подтверждения покупки",
            )
        connection.execute(
            "UPDATE receipts SET inventory_offer_status = 'accepted' WHERE id = ?",
            (receipt_id,),
        )
        _sync_shopping_from_inventory(connection, purchase["product_id"], status)
        return inventory_id, True


def decline_purchase_suggestion(database, receipt_id):
    with database.transaction() as connection:
        connection.execute(
            "UPDATE receipts SET inventory_offer_status = 'declined' WHERE id = ?",
            (receipt_id,),
        )


def save_shopping_item(database, values):
    status = _validate_shopping_status(values.get("status"))
    with database.transaction() as connection:
        product_id = _ensure_product(connection, values.get("product_name"))
        connection.execute(
            """INSERT INTO shopping_items (id, product_id, status, source)
               VALUES (?, ?, ?, 'manual')
               ON CONFLICT(product_id) DO UPDATE SET
                 status = excluded.status, source = 'manual', updated_at = CURRENT_TIMESTAMP""",
            (_id(), product_id, status),
        )


def list_shopping_items(database):
    groups = {status: [] for status in SHOPPING_STATUSES}
    with database.connect() as connection:
        rows = connection.execute(
            """SELECT s.*, p.name AS product_name
               FROM shopping_items s JOIN products p ON p.id = s.product_id
               ORDER BY p.name COLLATE NOCASE"""
        ).fetchall()
    for row in rows:
        groups[row["status"]].append(row)
    return groups
