import sqlite3
import tempfile
import unittest
from pathlib import Path

from app import create_app
from app.services.purchases import budget_summary, list_purchases


class Stage2AcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = str(Path(self.temporary_directory.name) / "test.sqlite3")
        self.app = self._new_app()
        self.client = self.app.test_client()

    def tearDown(self):
        self.temporary_directory.cleanup()

    def _new_app(self):
        return create_app(
            {
                "TESTING": True,
                "DATABASE_PATH": self.database_path,
                "SECRET_KEY": "test-only",
                "DEFAULT_CURRENCY": "EUR",
            }
        )

    def _add(self, key="test-purchase-1", total="12,34", draft=False):
        return self.client.post(
            "/add",
            data={
                "idempotency_key": key,
                "description": "Тестовый продукт",
                "total": total,
                "currency": "EUR",
                "quantity": "2",
                "unit": "шт.",
                "store": "Тестовый магазин",
                "purchase_date": "2026-10-03",
                "category": "Тестовая категория",
                "draft": "on" if draft else "",
            },
            follow_redirects=True,
        )

    def _first_purchase(self):
        return list_purchases(self.app.extensions["database"])[0]

    def test_01_purchase_survives_application_restart_with_same_id(self):
        response = self._add()
        self.assertEqual(response.status_code, 200)
        original = self._first_purchase()

        restarted_app = self._new_app()
        restarted = list_purchases(restarted_app.extensions["database"])[0]

        self.assertEqual(restarted["receipt_id"], original["receipt_id"])
        self.assertEqual(restarted["description"], "Тестовый продукт")

    def test_02_confirmed_purchase_is_reflected_in_budget(self):
        self._add(total="12,34")
        summary = budget_summary(self.app.extensions["database"], "2026-10")
        self.assertEqual(summary["EUR"]["spent_cents"], 1234)
        self.assertIsNone(summary["EUR"]["limit_cents"])

    def test_03_price_correction_recalculates_totals_and_keeps_audit(self):
        self._add(total="12,34")
        purchase = self._first_purchase()
        self.client.post(
            "/purchases/{}/edit".format(purchase["receipt_id"]),
            data={"total": "15,67", "confirmed": "on", "included_in_budget": "on"},
        )

        summary = budget_summary(self.app.extensions["database"], "2026-10")
        self.assertEqual(summary["EUR"]["spent_cents"], 1567)
        with sqlite3.connect(self.database_path) as connection:
            audit = connection.execute(
                "SELECT old_total_cents, new_total_cents FROM purchase_item_changes"
            ).fetchone()
        self.assertEqual(audit, (1234, 1567))

    def test_04_excluded_purchase_stays_in_history_but_not_budget(self):
        self._add()
        purchase = self._first_purchase()
        self.client.post(
            "/purchases/{}/edit".format(purchase["receipt_id"]),
            data={"total": "12,34", "confirmed": "on"},
        )

        history = list_purchases(self.app.extensions["database"])
        summary = budget_summary(self.app.extensions["database"], "2026-10")
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["included_in_budget"], 0)
        self.assertNotIn("EUR", summary)

    def test_08_draft_does_not_affect_expenses(self):
        self._add(total="12,34", draft=True)
        history = list_purchases(self.app.extensions["database"])
        summary = budget_summary(self.app.extensions["database"], "2026-10")
        self.assertEqual(history[0]["status"], "draft")
        self.assertNotIn("EUR", summary)

    def test_duplicate_form_submission_creates_one_purchase(self):
        self._add(key="same-operation")
        response = self._add(key="same-operation")
        self.assertIn("дубль не создан".encode("utf-8"), response.data)
        self.assertEqual(len(list_purchases(self.app.extensions["database"])), 1)

    def test_all_five_russian_screens_open(self):
        for path, title in (
            ("/", "Обзор"),
            ("/add", "Добавить"),
            ("/purchases", "Покупки"),
            ("/home", "Дома"),
            ("/shopping", "Купить"),
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn(title.encode("utf-8"), response.data)

    def test_required_schema_entities_exist(self):
        expected = {
            "receipts",
            "purchase_items",
            "products",
            "product_aliases",
            "home_inventory",
            "inventory_changes",
            "budget_settings",
        }
        with sqlite3.connect(self.database_path) as connection:
            actual = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        self.assertTrue(expected.issubset(actual))

    def test_money_with_more_than_two_decimals_is_rejected(self):
        response = self._add(key="bad-money", total="1,999")
        self.assertIn("не более двух знаков".encode("utf-8"), response.data)
        self.assertEqual(list_purchases(self.app.extensions["database"]), [])

    def test_blank_optional_values_stay_unknown_and_create_draft(self):
        self.client.post(
            "/add",
            data={
                "idempotency_key": "unknown-values",
                "description": "Товар без подробностей",
                "currency": "EUR",
            },
        )
        purchase = self._first_purchase()
        self.assertEqual(purchase["status"], "draft")
        self.assertIsNone(purchase["total_cents"])
        self.assertIsNone(purchase["quantity_milli"])
        self.assertIsNone(purchase["store"])
        self.assertIsNone(purchase["purchase_date"])

    def test_search_filters_purchase_history(self):
        self._add(key="search-first")
        self.client.post(
            "/add",
            data={
                "idempotency_key": "search-second",
                "description": "Другой товар",
                "total": "5,00",
                "currency": "EUR",
                "purchase_date": "2026-10-03",
            },
        )
        response = self.client.get("/purchases?query=Другой")
        self.assertIn("Другой товар".encode("utf-8"), response.data)
        self.assertNotIn("Тестовый продукт".encode("utf-8"), response.data)

    def test_monthly_budget_can_be_set_and_removed(self):
        self.client.post(
            "/budget",
            data={"month": "2026-10", "currency": "EUR", "amount": "350,00"},
        )
        with_limit = budget_summary(self.app.extensions["database"], "2026-10")
        self.assertEqual(with_limit["EUR"]["limit_cents"], 35000)

        self.client.post(
            "/budget",
            data={"month": "2026-10", "currency": "EUR", "amount": ""},
        )
        without_limit = budget_summary(self.app.extensions["database"], "2026-10")
        self.assertNotIn("EUR", without_limit)


if __name__ == "__main__":
    unittest.main()
