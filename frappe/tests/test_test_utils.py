import logging
import time
from collections import defaultdict
from datetime import timedelta
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, TestResult
from unittest.mock import patch

import frappe
from frappe.testing.result import TestResult as FrappeTestResult
from frappe.tests import IntegrationTestCase, UnitTestCase
from frappe.tests.baseline import DESK_USER, SYSTEM_MANAGER, WEBSITE_MANAGER, WORKSPACE_MANAGER
from frappe.tests.utils import cleanup_committed_test_data, generators
from frappe.tests.utils.generators import get_missing_records_doctypes, get_modules
from frappe.utils.data import now_datetime


class TestSubtestReporting(UnitTestCase):
	def test_failed_subtest_reports_original_error_to_plain_stream(self):
		class FailingSubtest(TestCase):
			def runTest(self):
				with self.subTest(layer="site"):
					raise KeyError("Test Dock Beta")

		stream = StringIO()
		result = FrappeTestResult(stream, descriptions=True, verbosity=2)
		FailingSubtest().run(result)

		self.assertEqual(len(result.errors), 1)
		self.assertIn("KeyError: 'Test Dock Beta'", result.errors[0][1].splitlines()[-1])
		self.assertIn("layer='site'", stream.getvalue())


class TestTestUtils(IntegrationTestCase):
	SHOW_TRANSACTION_COMMIT_WARNINGS = True

	def test_document_assertions(self):
		currency = frappe.new_doc("Currency")
		currency.currency_name = "STONKS"
		currency.smallest_currency_fraction_value = 0.420_001
		currency.save()

		self.assertDocumentEqual(currency.as_dict(), currency)

	def test_integration_teardown_rolls_back_test_and_cleanup_writes(self):
		description = f"Rollback test {frappe.generate_hash()}"

		class RollbackCase(IntegrationTestCase):
			def runTest(self):
				frappe.get_doc(doctype="ToDo", description=description).insert()
				self.addCleanup(frappe.get_doc(doctype="ToDo", description=description).insert)
				self.assertTrue(frappe.db.exists("ToDo", {"description": description}))

			def tearDown(self):
				self.assertTrue(frappe.db.exists("ToDo", {"description": description}))
				super().tearDown()

		with self.set_user("test@example.com"):
			result = RollbackCase().run(TestResult())

		self.assertTrue(result.wasSuccessful(), result.errors + result.failures)
		self.assertFalse(frappe.db.exists("ToDo", {"description": description}))

	def test_cleanup_after_failure_removes_published_rows_without_committing_pending_writes(self):
		published = f"Published cleanup fixture {frappe.generate_hash(length=8)}"
		pending = f"Pending cleanup fixture {frappe.generate_hash(length=8)}"

		class FailingCase(TestCase):
			def runTest(self):
				for description in (published, pending):
					self.addCleanup(
						cleanup_committed_test_data, frappe.db.delete, "ToDo", {"description": description}
					)

				frappe.get_doc(doctype="ToDo", description=published).insert()
				frappe.db.commit()  # nosemgrep
				frappe.get_doc(doctype="ToDo", description=pending).insert()
				self.fail("Failure after publishing the fixture")

		result = FailingCase().run(TestResult())

		self.assertFalse(result.errors)
		self.assertEqual(len(result.failures), 1)
		with self.secondary_connection():
			for description in (published, pending):
				self.assertFalse(frappe.db.exists("ToDo", {"description": description}))

	def test_committed_baseline_can_be_rebuilt_and_survives_test_rollback(self):
		description = f"Shared baseline {frappe.generate_hash(length=8)}"
		fixture = {"doctype": "ToDo", "description": description}
		self.addCleanup(cleanup_committed_test_data, frappe.db.delete, "ToDo", {"description": description})
		directory = self.enterContext(TemporaryDirectory())
		manager = generators.TestRecordManager()
		manager.log_file = Path(directory) / "test-records.jsonl"
		self.enterContext(patch.object(generators, "test_record_manager_instance", manager))
		self.enterContext(patch.object(frappe.tests, "global_test_dependencies", ["ToDo"]))
		self.enterContext(patch.object(frappe.local, "test_objects", defaultdict(list)))

		def load_fixture(doctype):
			generators.make_test_objects(doctype, [fixture])

		def fail_after_insert(doctype):
			load_fixture(doctype)
			raise RuntimeError("Baseline setup failed")

		with (
			patch.object(generators, "make_test_records", fail_after_insert),
			self.assertRaisesRegex(RuntimeError, "Baseline setup failed"),
		):
			generators.make_test_baseline("frappe")
		self.assertFalse(frappe.db.exists("ToDo", {"description": description}))
		self.assertFalse(manager.log_file.exists())

		actors = [DESK_USER, SYSTEM_MANAGER, WEBSITE_MANAGER, WORKSPACE_MANAGER]

		def actor_creation_times():
			return frappe.db.get_all(
				"User", filters={"name": ["in", actors]}, fields=["name", "creation"], order_by="name"
			)

		with patch.object(generators, "make_test_records", load_fixture):
			generators.make_test_baseline("frappe")
			journal = manager.log_file.read_text()
			baseline_users = actor_creation_times()
			fixture_name = frappe.db.get_value("ToDo", {"description": description})
			frappe.local.test_objects.clear()
			manager._log = None
			generators.make_test_baseline("frappe")
		self.assertEqual(manager.log_file.read_text(), journal)
		self.assertCountEqual((user.name for user in baseline_users), actors)
		self.assertEqual(actor_creation_times(), baseline_users)
		self.assertEqual(frappe.db.count("ToDo", {"description": description}), 1)

		class BaselineCase(IntegrationTestCase):
			def runTest(self):
				frappe.db.set_value("User", DESK_USER, "enabled", 0)
				frappe.db.delete("ToDo", {"name": fixture_name})
				frappe.get_doc(doctype="ToDo", description=description).insert()

		with self.set_user("test@example.com"):
			result = BaselineCase().run(TestResult())
		self.assertTrue(result.wasSuccessful(), result.errors + result.failures)
		with self.secondary_connection():
			self.assertTrue(frappe.db.get_value("User", DESK_USER, "enabled"))
			self.assertEqual(frappe.db.get_value("ToDo", fixture_name, "description"), description)
			self.assertEqual(frappe.db.count("ToDo", {"description": description}), 1)

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
