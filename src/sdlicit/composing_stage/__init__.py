"""Composing stage — ADR creation with agent-assisted suggestions.

The frontend drives the step-by-step MADR form.  After each step the
backend's ADR_Agent analyses the input and returns suggestions.
MADR rendering is handled entirely by the CLI (see cli/shared/madr.py).
"""
