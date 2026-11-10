import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from rq import Queue

import frappe
from frappe.testing.discovery import TestRunnerError
from frappe.testing.environment import check_test_site_isolation, quarantine_test_site
from frappe.testing.suite import TestSuite
from frappe.tests import IntegrationTestCase
from frappe.tests.classes.transaction_guard import TransactionBoundaryError
from frappe.utils.logging import get_log_db


class TestTransactionIsolation(IntegrationTestCase):
	def test_reset_site_allows_a_commit_and_discards_the_database(self):
		baseline = frappe.local.db
		description = frappe.generate_hash()
		paths = []

		class ResetCase(IntegrationTestCase):
			ISOLATION_MODE = "reset"
			ISOLATION_REASON = "Verify that a second connection can see a committed document."

			def setUp(self):
				paths.append(Path(frappe.local.site_path).resolve())
				self.assertIsNot(frappe.local.db, baseline)

			def test_commit(self):
				todo = frappe.get_doc(doctype="ToDo", description=description).insert()
				callbacks = []
				frappe.db.after_commit.add(lambda: callbacks.append(todo.name))
				frappe.db.commit()
				self.assertEqual(callbacks, [todo.name])
				primary = frappe.local.db
				with self.secondary_connection():
					self.assertIsNot(frappe.local.db, primary)
					self.assertEqual(frappe.db.get_value("ToDo", todo.name, "description"), description)
				self.assertIs(frappe.local.db, primary)

		result = TestSuite([ResetCase("test_commit")]).run(unittest.TestResult())
		self.assertTrue(result.wasSuccessful(), (result.failures, result.errors))
		self.assertIs(frappe.local.db, baseline)
		self.assertFalse(paths[0].exists())
		self.assertFalse(frappe.db.exists("ToDo", {"description": description}))

	def test_failed_site_install_discards_the_partial_site(self):
		baseline = frappe.local.db
		paths = []

		def fail_install(database, site, **kwargs):
			path = Path(site)
			path.mkdir()
			paths.append(path)
			raise RuntimeError("installation failed")

		class ResetCase(IntegrationTestCase):
			ISOLATION_MODE = "reset"
			ISOLATION_REASON = "Exercise a partial site installation."

			def test_never_runs(self):
				self.fail("A failed installation should prevent this test from running")

		with patch("frappe.tests.classes.reset._new_site", fail_install):
			result = TestSuite([ResetCase("test_never_runs")]).run(unittest.TestResult())
		self.assertEqual(len(result.errors), 1)
		self.assertIn("installation failed", result.errors[0][1])
		self.assertIs(frappe.local.db, baseline)
		self.assertFalse(paths[0].exists())

	def test_rejects_transaction_breakers_even_if_caught_by_the_test(self):
		operations = (
			lambda: frappe.db.commit(),
			lambda: frappe.db.rollback(),
			lambda: frappe.db.begin(),
			lambda: frappe.db.sql("commit"),
			lambda: frappe.db.sql("select 1; commit"),
			lambda: frappe.db.sql("set autocommit = 1"),
			lambda: frappe.db.sql("create table forbidden (id integer)"),
			lambda: frappe.db.sql_ddl("create table forbidden (id integer)"),
			lambda: frappe.db._conn.commit(),
			lambda: frappe.db._conn.cursor().execute("rollback"),
			lambda: frappe.connect(),
			lambda: sqlite3.connect(":memory:"),
			lambda: Queue(connection=frappe.cache).enqueue_job(object()),
		)

		class RollbackCase(IntegrationTestCase):
			ISOLATION_MODE = "rollback"

			def test_operation(self):
				with self.assertRaises(TransactionBoundaryError):
					self.operation()

		for index, operation in enumerate(operations):
			with self.subTest(operation=index):
				case = RollbackCase("test_operation")
				case.operation = operation
				result = case.run()
				self.assertEqual(len(result.failures), 1, result.errors)
				self.assertIn("is forbidden inside a rollback test", result.failures[0][1])
				self.assertFalse(result.shouldStop)

	def test_savepoint_preserves_class_fixtures_and_callbacks(self):
		description = frappe.generate_hash()
		calls = []

		class SavepointCase(IntegrationTestCase):
			ISOLATION_MODE = "savepoint"
			ISOLATION_REASON = "Exercise the lifecycle of shared fixtures."

			@classmethod
			def setUpClass(cls):
				super().setUpClass()
				cls.name = frappe.get_doc(doctype="ToDo", description=description).insert().name
				frappe.db.after_rollback.add(lambda: calls.append("class rollback"))

			def test_change_fixture(self):
				frappe.db.savepoint("owned_by_test")
				frappe.db.set_value("ToDo", self.name, "description", "nested write")
				frappe.db.rollback(save_point="owned_by_test")
				frappe.db.release_savepoint("owned_by_test")
				self.assertEqual(frappe.db.get_value("ToDo", self.name, "description"), description)
				frappe.db.set_value("ToDo", self.name, "description", "test write")
				frappe.db.after_rollback.add(self.write_after_rollback)
				frappe.db.after_commit.add(lambda: calls.append("commit"))

			def write_after_rollback(self):
				calls.append("test rollback")
				frappe.db.set_value("ToDo", self.name, "description", "cleanup write")

			def test_baseline(self):
				self.assertEqual(frappe.db.get_value("ToDo", self.name, "description"), description)
				self.assertEqual(calls, ["test rollback"])

		result = TestSuite([SavepointCase("test_change_fixture"), SavepointCase("test_baseline")]).run(
			unittest.TestResult()
		)
		self.assertTrue(result.wasSuccessful(), (result.failures, result.errors))
		self.assertEqual(calls, ["test rollback", "class rollback"])
		self.assertFalse(frappe.db.exists("ToDo", {"description": description}))

	def test_guarded_rollback_contains_bulk_inserts(self):
		name = frappe.generate_hash(length=10)

		class RollbackCase(IntegrationTestCase):
			ISOLATION_MODE = "rollback"

			def test_insert(self):
				frappe.db.bulk_insert("ToDo", ("name", "description"), [(name, "bulk fixture")])
				self.assertTrue(frappe.db.exists("ToDo", name))

		result = RollbackCase("test_insert").run()
		self.assertTrue(result.wasSuccessful(), (result.failures, result.errors))
		self.assertFalse(frappe.db.exists("ToDo", name))

	def test_failed_class_setup_rolls_back_shared_fixtures(self):
		description = frappe.generate_hash()

		class SavepointCase(IntegrationTestCase):
			ISOLATION_MODE = "savepoint"
			ISOLATION_REASON = "Exercise partial class setup."

			@classmethod
			def setUpClass(cls):
				super().setUpClass()
				frappe.get_doc(doctype="ToDo", description=description).insert()
				raise RuntimeError("failed class setup")

			def test_never_runs(self):
				self.fail("Setup should prevent this test from running")

		result = TestSuite([SavepointCase("test_never_runs")]).run(unittest.TestResult())
		self.assertEqual(result.testsRun, 0)
		self.assertEqual(len(result.errors), 1)
		self.assertIn("failed class setup", result.errors[0][1])
		self.assertFalse(frappe.db.exists("ToDo", {"description": description}))

	def test_cleanup_failure_stops_the_suite_and_keeps_the_original_failure(self):
		class RollbackCase(IntegrationTestCase):
			ISOLATION_MODE = "rollback"

			def test_fails(self):
				self.addCleanup(self.fail_cleanup)
				self.fail("test failure")

			def fail_cleanup(self):
				raise RuntimeError("cleanup failure")

			def test_never_runs(self):
				self.fail("Cleanup should prevent this test from running")

		with patch.object(frappe.local, "test_isolation_failed", False, create=True):
			result = TestSuite([RollbackCase("test_fails"), RollbackCase("test_never_runs")]).run(
				unittest.TestResult()
			)
			self.assertTrue(frappe.local.test_isolation_failed)
		self.assertEqual(result.testsRun, 1)
		self.assertIn("test failure", result.failures[0][1])
		self.assertIn("cleanup failure", result.errors[0][1])

	def test_quarantined_site_cannot_be_reused_by_another_run(self):
		with TemporaryDirectory() as site_path, patch.object(frappe, "destroy") as destroy:
			check_test_site_isolation(site_path)
			quarantine_test_site(site_path)
			destroy.assert_called_once_with()
			with self.assertRaisesRegex(TestRunnerError, "Rebuild it before reuse"):
				check_test_site_isolation(site_path)

	def test_log_database_has_a_separate_baseline(self):
		class RollbackCase(IntegrationTestCase):
			ISOLATION_MODE = "rollback"

			def test_log_write(self):
				db = get_log_db()
				db.sql("CREATE TABLE test_log_isolation (id INTEGER)")
				db.sql("INSERT INTO test_log_isolation VALUES (1)")
				self.assertEqual(db.sql("SELECT id FROM test_log_isolation", pluck=True), [1])

			def test_log_baseline(self):
				self.assertFalse(get_log_db().table_exists("test_log_isolation"))

		result = TestSuite([RollbackCase("test_log_write"), RollbackCase("test_log_baseline")]).run(
			unittest.TestResult()
		)
		self.assertTrue(result.wasSuccessful(), (result.failures, result.errors))

	def test_missing_runner_savepoint_quarantines_the_environment(self):
		class RollbackCase(IntegrationTestCase):
			ISOLATION_MODE = "rollback"

			def test_break_boundary(self):
				guard = self._transaction_guard
				marker = next(iter(guard.savepoints))
				cursor = guard.connection._connection.cursor()
				try:
					cursor.execute(f"RELEASE SAVEPOINT {marker}")
				finally:
					cursor.close()

		with patch.object(frappe.local, "test_isolation_failed", False, create=True):
			result = TestSuite([RollbackCase("test_break_boundary")]).run(unittest.TestResult())
			self.assertTrue(frappe.local.test_isolation_failed)
		self.assertTrue(result.shouldStop)
		self.assertIn("SAVEPOINT", result.errors[0][1].upper())
