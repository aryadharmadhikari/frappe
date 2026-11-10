import re
import sqlite3
from contextlib import ExitStack, contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from weakref import WeakSet

import psycopg
import pymysql
import sqlparse
from psycopg.sql import Composable
from rq import Queue

import frappe
from frappe.database.database import Database
from frappe.database.utils import get_query_type
from frappe.utils.logging import LogDatabase, close_log_db

DATA_STATEMENTS = frozenset({"select", "insert", "update", "delete", "replace", "with", "show", "explain"})
SAVEPOINT_STATEMENT = re.compile(
	r"\s*(savepoint|release(?:\s+savepoint)?|rollback(?:\s+work)?\s+to(?:\s+savepoint)?)"
	r"\s+([A-Za-z_][A-Za-z_0-9]*)\s*;?\s*",
	re.IGNORECASE,
)
ESCAPING_SQL = re.compile(r"/\*!|/\*M!|\b(?:into\s+(?:outfile|dumpfile)|or\s+rollback)\b", re.I)
TRACE_COMMENT = re.compile(r"/\* FRAPPE_TRACE_ID: [0-9a-fA-F-]{8,64} \*/\s*$")
COPY_FROM_STDIN = re.compile(r"\s*copy\s+[^;]+\s+from\s+stdin\s*;?\s*", re.I)


class TransactionBoundaryError(AssertionError):
	pass


class TransactionGuard(ExitStack):
	def __init__(self, db):
		super().__init__()
		self.db = db
		self.savepoints = set()
		self.savepoint_owners = []
		self.runner_operation = False
		self.violation = None
		self.log_connections = WeakSet()

	def __enter__(self):
		db = self.db
		connection = db._conn
		connect_database = Database.connect
		check_transaction_status = Database.check_transaction_status
		sql = type(db).sql
		connect_sqlite = sqlite3.connect
		connect_log = LogDatabase.connect

		def connect_isolated_log(log_db):
			with (
				patch.object(Database, "connect", connect_database),
				patch.object(sqlite3, "connect", connect_sqlite),
			):
				self.log_connections.add(log_db)
				return connect_log(log_db)

		def check_connection_owner(database, *args, **kwargs):
			if database is not db and database not in self.log_connections:
				self.reject("using another database connection")
			return check_transaction_status(database, *args, **kwargs)

		def execute_sql(database, query, *args, **kwargs):
			if database is db and isinstance(query, str) and kwargs.get("run", True):
				self.check_sql(query, track=False)
			return sql(database, query, *args, **kwargs)

		try:
			self.connection = GuardedConnection(self, connection)
			self.enter_context(patch.object(db, "_conn", self.connection))
			self.enter_context(patch.object(db, "_cursor", GuardedCursor(self, db._cursor)))
			self.enter_context(patch.object(type(db), "sql", execute_sql))
			for method in ("commit", "begin", "close", "sql_ddl", "read_only"):
				self._patch_operation(db, method)
			self._patch_operation(db, "rollback", savepoint=True)
			for target, name in (
				(frappe, "connect"),
				(frappe, "connect_replica"),
				(frappe, "init"),
				(Database, "connect"),
				(sqlite3, "connect"),
				(pymysql, "connect"),
				(psycopg, "connect"),
				(psycopg.Connection, "connect"),
			):
				self.enter_context(patch.object(target, name, self.reject_connection))
			self.enter_context(patch.object(LogDatabase, "connect", connect_isolated_log))
			self.enter_context(patch.object(Database, "check_transaction_status", check_connection_owner))
			self.enter_context(patch.object(Queue, "enqueue_job", self.reject_enqueue))
			try:
				import MySQLdb
			except ImportError:
				pass
			else:
				self.enter_context(patch.object(MySQLdb, "connect", self.reject_connection))
			if db.db_type == "postgres":
				for name in ("escape", "mogrify"):
					self.enter_context(patch.object(db, name, self._adapt_sql(name, connection)))
		except BaseException:
			self.close()
			raise
		return self

	@contextmanager
	def isolated_log_database(self):
		with ExitStack() as stack:
			path = None

			def log_path(db):
				nonlocal path
				if path is None:
					path = Path(stack.enter_context(TemporaryDirectory())) / "logs.db"
				return path

			stack.enter_context(patch.object(LogDatabase, "get_db_path", log_path))
			keys = ("log_db", "log_tables_ready")
			previous = {key: getattr(frappe.local, key) for key in keys if hasattr(frappe.local, key)}
			frappe.local.log_db = None
			frappe.local.log_tables_ready = set()
			try:
				yield
			finally:
				try:
					close_log_db()
				finally:
					for key in keys:
						if key in previous:
							setattr(frappe.local, key, previous[key])
						elif hasattr(frappe.local, key):
							delattr(frappe.local, key)

	def _patch_operation(self, db, name, *, savepoint=False):
		original = getattr(db, name)

		def guarded(*args, **kwargs):
			if not self.runner_operation and not (savepoint and kwargs.get("save_point")):
				self.reject(f"frappe.db.{name}()")
			return original(*args, **kwargs)

		self.enter_context(patch.object(db, name, guarded))

	def _adapt_sql(self, name, connection):
		adapt = getattr(self.db, name)

		def with_native_connection(*args, **kwargs):
			with patch.object(self.db, "_conn", connection):
				return adapt(*args, **kwargs)

		return with_native_connection

	@contextmanager
	def boundary(self):
		self.runner_operation = True
		try:
			yield
		finally:
			self.runner_operation = False

	def check_sql(self, query, *, track=True):
		if self.runner_operation:
			return
		if isinstance(query, bytes):
			query = query.decode()
		if not isinstance(query, str):
			self.reject("a non-string driver query")
		if ESCAPING_SQL.search(query) or (
			";" in query.rstrip().removesuffix(";") and len(sqlparse.split(query)) != 1
		):
			self.reject("SQL with unscoped effects")
		if match := SAVEPOINT_STATEMENT.fullmatch(TRACE_COMMENT.sub("", query)):
			operation, name = (part.lower() for part in match.groups())
			if name in self.savepoints:
				self.reject("access to a runner savepoint")
			owned = self.savepoint_owners[-1]
			if operation == "savepoint":
				if any(name in outer for outer in self.savepoint_owners[:-1]):
					self.reject("shadowing a savepoint from an outer scope")
				if track:
					owned.add(name)
			elif name not in owned:
				self.reject("access to a savepoint from outside the current scope")
		else:
			query_type = get_query_type(query)
			if query_type not in DATA_STATEMENTS:
				self.reject(f"{query_type or 'unrecognized'} SQL")

	def check_connection(self):
		if self.db._conn is not self.connection or getattr(frappe.local, "db", None) is not self.db:
			raise TransactionBoundaryError("The rollback test lost its database connection")

	def report_violation(self):
		if self.violation:
			message, self.violation = self.violation, None
			raise TransactionBoundaryError(message)

	def reject(self, operation):
		message = f"{operation} is forbidden inside a rollback test. Use an isolated environment if required."
		self.violation = self.violation or message
		raise TransactionBoundaryError(message)

	def reject_connection(self, *args, **kwargs):
		self.reject("opening another database connection")

	def reject_enqueue(self, *args, **kwargs):
		self.reject("dispatching a job outside the test transaction")


