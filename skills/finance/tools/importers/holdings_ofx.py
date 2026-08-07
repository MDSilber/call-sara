#!/usr/bin/env python3
"""Entrypoint — the implementation lives in sara.cli.holdings_ofx."""
import runpy

runpy.run_module("sara.cli.holdings_ofx", run_name="__main__")
