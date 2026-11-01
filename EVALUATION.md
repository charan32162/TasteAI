# TasteAI evaluation

**Updated:** 2026-10-22

## Deterministic regression corpus

Run from the repository root:

```powershell
.\tasteai\.venv\Scripts\python.exe -m tasteai.evaluation.run --strict
```

The current corpus contains 110 synthetic search requests against the explicitly fictional seven-dish catalog. The strict evaluator checks parsed intent, hard constraints, requested attributes in the top result, and explanation grounding. It also runs a synthetic typed-claim fixture through the independent RAG validator; that fixture covers supported listing/price claims, unknown evidence, unknown prices, duplicates, citations, and injected text. Latest run: no failures.

| Metric | Result |
| --- | ---: |
| Intent expectations | 165 / 165 |
| Hard constraints | 179 / 179 |
| Requested top-match attributes | 8 / 8 |
| Grounded explanation claims | 494 / 494 |

These counts are test coverage on synthetic fixtures, not real-world recommendation accuracy, satisfaction probability, restaurant quality, or evidence confidence.

The RAG fixtures reported 5/5 expected claim classifications, citation coverage of 2/2 supported claims, zero unsupported-claim output across three invalid selectors, and 5/5 retrieval checks (exact lexical match, synthetic semantic-vector match without lexical overlap, hard budget, stale-evidence, and conflicting-price filters). These formulas are: claim-status accuracy = correctly classified fixture selectors / all fixture selectors; supported-claim citation coverage = supported selectors with cited evidence IDs / supported selectors; unsupported-claim output rate = invalid selectors that affect the rendered answer / invalid selectors. Semantic-vector cases use a deterministic test embedder; they do not measure OpenAI embedding quality. These are deterministic regression fixtures, not measurements of a live model's accuracy or real-world hallucination rate.

## Test suite

```powershell
.\tasteai\.venv\Scripts\python.exe -m unittest discover -s tasteai\tests -v
```

The suite covers API/auth/profile behavior, deterministic parsing/ranking, group constraints, Taste DNA learning, Google Places mocks and cost/privacy gates, mocked Nominatim/Overpass integration, and the offline structured-data parser. Live Google Places testing is opt-in and skipped by default because it may be billable; ordinary tests do not contact external providers.

Latest suite run: 181 tests, 180 passed, and one optional live Places smoke test skipped. Automated provider tests use mocked transports and do not make external calls. The current milestone made zero live OpenAI, Google Places, or OSM requests. Separately, one controlled Google Places discovery and one Place Details request were made for Chintalapudi on 2026-10-21; they verified five bounded restaurant candidates and no menu evidence. This single historical live check does not evaluate recommendation accuracy or verify the new AI paths.

## Gaps

No labeled real-user feedback set, licensed restaurant/menu dataset, restaurant identity resolution benchmark, broad freshness/contradiction corpus, browser automation suite, or calibrated confidence evaluation exists. The synthetic claim fixture does not establish live model behavior, natural-language entailment accuracy, or live provider compatibility. `pytest` is not installed in the current virtual environment; the full existing `unittest` suite was used instead.