class GuardedConnection:
	__slots__ = ("_connection", "_guard")

	def __init__(self, guard, connection):
		object.__setattr__(self, "_guard", guard)
		object.__setattr__(self, "_connection", connection)

	def __setattr__(self, name, value):
		if name == "_result" and value is None:
			self._connection._result = None
			return
		self._guard.reject(f"changing the driver connection's {name}")

	def __getattr__(self, name):
		if self._guard.runner_operation or name in {"info", "literal"}:
			return getattr(self._connection, name)
		self._guard.reject(f"using the driver connection's {name}")

	def cursor(self, *args, **kwargs):
		return GuardedCursor(self._guard, self._connection.cursor(*args, **kwargs))


class GuardedCursor:
	def __init__(self, guard, cursor):
		object.__setattr__(self, "_guard", guard)
		object.__setattr__(self, "_cursor", cursor)

	def __setattr__(self, name, value):
		if name in {"_result", "_rows"} and value is None:
			setattr(self._cursor, name, value)
			return
		self._guard.reject(f"changing the driver cursor's {name}")

	def __getattr__(self, name):
		if name == "connection":
			return self._guard.db._conn
		if name in {
			"_executed",
			"_result",
			"_rows",
			"adapters",
			"description",
			"mogrify",
			"rowcount",
		} or name.startswith("fetch"):
			return getattr(self._cursor, name)
		self._guard.reject(f"using the driver cursor's {name}")

	def execute(self, query, *args, **kwargs):
		self._guard.check_sql(query)
		result = self._cursor.execute(query, *args, **kwargs)
		return self if result is self._cursor else result

	def executemany(self, query, *args, **kwargs):
		self._guard.check_sql(query)
		result = self._cursor.executemany(query, *args, **kwargs)
		return self if result is self._cursor else result

	def copy(self, query, *args, **kwargs):
		statement = query.as_string(self._cursor) if isinstance(query, Composable) else query
		if not isinstance(statement, str) or not COPY_FROM_STDIN.fullmatch(statement):
			self._guard.reject("COPY outside the test transaction")
		return self._cursor.copy(query, *args, **kwargs)

	def close(self):
		self._cursor.close()
