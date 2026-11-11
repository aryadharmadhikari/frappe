from pathlib import Path
from tempfile import TemporaryDirectory

import frappe
from frappe.core.doctype.data_import.data_import import export_json, import_doc
from frappe.tests import IntegrationTestCase


class TestFixtureImport(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		self.fixture_path = str(Path(self.enterContext(TemporaryDirectory())) / "fixture.json")

	def test_fixtures_import(self):
		titles = ("John", "Jane")
		names = [frappe.get_doc(doctype="Note", title=title).insert().name for title in titles]
		filters = {"name": ("in", names)}
		export_json("Note", self.fixture_path, filters=filters)
		frappe.db.delete("Note", filters)
		self.assertEqual(frappe.db.count("Note", filters), 0)

		import_doc(self.fixture_path)

		self.assertEqual(frappe.db.count("Note", filters), len(titles))
		self.assertCountEqual(frappe.get_all("Note", filters=filters, pluck="title"), titles)

	def test_singles_fixtures_import(self):
		frappe.db.after_rollback.add(lambda: frappe.clear_cache(doctype="Website Settings"))
		frappe.db.set_single_value("Website Settings", "title_prefix", "Fixture title")
		export_json("Website Settings", self.fixture_path)
		frappe.db.set_single_value("Website Settings", "title_prefix", "Temporary title")

		import_doc(self.fixture_path)

		self.assertEqual(frappe.db.get_single_value("Website Settings", "title_prefix"), "Fixture title")
