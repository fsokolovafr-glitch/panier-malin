from datetime import date
from decimal import Decimal, InvalidOperation
from uuid import uuid4


class ValidationError(ValueError):
    pass


def _id():
    return str(uuid4())


def parse_money(value, required=False):
    raw = (value or "").strip().replace(",", ".")
    if not raw:
        if required:
            raise ValidationError("Укажите сумму.")
        return None
    try:
        amount = Decimal(raw)
    except InvalidOperation as error:
        raise ValidationError("Сумма указана неверно.") from error
    if amount < 0 or amount.as_tuple().exponent < -2:
        raise ValidationError("Сумма должна быть неотрицательной, не более двух знаков после запятой.")
    return int(amount * 100)


def parse_quantity(value):
    raw = (value or "").strip().replace(",", ".")
    if not raw:
        return None
    try:
        quantity = Decimal(raw)
    except InvalidOperation as error:
        raise ValidationError("Количество указано неверно.") from error
    if quantity < 0 or quantity.as_tuple().exponent < -3:
        raise ValidationError("Количество должно быть неотрицательным, не более трёх знаков после запятой.")
    return int(quantity * 1000)


def validate_date(value):
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        date.fromisoformat(raw)
    except ValueError as error:
        raise ValidationError("Дата указана неверно.") from error
    return raw


def validate_month(value):
    raw = (value or "").strip()
    try:
        date.fromisoformat(raw + "-01")
    except ValueError as error:
        raise ValidationError("Месяц указан неверно.") from error
    return raw


def validate_currency(value):
    currency = (value or "").strip().upper()
    if len(currency) != 3 or not currency.isalpha():
        raise ValidationError("Валюта должна быть трёхбуквенным кодом, например EUR.")
    return currency


def create_purchase(database, values):
    description = (values.get("description") or "").strip()
    if not description:
        raise ValidationError("Укажите, что куплено.")

    idempotency_key = (values.get("idempotency_key") or "").strip()
    if not idempotency_key:
        raise ValidationError("Не удалось безопасно сохранить форму. Обновите страницу.")

    total_cents = parse_money(values.get("total"))
    quantity_milli = parse_quantity(values.get("quantity"))
    purchase_date = validate_date(values.get("purchase_date"))
    currency = validate_currency(values.get("currency"))
    requested_draft = values.get("draft") in (True, "1", "on", "true")
    status = "draft" if requested_draft or total_cents is None else "confirmed"
    store = (values.get("store") or "").strip() or None
    unit = (values.get("unit") or "").strip() or None
    category = (values.get("category") or "").strip() or None

    with database.transaction() as connection:
        existing = connection.execute(
            "SELECT id FROM receipts WHERE idempotency_key = ?", (idempotency_key,)
        ).fetchone()
        if existing:
            return existing["id"], False

        product = connection.execute(
            "SELECT id FROM products WHERE name = ? COLLATE NOCASE", (description,)
        ).fetchone()
        product_id = product["id"] if product else _id()
        if not product:
            connection.execute(
                "INSERT INTO products (id, name, category) VALUES (?, ?, ?)",
                (product_id, description, category),
            )
        elif category:
            connection.execute(
                "UPDATE products SET category = COALESCE(category, ?) WHERE id = ?",
                (category, product_id),
            )

        if store:
            connection.execute(
                "INSERT OR IGNORE INTO product_aliases (id, product_id, store_name, store) VALUES (?, ?, ?, ?)",
                (_id(), product_id, description, store),
            )

        receipt_id = _id()
        connection.execute(
            """INSERT INTO receipts
               (id, source, status, idempotency_key, store, purchase_date, currency, included_in_budget)
               VALUES (?, 'manual', ?, ?, ?, ?, ?, 1)""",
            (receipt_id, status, idempotency_key, store, purchase_date, currency),
        )
        connection.execute(
            """INSERT INTO purchase_items
               (id, receipt_id, product_id, description, quantity_milli, unit, total_cents)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (_id(), receipt_id, product_id, description, quantity_milli, unit, total_cents),
        )
        return receipt_id, True


def list_purchases(database, query=""):
    needle = "%{}%".format(query.strip())
    with database.connect() as connection:
        return connection.execute(
            """SELECT r.id AS receipt_id, r.status, r.store, r.purchase_date, r.currency,
                      r.inventory_offer_status,
                      r.included_in_budget, i.id AS item_id, i.description,
                      i.quantity_milli, i.unit, i.total_cents
               FROM receipts r
               JOIN purchase_items i ON i.receipt_id = r.id
               WHERE (? = '%%' OR i.description LIKE ? COLLATE NOCASE OR COALESCE(r.store, '') LIKE ? COLLATE NOCASE)
               ORDER BY COALESCE(r.purchase_date, '') DESC, r.created_at DESC""",
            (needle, needle, needle),
        ).fetchall()


def update_purchase(database, receipt_id, values):
    total_cents = parse_money(values.get("total"))
    included = 1 if values.get("included_in_budget") in (True, "1", "on", "true") else 0
    confirmed = values.get("confirmed") in (True, "1", "on", "true")
    if confirmed and total_cents is None:
        raise ValidationError("Нельзя подтвердить покупку без суммы.")
    status = "confirmed" if confirmed else "draft"

    with database.transaction() as connection:
        item = connection.execute(
            "SELECT id, total_cents FROM purchase_items WHERE receipt_id = ?", (receipt_id,)
        ).fetchone()
        if not item:
            raise ValidationError("Покупка не найдена.")
        if item["total_cents"] != total_cents:
            connection.execute(
                """INSERT INTO purchase_item_changes
                   (id, item_id, old_total_cents, new_total_cents) VALUES (?, ?, ?, ?)""",
                (_id(), item["id"], item["total_cents"], total_cents),
            )
        connection.execute(
            "UPDATE purchase_items SET total_cents = ? WHERE id = ?",
            (total_cents, item["id"]),
        )
        connection.execute(
            """UPDATE receipts SET status = ?, included_in_budget = ?, updated_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (status, included, receipt_id),
        )


