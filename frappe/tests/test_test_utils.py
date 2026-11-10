import logging
import time
from datetime import timedelta

import frappe
from frappe.tests import IntegrationTestCase
from frappe.tests.utils.generators import get_missing_records_doctypes, get_modules
from frappe.utils.data import now_datetime


class TestTestUtils(IntegrationTestCase):
	ROLLBACK_AFTER_EACH_TEST = True
	SHOW_TRANSACTION_COMMIT_WARNINGS = True

	def test_rollback_after_each_test(self):
		key = frappe.generate_hash()

		class RollbackCase(IntegrationTestCase):
			ROLLBACK_AFTER_EACH_TEST = True

			def setUp(self):
				frappe.db.set_default(key, "set by setup")
				if self._testMethodName == "test_failed_setup":
					raise RuntimeError("setup failed")

			def test_success(self):
				frappe.db.set_default(key, "set by test")

			def test_failed_setup(self):
				pass

		result = RollbackCase("test_success").run()
		self.assertTrue(result.wasSuccessful(), result.errors)
		self.assertIsNone(frappe.db.get_default(key))

		result = RollbackCase("test_failed_setup").run()
		self.assertEqual(len(result.errors), 1)
		self.assertIn("RuntimeError: setup failed", result.errors[0][1])
		self.assertIsNone(frappe.db.get_default(key))

	def test_rollback_contains_callback_writes_and_restores_flags(self):
		description = frappe.generate_hash()
		callbacks = []

		class RollbackCase(IntegrationTestCase):
			ROLLBACK_AFTER_EACH_TEST = True

			def test_callback(self):
				frappe.flags.ignore_links = True
				frappe.db.before_rollback.add(lambda: callbacks.append("before"))
				frappe.db.after_rollback.add(self.write_after_rollback)
				frappe.db.after_commit.add(lambda: callbacks.append("commit"))

			def write_after_rollback(self):
				callbacks.append("after")
				frappe.get_doc(doctype="ToDo", description=description).insert()

		result = RollbackCase("test_callback").run()
		self.assertTrue(result.wasSuccessful(), result.errors)
		self.assertEqual(callbacks, ["before", "after"])
		self.assertFalse(frappe.db.exists("ToDo", {"description": description}))
		self.assertFalse(frappe.flags.ignore_links)

	def test_document_assertions(self):
		currency = frappe.new_doc("Currency")
		currency.currency_name = "STONKS"
		currency.smallest_currency_fraction_value = 0.420_001
		currency.save()

		self.assertDocumentEqual(currency.as_dict(), currency)

	def test_thread_locals(self):
		frappe.flags.temp_flag_to_be_discarded = True

	def test_temp_setting_changes(self):
		current_setting = frappe.get_system_settings("logout_on_password_reset")

		with IntegrationTestCase.change_settings(
			"System Settings", {"logout_on_password_reset": int(not current_setting)}
		):
			updated_settings = frappe.get_single_value("System Settings", "logout_on_password_reset")
			self.assertNotEqual(current_setting, updated_settings)

		restored_settings = frappe.get_single_value("System Settings", "logout_on_password_reset")
		self.assertEqual(current_setting, restored_settings)

	def test_time_freezing(self):
		now = now_datetime()

		tomorrow = now + timedelta(days=1)
		with self.freeze_time(tomorrow):
			self.assertEqual(now_datetime(), tomorrow)

	def test_outer_timeout_fires_after_inner_timeout_exits(self):
		self.addCleanup(frappe.set_user, "Administrator")
		frappe.set_user("test@example.com")

		with self.assertRaisesRegex(Exception, "outer timed out"):
			with self.timeout_context(1, "outer timed out"):
				with self.timeout_context(5):
					pass
				time.sleep(2)

	def test_outer_timeout_fires_inside_longer_inner_timeout(self):
		self.addCleanup(frappe.set_user, "Administrator")
		frappe.set_user("test@example.com")

		with self.assertRaisesRegex(Exception, "Operation timed out"):
			with self.timeout_context(1):
				with self.timeout_context(5):
					time.sleep(2)

	def test_get_modules_returns_none_for_missing_doctype(self):
		"""DocTypes from uninstalled apps should resolve to (None, None) instead of raising."""
		get_modules.cache_clear()
		try:
			module, test_module = get_modules("Definitely Not A Real DocType")
		finally:
			get_modules.cache_clear()
		self.assertIsNone(module)
		self.assertIsNone(test_module)

	def test_get_missing_records_doctypes_skips_missing_doctype(self):
		"""Missing link targets should be skipped with a warning, not crash the walk."""
		get_modules.cache_clear()
		try:
			with self.assertLogs("frappe.testing.generators", level=logging.WARNING) as log_ctx:
				result = get_missing_records_doctypes("Definitely Not A Real DocType")
		finally:
			get_modules.cache_clear()
		self.assertEqual(result, [])
		self.assertTrue(
			any("Definitely Not A Real DocType" in line for line in log_ctx.output),
			f"Expected warning mentioning the missing doctype, got: {log_ctx.output}",
		)


class TestCompatAssertQueryCount(IntegrationTestCase):
	"""Cover the `assertQueryCount` copy carried by the deprecated FrappeTestCase.

	`frappe.tests.utils.FrappeTestCase` keeps its own implementation, separate from
	the one on IntegrationTestCase. Nothing else in-tree exercises it, so a fix
	applied to one can silently miss the other.

	Deliberately not restricted by db_type: the two copies drift apart on driver
	details. `last_query` is a plain str only on MariaDB — SQLite hands back a
	LazyMogrify and Postgres a LazyDecode — so a MariaDB-only test cannot catch a
	regression here.
	"""

	ROLLBACK_AFTER_EACH_TEST = True

	def compat_case(self):
		from frappe.tests.utils import FrappeTestCase

		class _Case(FrappeTestCase):
			def runTest(self):
				pass

		return _Case()

	def test_query_under_the_budget_passes(self):
		with self.compat_case().assertQueryCount(5):
			frappe.db.sql("select 1")

	def test_going_over_the_budget_reports_the_executed_queries(self):
		# The failure message is built by joining the collected queries, and it is an
		# eagerly evaluated argument — so this path runs even when the count fits.
		with self.assertRaises(AssertionError) as raised:
			with self.compat_case().assertQueryCount(0):
				frappe.db.sql("select 1")

		self.assertIn("Queries executed:", str(raised.exception))


def tearDownModule():
	"""assertions for ensuring tests didn't leave state behind"""
	assert "temp_flag_to_be_discarded" not in frappe.flags
	assert not frappe.db.exists("Currency", "STONKS")
