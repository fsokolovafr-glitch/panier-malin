import tempfile
import unittest
from pathlib import Path

from app import create_app
from app.services.inventory import (
    list_inventory,
    list_inventory_changes,
    list_shopping_items,
)
from app.services.purchases import list_purchases


class Stage3InventoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = str(Path(self.temporary_directory.name) / "test.sqlite3")
        self.app = create_app(
            {
                "TESTING": True,
                "DATABASE_PATH": self.database_path,
                "SECRET_KEY": "test-only",
                "DEFAULT_CURRENCY": "EUR",
            }
        )
        self.client = self.app.test_client()
        self.database = self.app.extensions["database"]

    def tearDown(self):
        self.temporary_directory.cleanup()

    def _save_inventory(
        self,
        key,
        product_name="Тестовый домашний продукт",
        status="enough",
        quantity="2",
        unit="шт.",
        location="Холодильник",
        approximate=False,
    ):
        return self.client.post(
            "/home/save",
            data={
                "idempotency_key": key,
                "product_name": product_name,
                "status": status,
                "quantity": quantity,
                "unit": unit,
                "location": location,
                "quantity_is_approximate": "on" if approximate else "",
            },
        )

    def _save_purchase(self, key="purchase-for-home"):
        return self.client.post(
            "/add",
            data={
                "idempotency_key": key,
                "description": "Тестовая упаковка",
                "total": "4,50",
                "currency": "EUR",
                "quantity": "2",
                "unit": "шт.",
                "purchase_date": "2026-10-03",
            },
        )

    def test_07_repeated_move_changes_location_without_duplicate_or_quantity_change(self):
        self._save_inventory("create-home")
        original = list_inventory(self.database)[0]
        move_data = {"idempotency_key": "move-to-freezer", "location": "Морозилка"}

        self.client.post("/home/{}/move".format(original["id"]), data=move_data)
        self.client.post("/home/{}/move".format(original["id"]), data=move_data)

        inventory = list_inventory(self.database)
        moves = [change for change in list_inventory_changes(self.database) if change["change_type"] == "move"]
        self.assertEqual(len(inventory), 1)
        self.assertEqual(inventory[0]["location"], "Морозилка")
        self.assertEqual(inventory[0]["quantity_milli"], 2000)
        self.assertEqual(len(moves), 1)
        self.assertEqual(moves[0]["from_location"], "Холодильник")
        self.assertEqual(moves[0]["to_location"], "Морозилка")

    def test_purchase_waits_for_confirmation_before_becoming_inventory(self):
        response = self._save_purchase()
        self.assertEqual(response.status_code, 302)
        self.assertIn("home-suggestion", response.headers["Location"])
        self.assertEqual(list_inventory(self.database), [])
        self.assertEqual(sum(len(items) for items in list_shopping_items(self.database).values()), 0)

    def test_repeated_purchase_confirmation_does_not_double_inventory(self):
        self._save_purchase()
        receipt_id = list_purchases(self.database)[0]["receipt_id"]
        confirmation = {"decision": "accept", "status": "enough", "location": "Шкаф"}

        self.client.post("/purchases/{}/home-suggestion".format(receipt_id), data=confirmation)
        self.client.post("/purchases/{}/home-suggestion".format(receipt_id), data=confirmation)

        inventory = list_inventory(self.database)
        purchase_adds = [
            change
            for change in list_inventory_changes(self.database)
            if change["change_type"] == "purchase_add"
        ]
        self.assertEqual(len(inventory), 1)
        self.assertEqual(inventory[0]["quantity_milli"], 2000)
        self.assertEqual(len(purchase_adds), 1)

    def test_running_low_and_out_product_is_in_need_to_buy_group(self):
        self._save_inventory("low-stock", status="running_low")
        groups = list_shopping_items(self.database)
        self.assertEqual([item["product_name"] for item in groups["need"]], ["Тестовый домашний продукт"])

        self._save_inventory("out-stock", status="out", quantity="0")
        groups = list_shopping_items(self.database)
        self.assertEqual(len(groups["need"]), 1)
        self.assertEqual(groups["need"][0]["source"], "inventory_rule")

    def test_enough_status_removes_only_automatic_shopping_item(self):
        self._save_inventory("low-stock", status="running_low")
        self._save_inventory("enough-stock", status="enough", quantity="5")
        groups = list_shopping_items(self.database)
        self.assertEqual(sum(len(items) for items in groups.values()), 0)

    def test_unknown_approximate_quantity_and_location_are_preserved(self):
        self._save_inventory(
            "approximate-stock",
            quantity="",
            location="",
            approximate=True,
            status="check",
        )
        item = list_inventory(self.database)[0]
        self.assertIsNone(item["quantity_milli"])
        self.assertIsNone(item["location"])
        self.assertEqual(item["quantity_is_approximate"], 1)
        self.assertEqual(item["status"], "check")

    def test_manual_shopping_groups_are_independent_from_receipt_history(self):
        self._save_purchase()
        self.client.post(
            "/shopping/save",
            data={"product_name": "Тестовый пункт списка", "status": "optional"},
        )
        groups = list_shopping_items(self.database)
        self.assertEqual([item["product_name"] for item in groups["optional"]], ["Тестовый пункт списка"])
        all_names = [item["product_name"] for items in groups.values() for item in items]
        self.assertNotIn("Тестовая упаковка", all_names)


if __name__ == "__main__":
    unittest.main()
