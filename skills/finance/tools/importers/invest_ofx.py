#!/usr/bin/env python3
"""Entrypoint — the implementation lives in sara.cli.invest_ofx."""
import runpy

runpy.run_module("sara.cli.invest_ofx", run_name="__main__")
