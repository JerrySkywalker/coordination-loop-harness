# Contributing

1. Open an issue describing the protocol or tooling change.
2. Keep changes backward-compatible or add an explicit schema migration.
3. Add tests for lease admission, overlap detection, and failure paths.
4. Run `python -m unittest discover -s tests -v` and `clh validate --root .`.
5. Do not include real repository secrets or production evidence in fixtures.
6. Open a pull request with a threat-boundary note for changes to locks, owner gates, or attach tooling.
