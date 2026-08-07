#!/usr/bin/env python3
"""Entrypoint — the implementation lives in sara.advisor.run_checks."""
import runpy

runpy.run_module("sara.advisor.run_checks", run_name="__main__")
