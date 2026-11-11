# Copyright (c) 2022, Frappe Technologies Pvt. Ltd. and Contributors
# License: MIT. See LICENSE

import base64
import json
from types import SimpleNamespace

import requests

import frappe
from frappe.frappeclient import FrappeClient, FrappeException
from frappe.model import default_fields
from frappe.tests import IntegrationTestCase, UnitTestCase
from frappe.tests.utils import cleanup_committed_test_data
from frappe.tests.utils.test_capabilities import TestService, requires_test_service
from frappe.utils.data import get_url


@requires_test_service(TestService.WEB_SERVER)
class TestFrappeClient(IntegrationTestCase):
	PASSWORD = frappe.conf.admin_password or "admin"

	def setUp(self):
		super().setUp()
		self.prefix = f"FrappeClient {frappe.generate_hash(length=10)} "
		self.addCleanup(cleanup_committed_test_data, self.delete_notes)

	def title(self, value):
		return self.prefix + value

	def delete_notes(self):
		for name in frappe.get_all("Note", {"title": ("like", self.prefix + "%")}, pluck="name"):
			frappe.delete_doc("Note", name, force=True)

	def test_insert_many(self):
		server = FrappeClient(get_url(), "Administrator", self.PASSWORD, verify=False)
		server.insert_many(
			[
				{"doctype": "Note", "title": self.title("Sing")},
				{"doctype": "Note", "title": self.title("a")},
				{"doctype": "Note", "title": self.title("song")},
				{"doctype": "Note", "title": self.title("of")},
				{"doctype": "Note", "title": self.title("sixpence")},
			]
		)
		records = server.get_list("Note", fields=["title"], filters={"title": ("like", self.prefix + "%")})
		records = [r.get("title") for r in records]

		self.assertIn(self.title("Sing"), records)
		self.assertIn(self.title("a"), records)
		self.assertIn(self.title("song"), records)
		self.assertIn(self.title("of"), records)
		self.assertIn(self.title("sixpence"), records)

	def test_create_doc(self):
		server = FrappeClient(get_url(), "Administrator", self.PASSWORD, verify=False)
		response = server.insert({"doctype": "Note", "title": self.title("test_create")})

		for field in default_fields:
			self.assertIn(field, response)

		self.assertEqual(response.get("doctype"), "Note")
		self.assertEqual(response.get("title"), self.title("test_create"))

	def test_list_docs(self):
		server = FrappeClient(get_url(), "Administrator", self.PASSWORD, verify=False)
		note = server.insert({"doctype": "Note", "title": self.title("Test list docs")})
		doc_list = server.get_list("Note", filters={"name": note["name"]})

		self.assertEqual([doc["name"] for doc in doc_list], [note["name"]])

	def test_list_summary(self):
		server = FrappeClient(get_url(), "Administrator", self.PASSWORD, verify=False)
		server.insert_many(
			[
				{"doctype": "Note", "title": self.title("Sing")},
				{"doctype": "Note", "title": self.title("a")},
				{"doctype": "Note", "title": self.title("song")},
				{"doctype": "Note", "title": self.title("of")},
				{"doctype": "Note", "title": self.title("sixpence")},
			]
		)
		notes = server.get_list(
			"Note", fields=["title"], filters={"title": ("like", self.prefix + "%")}, order_by="creation desc"
		)

		notes = [d.get("title") for d in notes]
		self.assertEqual(notes[0], self.title("sixpence"))

		getlist_users = server.get_list(
			"User",
			fields=[{"COUNT": "name", "as": "user_count"}],
			filters={"user_type": "System User"},
			group_by="user_type",
		)
		getall_users = frappe.db.get_all(
			"User",
			fields=[{"COUNT": "name", "as": "system_user_count"}],
			filters={"user_type": "System User"},
			group_by="user_type",
		)
		self.assertEqual(getlist_users[0]["user_count"], getall_users[0]["system_user_count"])

	def test_get_doc(self):
		USER = "Administrator"
		TITLE = self.title("get_this")
		DOCTYPE = "Note"
		server = FrappeClient(get_url(), "Administrator", self.PASSWORD, verify=False)

		NAME = server.insert({"doctype": DOCTYPE, "title": TITLE}).get("name")
		doc = server.get_doc(DOCTYPE, NAME)

		for field in default_fields:
			self.assertIn(field, doc)

		self.assertEqual(doc.get("doctype"), DOCTYPE)
		self.assertEqual(doc.get("name"), NAME)
		self.assertEqual(doc.get("title"), TITLE)
		self.assertEqual(doc.get("owner"), USER)

	def test_get_value_by_filters(self):
		CONTENT = "test get value"
		server = FrappeClient(get_url(), "Administrator", self.PASSWORD, verify=False)
		server.insert({"doctype": "Note", "title": self.title("get_value"), "content": CONTENT}).get("name")

		self.assertEqual(
			server.get_value("Note", "content", {"title": self.title("get_value")}).get("content"), CONTENT
		)

	def test_get_value_by_name(self):
		server = FrappeClient(get_url(), "Administrator", self.PASSWORD, verify=False)
		CONTENT = "test get value"
		NAME = server.insert({"doctype": "Note", "title": self.title("get_value"), "content": CONTENT}).get(
			"name"
		)

		self.assertEqual(server.get_value("Note", "content", NAME).get("content"), CONTENT)

	def test_get_value_with_malicious_query(self):
		server = FrappeClient(get_url(), "Administrator", self.PASSWORD, verify=False)
		server.insert({"doctype": "Note", "title": self.title("get_value")})

		self.assertRaises(
			FrappeException,
			server.get_value,
			"Note",
			"(select (password) from(__Auth) order by name desc limit 1)",
			{"title": self.title("get_value")},
		)

	def test_get_single(self):
		server = FrappeClient(get_url(), "Administrator", self.PASSWORD, verify=False)
		original = frappe.db.get_single_value("Website Settings", "title_prefix")
		self.addCleanup(
			cleanup_committed_test_data,
			frappe.db.set_single_value,
			"Website Settings",
			"title_prefix",
			original,
		)
		server.set_value("Website Settings", "Website Settings", "title_prefix", "test-prefix")
		self.assertEqual(
			server.get_value("Website Settings", "title_prefix", "Website Settings").get("title_prefix"),
			"test-prefix",
		)
		self.assertEqual(
			server.get_value("Website Settings", "title_prefix").get("title_prefix"), "test-prefix"
		)

	def test_update_doc(self):
		server = FrappeClient(get_url(), "Administrator", self.PASSWORD, verify=False)
		resp = server.insert({"doctype": "Note", "title": self.title("Sing")})
		doc = server.get_doc("Note", resp.get("name"))

		CONTENT = "<h1>Hello, World!</h1>"
		doc["content"] = CONTENT
		doc = server.update(doc)
		self.assertTrue(doc["content"] == CONTENT)

	def test_update_child_doc(self):
		server = FrappeClient(get_url(), "Administrator", self.PASSWORD, verify=False)
		self.addCleanup(cleanup_committed_test_data, self.delete_event_and_contacts)
		contacts = server.insert_many(
			[
				{"doctype": "Contact", "first_name": self.title("George"), "last_name": "Steevens"},
				{"doctype": "Contact", "first_name": self.title("William"), "last_name": "Shakespeare"},
			]
		)

		# create an event with one of the created contacts
		event = server.insert(
			{
				"doctype": "Event",
				"subject": self.title("Event"),
				"event_participants": [{"reference_doctype": "Contact", "reference_docname": contacts[0]}],
			}
		)

		# update the event's contact to the second contact
		server.update(
			{
				"doctype": "Event Participants",
				"name": event.get("event_participants")[0].get("name"),
				"reference_docname": contacts[1],
			}
		)

		# the change should run the parent document's validations and
		# create a Communication record with the new contact
		self.assertTrue(frappe.db.exists("Communication Link", {"link_name": contacts[1]}))

	def delete_event_and_contacts(self):
		for name in frappe.get_all("Event", {"subject": self.title("Event")}, pluck="name"):
			for communication in frappe.get_all(
				"Communication", {"reference_doctype": "Event", "reference_name": name}, pluck="name"
			):
				frappe.delete_doc("Communication", communication, force=True)
			frappe.delete_doc("Event", name, force=True)
		for name in frappe.get_all("Contact", {"first_name": ("like", self.prefix + "%")}, pluck="name"):
			frappe.delete_doc("Contact", name, force=True)

	def test_delete_doc(self):
		server = FrappeClient(get_url(), "Administrator", self.PASSWORD, verify=False)
		NAME_TO_DELETE = server.insert({"doctype": "Note", "title": self.title("Sing")}).get("name")
		server.delete("Note", NAME_TO_DELETE)
		self.assertFalse(frappe.db.get_value("Note", NAME_TO_DELETE))

	def test_auth_via_api_key_secret(self):
		# Generate and read the credentials through the web process that will
		# authenticate them. This avoids cross-process SQLite snapshot races.
		server = FrappeClient(get_url(), "Administrator", self.PASSWORD, verify=False)
		user = f"frappeclient-{frappe.generate_hash(length=10)}@example.com"
		self.addCleanup(cleanup_committed_test_data, frappe.delete_doc_if_exists, "User", user)
		server.insert(
			{"doctype": "User", "email": user, "first_name": "FrappeClient", "send_welcome_email": 0}
		)
		keys = server.post_api("frappe.core.doctype.user.user.generate_keys", {"user": user})
		api_key = keys["api_key"]
		generated_secret = keys["api_secret"]
		header = {"Authorization": f"token {api_key}:{generated_secret}"}
		with requests.post(
			get_url() + "/api/method/frappe.auth.get_logged_user", headers=header, timeout=30
		) as response:
			self.assertEqual(response.status_code, 200, response.text)
			self.assertEqual(user, response.json()["message"])
		self.assertEqual(keys["api_secret"], generated_secret)

		header = {
			"Authorization": "Basic {}".format(
				base64.b64encode(frappe.safe_encode(f"{api_key}:{generated_secret}")).decode()
			)
		}
		with requests.post(
			get_url() + "/api/method/frappe.auth.get_logged_user", headers=header, timeout=30
		) as response:
			self.assertEqual(response.status_code, 200, response.text)
			self.assertEqual(user, response.json()["message"])

		# Valid api key, invalid api secret
		api_secret = "ksk&93nxoe3os"
		header = {"Authorization": f"token {api_key}:{api_secret}"}
		with requests.post(
			get_url() + "/api/method/frappe.auth.get_logged_user", headers=header, timeout=30
		) as response:
			self.assertEqual(response.status_code, 401)

		# random api key and api secret
		api_key = "@3djdk3kld"
		api_secret = "ksk&93nxoe3os"
		header = {"Authorization": f"token {api_key}:{api_secret}"}
		with requests.post(
			get_url() + "/api/method/frappe.auth.get_logged_user", headers=header, timeout=30
		) as response:
			self.assertEqual(response.status_code, 401)


class TestFrappeClientErrors(UnitTestCase):
	def get_raised_message(self, payload):
		client = FrappeClient.__new__(FrappeClient)
		with self.assertRaises(FrappeException) as raised:
			client.post_process(SimpleNamespace(json=lambda: payload))

		return str(raised.exception)

	def test_error_outside_developer_mode(self):
		message = self.get_raised_message(
			{
				"exc_type": "PermissionError",
				"_server_messages": json.dumps([json.dumps({"message": "No permission for <b>Note</b>"})]),
			}
		)

		self.assertIn("PermissionError", message)
		self.assertIn("No permission for Note", message)

	def test_error_with_traceback(self):
		message = self.get_raised_message(
			{
				"exc_type": "ValidationError",
				"exc": json.dumps(["Traceback (most recent call last):\nValidationError: Title is required"]),
			}
		)

		self.assertIn("ValidationError: Title is required", message)

	def test_error_from_api_v2(self):
		message = self.get_raised_message(
			{"errors": [{"type": "DoesNotExistError", "message": "Note <b>get_this</b> not found"}]}
		)

		self.assertIn("DoesNotExistError", message)
		self.assertIn("Note get_this not found", message)
