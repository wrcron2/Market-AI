"""Validation fortress (Trading System v2.4 plan, §4).

Shared cost model (§4.1), pre-registered hurdle config (§4.2), walk-forward
runner with embargoed folds and block bootstrap (§4.3), and the standardized
verdict table (§4.5). The G1 gate: this package reproduces Item Zero's
published numbers (item_zero_results.csv) within rounding — pinned by
tests/test_fortress_reproduces_item_zero.py.
"""
