"""Shared test environment: endpoint suites opt into access control explicitly."""

import os


os.environ.setdefault("HIVE_AUTH_MODE", "disabled")
