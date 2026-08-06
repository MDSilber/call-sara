"""Beancount-facing half of the package: queries, rendering, the one writer.

Deliberately the only part of sara that knows the ledger is Beancount —
swap the engine and only this package changes.
"""
