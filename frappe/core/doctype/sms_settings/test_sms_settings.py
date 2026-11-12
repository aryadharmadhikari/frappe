# Copyright (c) 2017, Frappe Technologies and Contributors
# License: MIT. See LICENSE
import frappe
from frappe.core.doctype.sms_settings.sms_settings import check_sms_permission
from frappe.tests import IntegrationTestCase
from frappe.tests.baseline import DESK_USER, SYSTEM_MANAGER


class TestSMSSettings(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		self.sms_settings = frappe.get_single("SMS Settings")
		self.sms_settings.set("allowed_roles", [])
		self.sms_settings.flags.ignore_mandatory = True
		self.sms_settings.save()

	def test_user_without_allowed_role_is_blocked(self):
		with self.set_user(DESK_USER):
			self.assertRaises(frappe.PermissionError, check_sms_permission)

	def test_system_manager_is_always_allowed(self):
		with self.set_user(SYSTEM_MANAGER):
			check_sms_permission()

	def test_user_with_configured_role_is_allowed(self):
		self.sms_settings.append("allowed_roles", {"role": "Desk User"})
		self.sms_settings.save()

		with self.set_user(DESK_USER):
			check_sms_permission()
