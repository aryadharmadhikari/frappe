import shutil
from pathlib import Path
from uuid import uuid4

import frappe
from frappe.database import drop_user_and_database
from frappe.installer import _new_site
from frappe.utils.local import release_local
from frappe.utils.logging import close_log_db


def create_reset_site(cleanups):
	"""Install a fresh site and register its disposal before the installation starts."""
	if Path(frappe.local.sites_path).resolve() != Path.cwd():
		raise RuntimeError("Run reset tests from the bench's sites directory with bench run-tests.")
	baseline = dict(frappe.local)
	config = frappe.conf.copy()
	apps = [app for app in frappe.get_installed_apps() if app != "frappe"]
	site = f"test-reset-{uuid4().hex}.localhost"
	database = f"_{uuid4().hex[:24]}"
	path = Path(site).resolve()
	if path.exists():
		raise FileExistsError(path)

	cleanups.callback(_restore_local, baseline)
	cleanups.callback(_remove_site_directory, path)
	cleanups.callback(frappe.destroy)
	frappe.init(site, force=True, new_site=True)
	admin_password = frappe.generate_hash(length=24)
	installed = False
	try:
		_new_site(
			database,
			site,
			db_root_username=config.get(f"{config.db_type}_root_login") or config.root_login,
			db_root_password=config.get(f"{config.db_type}_root_password") or config.root_password,
			db_type=config.db_type,
			db_host=config.db_host,
			db_port=config.db_port,
			db_socket=config.db_socket,
			admin_password=admin_password,
			install_apps=apps,
		)
		installed = True
	finally:
		owned = dict(frappe.local)
		owned["flags"] = frappe.flags.copy()
		owned["conf"] = frappe.conf.copy()
		cleanups.callback(_dispose_site_database, owned, database, installed)

	frappe.flags.in_test = True
	return admin_password


def _dispose_site_database(owned, database, installed):
	try:
		if getattr(frappe.local, "site", None) == owned["site"]:
			close_log_db()
			if frappe.db:
				frappe.db.close()
	finally:
		_restore_local(owned)
		if frappe.conf.db_name == database:
			_drop_site_database(database, installed)


def _drop_site_database(database, installed):
	try:
		if installed:
			frappe.clear_cache()
	finally:
		try:
			if frappe.db:
				frappe.db.close()
		finally:
			if frappe.conf.db_type != "sqlite":
				drop_user_and_database(database, database)


def _remove_site_directory(path):
	if path.exists():
		shutil.rmtree(path)


def _restore_local(state):
	release_local(frappe.local)
	for name, value in state.items():
		setattr(frappe.local, name, value)
