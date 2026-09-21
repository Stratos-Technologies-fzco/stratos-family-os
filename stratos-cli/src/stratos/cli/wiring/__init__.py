"""Composition root helpers: build application services from concrete adapters.

Each function takes the `CliContext` and returns a fully wired service. Application code never
imports these adapters; only this package (and `cli.context`) does.
"""
