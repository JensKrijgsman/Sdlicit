# Test layout

- `test/backend/` tests import from `sdlicit.*` (the package in `src/`).
- `test/cli/` tests exercise `cli/` (the terminal client, mocking the
  REST client and filesystem, no live server needed).
- `test/test_benchmark_scoring.py` tests the standalone `benchmark/`
  package at the repo root, kept separate since it is its own top level
  package, not part of `src/sdlicit` or `cli/`.
- `test/conftest.py` stays at the top of `test/` on purpose: it puts
  `cli/` on `sys.path` for every test in the tree, including
  `test/cli/`, since pytest applies a `conftest.py` to its whole
  subtree regardless of nesting depth.

Run everything with `uv run pytest -v` from `sdlicit-public/`.
