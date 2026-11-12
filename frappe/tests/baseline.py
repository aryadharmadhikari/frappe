# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: MIT. See LICENSE

import frappe

DESK_USER = "test-baseline-desk-user@example.com"
SYSTEM_MANAGER = "test-baseline-system-manager@example.com"
WEBSITE_MANAGER = "test-baseline-website-manager@example.com"
WORKSPACE_MANAGER = "test-baseline-workspace-manager@example.com"


def make_master_records() -> None:
	"""Create shared reference data and permission actors without committing."""
	for doctype, field, names in (
		("Gender", "gender", ("Female", "Male")),
		("Salutation", "salutation", ("Mr", "Mrs")),
		("Role", "role_name", ("Blogger",)),
	):
		for name in names:
			if not frappe.db.exists(doctype, name):
				frappe.get_doc(doctype=doctype, **{field: name}).insert()

	for email, roles in (
		(DESK_USER, ("Desk User",)),
		(SYSTEM_MANAGER, ("System Manager",)),
		(WEBSITE_MANAGER, ("Website Manager",)),
		(WORKSPACE_MANAGER, ("Desk User", "Workspace Manager")),
	):
		if frappe.db.exists("User", email):
			user = frappe.get_doc("User", email)
			if not user.enabled or {row.role for row in user.roles} != set(roles):
				raise ValueError(
					f"Test baseline user {email!r} must be enabled and have only these roles: {roles!r}"
				)
		else:
			frappe.get_doc(
				doctype="User",
				email=email,
				first_name=roles[-1],
				send_welcome_email=0,
				roles=[{"role": role} for role in roles],
			).insert()
