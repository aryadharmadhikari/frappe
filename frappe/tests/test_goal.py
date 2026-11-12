# Copyright (c) 2022, Frappe Technologies Pvt. Ltd. and Contributors
# License: MIT. See LICENSE

import frappe
from frappe.tests import IntegrationTestCase
from frappe.tests.test_db_query import setup_patched_blog_post, setup_test_user
from frappe.tests.test_helpers import setup_for_tests
from frappe.tests.utils import load_test_records_for
from frappe.utils import format_date, today
from frappe.utils.goal import get_monthly_goal_graph_data, get_monthly_results

EXTRA_TEST_RECORD_DEPENDENCIES = ["User"]


class TestGoal(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.events = {row["subject"]: row for row in load_test_records_for("Event")["Event"]}

	def make_event(self, subject):
		return frappe.copy_doc(self.events[subject]).insert()

	def make_private_events(self):
		return [self.make_event(subject).name for subject in ("_Test Event 2", "_Test Event 3")]

	def test_get_monthly_results(self):
		"""Test monthly aggregation values of a field"""
		result_dict = get_monthly_results(
			"Event",
			"subject",
			"creation",
			filters={"name": ["in", self.make_private_events()], "event_type": "Private"},
			aggregation="count",
		)

		self.assertEqual(result_dict.get(format_date(today(), "MM-yyyy")), 2)

	def test_get_monthly_results_sum(self):
		"""sum/avg must aggregate the column, not a string literal -- the literal form
		(`sum('send_reminder')`) sums to 0 on MariaDB and errors on Postgres."""
		names = self.make_private_events()
		for name in names:
			frappe.db.set_value("Event", name, "send_reminder", 1)

		result_dict = get_monthly_results(
			"Event",
			"send_reminder",
			"creation",
			filters={"name": ["in", names], "event_type": "Private"},
			aggregation="sum",
		)

		self.assertEqual(result_dict.get(format_date(today(), "MM-yyyy")), 2)

	def test_get_monthly_results_field_permissions(self):
		setup_for_tests()
		with setup_patched_blog_post(), setup_test_user(set_user=True):
			get_monthly_results("Test Blog Post", "idx", "creation", {}, "max")

			for goal_field, date_col in (("published", "creation"), ("idx", "published")):
				with self.subTest(goal_field=goal_field, date_col=date_col):
					with self.assertRaises(frappe.PermissionError):
						get_monthly_results("Test Blog Post", goal_field, date_col, {}, "max")

	def test_get_monthly_goal_graph_data(self):
		"""Test for accurate values in graph data (based on test_get_monthly_results)"""
		docname = self.make_event("_Test Event 1").name
		frappe.db.set_value("Event", docname, "description", 1)
		data = get_monthly_goal_graph_data(
			"Test",
			"Event",
			docname,
			"description",
			"description",
			"description",
			"Event",
			"",
			"description",
			"creation",
			filters={"name": docname, "starts_on": "2014-01-01"},
			aggregation="count",
		)
		self.assertEqual(float(data["data"]["datasets"][0]["values"][-1]), 1)
		self.assertEqual(data["fieldtype"], "Text Editor")
		self.assertIsNone(data["currency"])

	def test_monthly_goal_graph_data_for_a_standard_field(self):
		docname = self.make_event("_Test Event 1").name
		data = get_monthly_goal_graph_data(
			"Test",
			"Event",
			docname,
			"idx",
			"idx",
			"description",
			"Event",
			"",
			"description",
			"creation",
			filters={"name": docname, "starts_on": "2014-01-01"},
			aggregation="count",
		)
		self.assertIsNone(data["fieldtype"])
		self.assertIsNone(data["currency"])
