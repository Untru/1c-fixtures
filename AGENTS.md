# 1c-fixtures

Use feature/ for new work branches. This repository owns public native inputs,
fixtures, generation tools and provenance, not converter baselines.

Never add application exports, commercial CF/1CD, private paths or credentials.
Fixtures are independently generated or minimized sanitized legacy fragments.
Do not regenerate native expected bytes with a converter under test. Re-prove
changes with the exact native platform and keep both native rounds identical
outside ConfigDumpInfo.xml. Keep native manifests and source hashes current.
Keep small sets and combined configurations; cross-version CF layouts remain
separate from XML reconstruction. Preserve binary/XML bytes via .gitattributes.

Check: python tools/verify_corpus.py and python -m unittest discover -s tools/coverage -p test_*.py.
Never put baseline.json, storage-baseline.json or converter diagnostic assertions
in the corpus. Consumer repositories own those promises.
