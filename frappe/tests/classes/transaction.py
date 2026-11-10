from contextlib import nullcontext
from uuid import uuid4

from frappe.utils import CallbackManager

CALLBACKS = ("before_commit", "after_commit", "before_rollback", "after_rollback")


class TransactionScope:
	def __init__(self, db, *, savepoint=False, guard=None, verify=True):
		self.db = db
		self.guard = guard
		self.savepoint = savepoint
		self.marker = f"frappe_test_{uuid4().hex}" if savepoint or (guard and verify) else None
		self.transaction_writes = db.transaction_writes
		if self.marker:
			with self._boundary():
				db.savepoint(self.marker)
			if guard:
				guard.savepoints.add(self.marker)
		if guard:
			guard.savepoint_owners.append(set())
		self.outer_callbacks = {name: getattr(db, name) for name in CALLBACKS}
		for name in CALLBACKS:
			setattr(db, name, CallbackManager())

	def close(self):
		db = self.db
		errors = []
		try:
			_run_rollback_callbacks(db.before_rollback, errors)
			after_rollback = db.after_rollback.cut(0)
			self._rollback()
			if after_rollback:
				for callback in after_rollback:
					db.after_rollback.add(callback)
				_run_rollback_callbacks(db.after_rollback, errors)
				self._rollback(verify=False)
			if self.savepoint:
				with self._boundary():
					db.release_savepoint(self.marker)
		except Exception as error:
			errors.append(error)
		finally:
			if self.guard:
				self.guard.savepoint_owners.pop()
				if self.marker:
					self.guard.savepoints.remove(self.marker)
			if self.savepoint:
				db.transaction_writes = self.transaction_writes
			for name, callbacks in self.outer_callbacks.items():
				setattr(db, name, callbacks)
		if errors:
			raise ExceptionGroup("Test rollback callbacks failed", errors)

	def _rollback(self, *, verify=True):
		for name in CALLBACKS:
			getattr(self.db, name).reset()
		if self.guard:
			self.guard.check_connection()
		with self._boundary():
			if self.savepoint:
				self.db.rollback(save_point=self.marker)
			else:
				try:
					if verify and self.marker:
						self.db.rollback(save_point=self.marker)
				finally:
					self.db.rollback()

	def _boundary(self):
		return self.guard.boundary() if self.guard else nullcontext()


def _run_rollback_callbacks(callbacks, errors):
	while len(callbacks):
		try:
			callbacks.run()
		except Exception as error:
			errors.append(error)
