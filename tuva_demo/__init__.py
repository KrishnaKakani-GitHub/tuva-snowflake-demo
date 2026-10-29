"""Audit and smoke-check helpers for the Tuva Snowflake demo.

Data scope: Tuva's bundled synthetic data only. Nothing here reads or writes
row-level records; the smoke check issues aggregate COUNT(*) queries only.
"""
