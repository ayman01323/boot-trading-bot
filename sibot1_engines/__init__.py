"""SiBot 1 independent trading-engine namespace.

This package is intentionally inert at import time. Engine packages may emit intents;
shared infrastructure owns risk gating, capital, signing and execution.
"""