def budget_summary(database, month):
    validate_month(month)
    with database.connect() as connection:
        spent_rows = connection.execute(
            """SELECT r.currency, COALESCE(SUM(i.total_cents), 0) AS spent_cents
               FROM receipts r
               JOIN purchase_items i ON i.receipt_id = r.id
               WHERE r.status = 'confirmed'
                 AND r.included_in_budget = 1
                 AND substr(r.purchase_date, 1, 7) = ?
                 AND i.total_cents IS NOT NULL
               GROUP BY r.currency""",
            (month,),
        ).fetchall()
        limits = connection.execute(
            "SELECT currency, limit_cents FROM budget_settings WHERE month = ?",
            (month,),
        ).fetchall()

    by_currency = {row["currency"]: {"spent_cents": row["spent_cents"], "limit_cents": None} for row in spent_rows}
    for row in limits:
        by_currency.setdefault(row["currency"], {"spent_cents": 0, "limit_cents": None})
        by_currency[row["currency"]]["limit_cents"] = row["limit_cents"]
    return by_currency


def set_budget(database, month, currency, amount):
    month = validate_month(month)
    currency = validate_currency(currency)
    limit_cents = parse_money(amount)
    with database.transaction() as connection:
        if limit_cents is None:
            connection.execute(
                "DELETE FROM budget_settings WHERE month = ? AND currency = ?",
                (month, currency),
            )
        else:
            connection.execute(
                """INSERT INTO budget_settings (id, month, currency, limit_cents)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(month, currency) DO UPDATE SET limit_cents = excluded.limit_cents""",
                (_id(), month, currency, limit_cents),
            )


def overview_breakdown(database, month):
    with database.connect() as connection:
        categories = connection.execute(
            """SELECT COALESCE(p.category, 'Без категории') AS label, r.currency,
                      SUM(i.total_cents) AS total_cents
               FROM receipts r JOIN purchase_items i ON i.receipt_id = r.id
               LEFT JOIN products p ON p.id = i.product_id
               WHERE r.status = 'confirmed' AND r.included_in_budget = 1
                 AND substr(r.purchase_date, 1, 7) = ? AND i.total_cents IS NOT NULL
               GROUP BY label, r.currency ORDER BY total_cents DESC""",
            (month,),
        ).fetchall()
        stores = connection.execute(
            """SELECT COALESCE(r.store, 'Магазин не указан') AS label, r.currency,
                      SUM(i.total_cents) AS total_cents
               FROM receipts r JOIN purchase_items i ON i.receipt_id = r.id
               WHERE r.status = 'confirmed' AND r.included_in_budget = 1
                 AND substr(r.purchase_date, 1, 7) = ? AND i.total_cents IS NOT NULL
               GROUP BY label, r.currency ORDER BY total_cents DESC""",
            (month,),
        ).fetchall()
    return categories, stores


def format_cents(value):
    if value is None:
        return "не указана"
    sign = "-" if value < 0 else ""
    units, cents = divmod(abs(value), 100)
    grouped_units = "{:,}".format(units).replace(",", " ")
    return "{}{},{:02d}".format(sign, grouped_units, cents)


def format_quantity(value):
    if value is None:
        return "не указано"
    sign = "-" if value < 0 else ""
    units, fraction = divmod(abs(value), 1000)
    if not fraction:
        return "{}{}".format(sign, units)
    return "{}{},{}".format(sign, units, "{:03d}".format(fraction).rstrip("0"))
