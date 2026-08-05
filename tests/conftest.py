"""Shared pytest fixtures."""
import sys
import os

# Ensure the project root is on the path so both root scripts and kalshi/ package resolve.
ROOT = os.path.dirname(os.path.dirname(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
