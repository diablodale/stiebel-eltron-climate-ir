"""Marks this directory as a package, which is not incidental.

Without it pytest imports `tests/hardware/conftest.py` under the bare module name
`conftest`, where it collides with `tests/conftest.py` -- the one holding the
capture corpus. Whichever loaded second won, and `tests/test_captures.py` and
`tests/test_cli.py`, which both do `from conftest import ...`, failed to import.

With it the module becomes `hardware.conftest`, since `tests/` is deliberately
not a package and so bounds the name.
"""
