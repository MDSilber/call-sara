#!/usr/bin/env python3
"""Entrypoint — the implementation lives in sara.ingest."""
import runpy

runpy.run_module("sara.ingest", run_name="__main__")
