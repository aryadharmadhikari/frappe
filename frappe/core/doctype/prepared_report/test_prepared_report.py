# Copyright (c) 2018, Frappe Technologies and Contributors
# License: MIT. See LICENSE
import json
import time
import unittest

import frappe
from frappe.core.doctype.prepared_report.prepared_report import delete_prepared_reports
from frappe.desk.query_report import generate_report_result, get_prepared_report_result, get_report_doc
from frappe.tests import IntegrationTestCase, timeout
from frappe.tests.utils import cleanup_committed_test_data, wait_for_job
from frappe.tests.utils.test_capabilities import TestService, requires_test_service


@requires_test_service(TestService.BACKGROUND_WORKER)
class TestPreparedReport(IntegrationTestCase):
	@timeout(seconds=20)
	def wait_for_status(self, report, status):
		while True:
			frappe.db.rollback()  # read new data
			report.reload()
			if report.status == status:
				break
			time.sleep(0.5)

	def create_prepared_report(self, report=None, commit=True):
		doc = frappe.get_doc(
			{
				"doctype": "Prepared Report",
				"report_name": report or "Database Storage Usage By Tables",
			}
		).insert()

		if commit:
			self.addCleanup(self.delete_published_report, doc.name)
			frappe.db.commit()  # nosemgrep

		return doc

	def delete_published_report(self, name):
		frappe.db.rollback()
		filters = {"ref_doctype": "Prepared Report", "ref_docname": name}
		tasks = frappe.get_all("Background Task", filters=filters, fields=["name", "job_id"])
		for task in tasks:
			if task.job_id:
				wait_for_job(task.job_id, wait_timeout=20)
		cleanup_committed_test_data(self.delete_report_and_tasks, name, tasks)

	@staticmethod
	def delete_report_and_tasks(name, tasks):
		for task in tasks:
			frappe.delete_doc("Background Task", task.name, delete_permanently=True)
		frappe.delete_doc("Prepared Report", name, force=True, delete_permanently=True)

	def create_report(self, **fields):
		report = frappe.new_doc("Report", report_name=frappe.generate_hash(), ref_doctype="ToDo", **fields)
		self.addCleanup(
			cleanup_committed_test_data,
			frappe.delete_doc_if_exists,
			"Report",
			report.report_name,
			delete_permanently=True,
		)
		return report.insert()

	def test_queueing(self):
		doc = self.create_prepared_report()
		self.assertEqual("Queued", doc.status)
		self.assertTrue(doc.queued_at)

		self.wait_for_status(doc, "Completed")

		self.assertTrue(doc.job_id)
		self.assertTrue(doc.report_end_time)

	def test_prepared_data(self):
		doc = self.create_prepared_report()
		self.wait_for_status(doc, "Completed")

		prepared_data = json.loads(doc.get_prepared_data().decode("utf-8"))
		generated_data = generate_report_result(get_report_doc("Database Storage Usage By Tables"))
		self.assertEqual(len(prepared_data["columns"]), len(generated_data["columns"]))
		self.assertEqual(len(prepared_data["result"]), len(generated_data["result"]))
		self.assertEqual(len(prepared_data), len(generated_data))

	def test_failed_report_is_surfaced(self):
		report = self.create_report(report_type="Query Report", query="select * from `tab_no_such_table`")
		doc = self.create_prepared_report(report.name)
		self.wait_for_status(doc, "Error")
		self.assertTrue(doc.error_message)

		result = get_prepared_report_result(get_report_doc(report.name), {}, dn=doc.name)
		self.assertEqual("Error", result["doc"].status)
		# the reader gets the message, the traceback stays on the document
		self.assertIn("tab_no_such_table", result["error"])
		self.assertNotIn("Traceback", result["error"])
		self.assertIsNone(result["doc"].error_message)

	def test_deleting_unreadable_report_raises(self):
		doc = self.create_prepared_report(commit=False)
		self.addCleanup(frappe.set_user, "Administrator")
		frappe.set_user("test1@example.com")
		self.assertRaises(frappe.PermissionError, delete_prepared_reports, [{"name": doc.name}])

	@unittest.skipIf(
		frappe.conf.db_type == "sqlite",
		"This test needs a database sleep function, which SQLite does not provide.",
	)
	def test_start_status_and_kill_jobs(self):
		if frappe.db.db_type == "postgres":
			query = "select pg_sleep(5)"
		elif frappe.db.db_type == "mariadb":
			query = "select sleep(5)"
		report = self.create_report(report_type="Query Report", query=query)
		doc = self.create_prepared_report(report.name)
		self.wait_for_status(doc, "Started")
		job_id = doc.job_id

		doc.delete()
		wait_for_job(job_id)
		job = frappe.get_doc("RQ Job", job_id)
		self.assertEqual(job.status, "stopped")

	@unittest.skipIf(
		frappe.conf.db_type == "sqlite",
		"This test needs a database sleep function, which SQLite does not provide.",
	)
	def test_cancel_from_background_task_sidepanel(self):
		from frappe.core.doctype.background_task.background_task import stop_task

		if frappe.db.db_type == "postgres":
			query = "select pg_sleep(5)"
		elif frappe.db.db_type == "mariadb":
			query = "select sleep(5)"
		report = self.create_report(report_type="Query Report", query=query)
		doc = self.create_prepared_report(report.name)
		self.wait_for_status(doc, "Started")
		bg_task = frappe.get_all(
			"Background Task", {"ref_doctype": "Prepared Report", "ref_docname": doc.name}, pluck="task_id"
		)
		self.assertEqual(len(bg_task), 1)

		stop_task(bg_task[0])

		self.assertEqual(doc.reload().status, "Cancelled")
		wait_for_job(doc.job_id)
		job = frappe.get_doc("RQ Job", doc.job_id)
		self.assertEqual(job.status, "stopped")
