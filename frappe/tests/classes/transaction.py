from uuid import uuid4

from frappe.utils import CallbackManager

CALLBACKS = ("before_commit", "after_commit", "before_rollback", "after_rollback")


class TransactionScope:
	def __init__(self, db, *, savepoint=False):
		self.db = db
		self.savepoint = f"frappe_test_{uuid4().hex}" if savepoint else None
		self.transaction_writes = db.transaction_writes
		if self.savepoint:
			db.savepoint(self.savepoint)
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
				self._rollback()
			if self.savepoint:
				db.release_savepoint(self.savepoint)
		finally:
			if self.savepoint:
				db.transaction_writes = self.transaction_writes
			for name, callbacks in self.outer_callbacks.items():
				setattr(db, name, callbacks)
		if errors:
			raise ExceptionGroup("Test rollback callbacks failed", errors)

	def _rollback(self):
		for name in CALLBACKS:
			getattr(self.db, name).reset()
		self.db.rollback(save_point=self.savepoint)


def _run_rollback_callbacks(callbacks, errors):
	while len(callbacks):
		try:
			callbacks.run()
		except Exception as error:
			errors.append(error)
