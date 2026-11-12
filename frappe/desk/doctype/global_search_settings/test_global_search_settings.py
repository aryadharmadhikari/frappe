# Copyright (c) 2026, Frappe Technologies and Contributors
# See license.txt

import frappe
from frappe.desk.doctype.global_search_settings.global_search_settings import (
	reset_global_search_settings_doctypes,
)
from frappe.tests import IntegrationTestCase
from frappe.tests.baseline import DESK_USER, SYSTEM_MANAGER

# On IntegrationTestCase, the doctype test records and all
# link-field test record dependencies are recursively loaded
# Use these module variables to add/remove to/from that list
EXTRA_TEST_RECORD_DEPENDENCIES = []  # eg. ["User"]
IGNORE_TEST_RECORD_DEPENDENCIES = []  # eg. ["User"]


class IntegrationTestGlobalSearchSettings(IntegrationTestCase):
	"""
	Integration tests for GlobalSearchSettings.
	Use this class for testing interactions between multiple components.
	"""

	def test_reset_requires_write_permission(self):
		with self.set_user(DESK_USER):
			self.assertRaises(frappe.PermissionError, reset_global_search_settings_doctypes)

		with self.set_user(SYSTEM_MANAGER):
			reset_global_search_settings_doctypes()
