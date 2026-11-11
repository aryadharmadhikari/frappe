# Copyright (c) 2022, Frappe Technologies and Contributors
# See license.txt

import time
import typing

import frappe
from frappe.tests import IntegrationTestCase, timeout
from frappe.tests.utils import cleanup_committed_test_data, wait_for_job
from frappe.tests.utils.test_capabilities import TestService, requires_test_service
from frappe.utils.background_jobs import get_queue

if typing.TYPE_CHECKING:
	from rq.job import Job


@requires_test_service(TestService.BACKGROUND_WORKER)
class TestSubmissionQueue(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.queue = get_queue(qtype="default")

	def setUp(self):
		from frappe.core.doctype.doctype.test_doctype import _remove_test_doctype, new_doctype

		super().setUp()
		doc = new_doctype(is_submittable=True, queue_in_background=True)
		self.addCleanup(cleanup_committed_test_data, _remove_test_doctype, doc)
		self.addCleanup(self.delete_queue_rows, doc.name)
		self.test_doctype = doc.insert().name

	def delete_queue_rows(self, doctype):
		frappe.db.rollback()
		filters = {"ref_doctype": doctype}
		for job_id in frappe.get_all("Submission Queue", filters, pluck="job_id"):
			if job_id:
				wait_for_job(job_id, wait_timeout=20)
		cleanup_committed_test_data(frappe.db.delete, "Submission Queue", filters)

	@timeout(seconds=20)
	def check_status(self, job: "Job", status, wait=True):
		if wait:
			while True:
				if job.is_queued or job.is_started:
					time.sleep(0.2)
				else:
					break
		self.assertEqual(frappe.get_doc("RQ Job", job.id).status, status)

	def test_queue_operation(self):
		from frappe.core.doctype.submission_queue.submission_queue import queue_submission

		d = frappe.new_doc(self.test_doctype)
		d.update({"some_fieldname": "Random"})
		d.insert()

		frappe.db.commit()
		queue_submission(d, "submit")
		with self.assertRaises(frappe.LinkExistsError):
			d.delete()
		frappe.db.commit()

		# Waiting for execution
		time.sleep(4)
		submission_queue = frappe.get_doc(
			"Submission Queue", {"ref_doctype": d.doctype, "ref_docname": d.name}
		)

		# Test queueing / starting
		job = self.queue.fetch_job(submission_queue.job_id)
		# Test completion
		self.check_status(job, status="finished")

		d.reload()
		d.cancel()
		d.delete()
		self.assertFalse(frappe.db.exists("Submission Queue", submission_queue.name))

	def test_force_delete_cleans_up_queued_submission(self):
		d = frappe.get_doc(doctype=self.test_doctype, some_fieldname="Random").insert()
		submission_queue = frappe.get_doc(
			doctype="Submission Queue",
			ref_doctype=d.doctype,
			ref_docname=d.name,
			status="Queued",
		)
		submission_queue.db_insert()

		d.delete(force=True)
		self.assertFalse(frappe.db.exists("Submission Queue", submission_queue.name))

	def test_cancel_operation(self):
		from frappe.core.doctype.submission_queue.submission_queue import queue_submission

		d = frappe.new_doc(self.test_doctype)
		d.update({"some_fieldname": "Random"})
		d.insert()
		d.submit()
		frappe.db.commit()

		self.assertEqual(d.docstatus, 1)

		queue_submission(d, "Cancel")
		frappe.db.commit()

		time.sleep(4)
		submission_queue = frappe.get_doc(
			"Submission Queue", {"ref_doctype": d.doctype, "ref_docname": d.name}
		)

		job = self.queue.fetch_job(submission_queue.job_id)
		self.check_status(job, status="finished")

		d.reload()
		self.assertEqual(d.docstatus, 2)

	def test_cancel_on_cancelled_doc(self):
		from frappe.core.doctype.submission_queue.submission_queue import queue_submission

		d = frappe.new_doc(self.test_doctype)
		d.update({"some_fieldname": "Random"})
		d.insert()
		d.submit()

		existing = frappe.get_doc(
			{
				"doctype": "Submission Queue",
				"ref_doctype": d.doctype,
				"ref_docname": d.name,
				"status": "Queued",
			}
		)
		existing.insert(d, "Cancel")

		initial_count = frappe.db.count(
			"Submission Queue", {"ref_doctype": d.doctype, "ref_docname": d.name, "status": "Queued"}
		)

		queue_submission(d, "Cancel")

		final_count = frappe.db.count(
			"Submission Queue", {"ref_doctype": d.doctype, "ref_docname": d.name, "status": "Queued"}
		)

		self.assertEqual(initial_count, final_count)

	def test_latest_submission_requires_read_permission(self):
		from frappe.core.doctype.submission_queue.submission_queue import get_latest_submissions

		d = frappe.get_doc(doctype=self.test_doctype, some_fieldname="Random").insert()

		self.addCleanup(frappe.set_user, "Administrator")
		frappe.set_user("test1@example.com")
		with self.assertRaises(frappe.PermissionError):
			get_latest_submissions(d.doctype, d.name)
