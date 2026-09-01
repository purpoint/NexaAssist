"""Tests that require a real PostgreSQL instance.

Everything in this package opts out of the suite-wide no-network guard (see
``conftest.py``) and is skipped when PostgreSQL is unreachable, so the suite
still runs offline.
"""
