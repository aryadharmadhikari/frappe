# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: MIT. See LICENSE
import json

import frappe
from frappe.core.doctype.doctype.doctype import clear_permissions_cache
from frappe.core.doctype.doctype.test_doctype import insert_test_doctype, new_doctype
from frappe.permissions import add_permission, reset_perms
from frappe.tests import IntegrationTestCase
from frappe.website.doctype.web_form.web_form import (
	accept,
	get_form_data,
	get_table_multiselect_fields,
	has_link_option,
)


class TestWebFormTableMultiSelect(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		frappe.local.request = None
		suffix = frappe.generate_hash(length=8)
		self.target = f"Test WF MultiSelect Target {suffix}"
		self.row = f"Test WF MultiSelect Row {suffix}"
		self.parent = f"Test WF MultiSelect Parent {suffix}"
		self.numbered = f"Test WF MultiSelect Numbered {suffix}"
		self.numbered_row = f"Test WF MultiSelect Numbered Row {suffix}"
		self.untitled = f"Test WF MultiSelect Untitled {suffix}"
		self.untitled_row = f"Test WF MultiSelect Untitled Row {suffix}"

		insert_test_doctype(
			self,
			new_doctype(
				self.target,
				fields=[{"fieldname": "title", "fieldtype": "Data", "label": "Title", "unique": 1}],
				autoname="field:title",
			),
		)
		insert_test_doctype(
			self,
			new_doctype(
				self.row,
				istable=1,
				permissions=[],
				fields=[
					{"fieldname": "target", "fieldtype": "Link", "label": "Target", "options": self.target}
				],
			),
		)
		# integer names shown by title: the branch that ships raw JSON
		insert_test_doctype(
			self,
			new_doctype(
				self.numbered,
				fields=[{"fieldname": "title", "fieldtype": "Data", "label": "Title"}],
				autoname="autoincrement",
				title_field="title",
				show_title_field_in_link=1,
			),
		)
		insert_test_doctype(
			self,
			new_doctype(
				self.numbered_row,
				istable=1,
				permissions=[],
				fields=[
					{"fieldname": "target", "fieldtype": "Link", "label": "Target", "options": self.numbered}
				],
			),
		)
		# integer names with no title field: the branch that ships a joined string
		insert_test_doctype(
			self,
			new_doctype(
				self.untitled,
				fields=[{"fieldname": "note", "fieldtype": "Data", "label": "Note"}],
				autoname="autoincrement",
			),
		)
		insert_test_doctype(
			self,
			new_doctype(
				self.untitled_row,
				istable=1,
				permissions=[],
				fields=[
					{"fieldname": "target", "fieldtype": "Link", "label": "Target", "options": self.untitled}
				],
			),
		)
		insert_test_doctype(
			self,
			new_doctype(
				self.parent,
				fields=[
					{"fieldname": "title", "fieldtype": "Data", "label": "Title"},
					{
						"fieldname": "targets",
						"fieldtype": "Table MultiSelect",
						"label": "Targets",
						"options": self.row,
					},
					{
						"fieldname": "numbers",
						"fieldtype": "Table MultiSelect",
						"label": "Numbers",
						"options": self.numbered_row,
					},
					{
						"fieldname": "untitled",
						"fieldtype": "Table MultiSelect",
						"label": "Untitled",
						"options": self.untitled_row,
					},
				],
			),
		)

		for title in ("Alpha", "Beta", "Gamma"):
			frappe.get_doc(doctype=self.target, title=title).insert(ignore_if_duplicate=True)

		self.numbered_names = [
			str(frappe.get_doc(doctype=self.numbered, title=title).insert().name) for title in ("One", "Two")
		]
		self.untitled_names = [
			str(frappe.get_doc(doctype=self.untitled, note=note).insert().name)
			for note in ("First", "Second")
		]

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.local.request = None
		super().tearDown()

	def test_form_data_ships_the_child_link_docfield(self):
		"""The portal has no DocType meta, so the control needs the child's Link field as-is."""
		web_form = self.make_web_form()
		frappe.set_user("Guest")

		out = get_form_data(doctype=self.parent, web_form_name=web_form.name)

		field = next(f for f in out.web_form.web_form_fields if f.fieldname == "targets")
		self.assertEqual(
			[(df["fieldname"], df["fieldtype"], df["options"]) for df in field.fields],
			[("target", "Link", self.target)],
		)
		self.assertIn("Alpha", self.link_options(web_form))

	def test_missing_child_doctype_does_not_break_the_form(self):
		self.assertEqual(get_table_multiselect_fields("Test WF No Such Row"), [])

	def test_has_link_option_descends_into_table_multiselect(self):
		fields = self.make_web_form().web_form_fields

		self.assertTrue(has_link_option(fields, self.target))
		self.assertFalse(has_link_option(fields, "User"))

	def test_portal_page_ships_the_child_link_docfield(self):
		"""The page render builds its own fields, apart from get_form_data."""
		web_form = self.make_web_form()
		frappe.set_user("Guest")
		frappe.local.path = f"{web_form.route}/new"
		frappe.local.form_dict = frappe._dict(is_new=1)

		context = frappe._dict()
		web_form.get_context(context)

		field = next(f for f in context.web_form_doc.web_form_fields if f.fieldname == "targets")
		self.assertEqual([df["fieldtype"] for df in field.fields], ["Link"])
		self.assertLessEqual({"Alpha", "Beta", "Gamma"}, set(field.fields[0]["link_options"].split("\n")))

	def test_key_required_form_needs_guest_read_on_the_link_doctype(self):
		"""The options are built with permissions ignored, so the form is the only allowlist."""
		web_form = self.make_web_form(key_required=1)
		key = self.make_web_form_request(web_form).key
		frappe.set_user("Guest")

		with self.assertRaises(frappe.PermissionError):
			self.link_options(web_form, web_form_request_key=key)

		frappe.set_user("Administrator")
		self.allow_guest_read(self.target)
		frappe.set_user("Guest")

		self.assertIn("Alpha", self.link_options(web_form, web_form_request_key=key))

	def test_login_required_options_show_own_rows_unless_the_field_opts_out(self):
		user = self.make_website_user()
		own = frappe.get_doc(doctype=self.target, title="Owned By TMS Website User").insert()
		self.addCleanup(frappe.delete_doc, self.target, own.name, force=True)
		frappe.db.set_value(self.target, own.name, "owner", user, update_modified=False)

		scoped = self.make_web_form(login_required=1)
		opted_out = self.make_web_form(login_required=1, field_settings={"allow_read_on_all_link_options": 1})
		frappe.set_user(user)

		self.assertEqual(self.link_options(scoped), [own.name])
		self.assertLessEqual({"Alpha", own.name}, set(self.link_options(opted_out)))

	def test_guest_submission_still_validates_links_on_the_server(self):
		"""The client skips validate_link_and_fetch for guests, so the save must catch bad links."""
		web_form = self.make_web_form()
		frappe.set_user("Guest")

		with self.assertRaises(frappe.LinkValidationError):
			self.submit(web_form, [{"target": "Not A Real Target"}])

		doc = self.submit(web_form, [{"target": "Alpha"}])
		self.assertEqual([row.target for row in doc.targets], ["Alpha"])

	def test_required_table_multiselect_rejects_an_empty_list(self):
		web_form = self.make_web_form(field_settings={"reqd": 1})
		frappe.set_user("Guest")

		with self.assertRaises(frappe.ValidationError):
			self.submit(web_form, [])

	def test_link_options_are_strings_for_an_autoincrement_doctype(self):
		"""The control matches with value.toLowerCase(), so an integer name throws on a keystroke."""
		web_form = self.make_web_form(link_field="numbers", options=self.numbered_row)
		frappe.set_user("Guest")

		options = self.link_options(web_form, fieldname="numbers", raw=True)

		self.assertEqual(sorted(row["value"] for row in options), sorted(self.numbered_names))
		for row in options:
			self.assertIsInstance(row["value"], str)

	def test_link_options_join_for_an_autoincrement_doctype_without_a_title_field(self):
		"""Without a title field the options ship as one joined string, which integers break."""
		web_form = self.make_web_form(link_field="untitled", options=self.untitled_row)
		frappe.set_user("Guest")

		options = self.link_options(web_form, fieldname="untitled", raw=True)

		# equality, not a subset: it pins the count, so an empty join cannot pass
		self.assertEqual(sorted(options), sorted(self.untitled_names))

	def make_web_form(self, field_settings=None, link_field="targets", options=None, **settings):
		"""Unique names keep get_cached_doc from serving a stale form."""
		suffix = frappe.generate_hash(length=8)
		web_form = frappe.get_doc(
			{
				"doctype": "Web Form",
				"title": f"_Test TMS Web Form {suffix}",
				"route": f"test-tms-web-form-{suffix}",
				"doc_type": self.parent,
				"module": "Website",
				"published": 1,
				"login_required": 0,
				"web_form_fields": [
					{"fieldname": "title", "fieldtype": "Data", "label": "Title"},
					{
						"fieldname": link_field,
						"fieldtype": "Table MultiSelect",
						"label": link_field.title(),
						"options": options or self.row,
						**(field_settings or {}),
					},
				],
				**settings,
			}
		).insert(ignore_permissions=True)

		self.addCleanup(frappe.delete_doc, "Web Form", web_form.name, force=True, ignore_permissions=True)
		return web_form

	def make_web_form_request(self, web_form):
		return frappe.get_doc(
			{
				"doctype": "Web Form Request",
				"web_form": web_form.name,
				"web_form_values": json.dumps({}),
				"doc_values": json.dumps({}),
			}
		).insert(ignore_permissions=True)

	def make_website_user(self):
		return (
			frappe.get_doc(
				doctype="User",
				email=f"tms-{frappe.generate_hash()}@example.com",
				first_name="TMS",
				user_type="Website User",
				send_welcome_email=0,
			)
			.insert()
			.name
		)

	def allow_guest_read(self, doctype):
		add_permission(doctype, "Guest", ptype="read")
		clear_permissions_cache(doctype)
		self.addCleanup(lambda: (reset_perms(doctype), clear_permissions_cache(doctype)))

	def link_options(self, web_form, fieldname="targets", raw=False, **kwargs):
		"""The options the portal control filters in the browser, as the page ships them."""
		out = get_form_data(doctype=self.parent, web_form_name=web_form.name, **kwargs)
		field = next(f for f in out.web_form.web_form_fields if f.fieldname == fieldname)
		options = field.fields[0]["link_options"]
		if isinstance(options, str):
			options = options.split("\n") if options[0] != "[" else json.loads(options)
		if raw:
			return options
		return sorted(row["value"] if isinstance(row, dict) else row for row in options)

	def submit(self, web_form, targets):
		return accept(
			web_form=web_form.name,
			data=json.dumps({"doctype": self.parent, "title": "_Test TMS Submission", "targets": targets}),
		)
