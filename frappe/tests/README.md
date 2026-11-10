# Frappe Test Framework

This README provides an overview of the test case framework available in Frappe. These utilities are designed to facilitate efficient and effective testing of Frappe applications.

This is different from the `frappe.testing` module which houses the discovery and runner infrastructure for CLI and CI.

## Directory Structure

The test framework is organized into the following structure:

```
frappe/tests/
├── classes/
│   ├── context_managers.py
│   ├── unit_test_case.py
│   └── ...
├── utils/
│   ├── generators.py
│   └── ...
├── test_api.py
├── test_child_table.py
└── ...
```

## Key Components

1. Test case classes (UnitTestCase and IntegrationTestCase)
3. Framework and class specific context managers
4. Utility functions and generators
5. Specific test modules for various Frappe components

### Test Case Classes

#### UnitTestCase ([`classes/unit_test_case.py`](./classes/unit_test_case.py))

###### Import convention: `from frappe.tests import UnitTestCase`

This class extends `unittest.TestCase` and provides additional utilities specific to the Frappe framework. It's designed for testing individual components or functions in isolation.

Key features include:
- Custom assertions for Frappe-specific comparisons
- Utilities for HTML and SQL normalization
- Context managers for user switching and time freezing

#### IntegrationTestCase ([`classes/integration_test_case.py`](./classes/integration_test_case.py))

###### Import convention: `from frappe.tests import IntegrationTestCase`

This class extends `UnitTestCase` and is designed for integration testing. It provides features for:
- Automatic site and connection setup
- Automatic test records loading
- Automatic reset of thread locals
- Context managers that depend on a site connection
- Asserts that depend on a site connection

For a detailed list of context managers, please refer to the code.

#### Test isolation

Use `UnitTestCase` for tests that do not use a database or persistent service. Use `enterContext`
or `addCleanup` for patches and process state.

Set `ISOLATION_MODE = "rollback"` for ordinary integration tests. The runner commits its test
record fixtures, and rolls back each test after `tearDown` and its cleanups. Create additional
database fixtures in `setUp`. Read changes on the test connection when the assertion only needs
to verify a write.

Set `ISOLATION_MODE = "savepoint"` with an `ISOLATION_REASON` when measurements justify sharing
uncommitted class fixtures. The runner rolls back to a protected savepoint after each method and
rolls back the class transaction after class cleanup. A `subTest` shares its method's transaction.

Both modes reject commits, full rollbacks, schema changes, additional connections and worker
dispatch. A test may use savepoints it creates in its own scope. The framework log database uses
a temporary file for each scope. Register cleanup for Redis keys, files, and other external effects.
Use reset mode when the operation under test cannot run inside a guarded transaction.

Set `ISOLATION_MODE = "reset"` with an `ISOLATION_REASON` when a test needs a real commit, controls
the full rollback, or changes schema. The runner installs and disposes of a separate site per
method. MariaDB and PostgreSQL need the database administrator credentials used for test site
installation. Register cleanup for secondary connections and wait for any workers before the
site is discarded.

A cleanup failure in a declared mode stops the test run. The CLI marks the site as quarantined
and rejects further test runs on it. Rebuild the site before clearing the quarantine marker.

Existing classes may still use class-level cleanup or the earlier `ROLLBACK_AFTER_EACH_TEST`
opt-in. Declare a mode as you migrate a class. Change the suite default only after validation
on every supported backend and equivalent first-pass CI measurements.

### Utility Functions and Generators ([`utils/generators.py`](./utils/generators.py))

This module contains utility functions for generating test records and managing test data.

### Specific Test Modules

Various test modules (e.g., test_api.py, test_document.py) contain tests for specific Frappe core components and functionalities.

Note that Document tests are collocated alongside each Document module.

## Usage

To use these test utilities in your Frappe application tests, you can inherit from the appropriate test case class:

```python
from frappe.tests import UnitTestCase

class MyTestCase(UnitTestCase):
    def test_something(self):
        # Your test code here
        pass
```

## Contributing

When adding new test utilities or modifying existing ones:
1. Place them in the appropriate directory based on their function.
2. Update this README to reflect any significant changes in the framework structure or usage.
3. Ensure that your changes follow the existing coding style and conventions.

Remember to always refer to the actual code for the most up-to-date and detailed information on available methods and their usage.
