import unittest

import frappe


class TestSuite(unittest.TestSuite):
	def _tearDownPreviousClass(self, test, result):
		previous = getattr(result, "_previousTestClass", None)
		errors = len(getattr(result, "errors", ()))
		super()._tearDownPreviousClass(test, result)
		if getattr(previous, "ISOLATION_MODE", None) and len(getattr(result, "errors", ())) > errors:
			frappe.local.test_isolation_failed = True

	def _handleModuleFixture(self, test, result):
		if getattr(frappe.local, "test_isolation_failed", False):
			result.stop()
			result._moduleSetUpFailed = True
			return
		super()._handleModuleFixture(test, result)
