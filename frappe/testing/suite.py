import unittest

import frappe


class TestSuite(unittest.TestSuite):
	def _handleModuleFixture(self, test, result):
		if getattr(frappe.local, "test_isolation_failed", False):
			result.stop()
			result._moduleSetUpFailed = True
			return
		super()._handleModuleFixture(test, result)
