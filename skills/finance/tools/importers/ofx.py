#!/usr/bin/env python3
"""Entrypoint — the implementation lives in sara.cli.ofx."""
import runpy

runpy.run_module("sara.cli.ofx", run_name="__main__")
