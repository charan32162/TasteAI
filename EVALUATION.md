# TasteAI evaluation

## Deterministic regression corpus

Run from the repository root:

```powershell
python -m tasteai.evaluation.run --strict
```

The evaluator runs 110 synthetic search cases against seven hand-crafted fictional evaluation-only fixtures. These fixtures are intentionally separate from the public 1,000-dish synthetic demo catalog so catalog changes do not silently rewrite recommendation expectations. API tests independently cover loading 1,000 unique catalog dishes, metadata preservation, name search, budget filtering, and API access.

| Regression measure | Result |
| --- | ---: |
| Strict evaluator cases | 110 / 110 |
| Intent expectations | 165 / 165 |
| Hard constraints | 179 / 179 |
| Requested top-match attributes | 8 / 8 |
| Grounded explanation claims | 494 / 494 |
| RAG claim classifications | 5 / 5 |
| RAG retrieval checks | 5 / 5 |
| Multi-restaurant checks | 5 / 5 |

These are deterministic checks on synthetic fixtures, not real-world recommendation accuracy, satisfaction probability, restaurant quality, live menu coverage, or evidence confidence.

## Retrieval and grounding fixtures

The RAG fixtures check five expected claim classifications. Supported-claim citation coverage is 2/2; no unsupported claim output occurs across three invalid selectors. The five retrieval cases cover exact lexical matching, a synthetic semantic-vector match without lexical overlap, hard budget filtering, stale-evidence filtering, and conflicting-price filtering.

The formulas are: claim-status accuracy = correctly classified fixture selectors / all fixture selectors; supported-claim citation coverage = supported selectors with cited evidence IDs / supported selectors; unsupported-claim output rate = invalid selectors that affect the rendered answer / invalid selectors. The semantic-vector case uses a deterministic test embedder; it does not measure OpenAI embedding quality. These are regression fixtures, not measurements of live-model accuracy, natural-language entailment, or a real-world hallucination rate.

The multi-restaurant fixture exercises overlapping dishes, aliases, stale or revoked sources, unknown signals, dietary filtering, and citations. Its 5/5 result is a regression check of those fixture expectations, not restaurant discovery coverage.

## Test suite

Run from the repository root:

```powershell
python -m pytest -q
```

Latest verified result: **196 passed, 1 skipped, 45 subtests**. The suite covers API and authentication/profile behavior, deterministic parsing and ranking, group constraints, Taste DNA learning, mocked Google Places and Nominatim/Overpass adapters, evidence workflows, and offline structured-data parsing. The optional live Places smoke test is skipped by default; ordinary tests use mocked transports and do not call external providers.

## Interpretation and gaps

The evaluator and tests measure deterministic regression behavior only. There is no labeled real-user feedback set, licensed broad restaurant/menu dataset, restaurant-identity resolution benchmark, comprehensive freshness/contradiction corpus, browser automation suite, or calibrated confidence evaluation. Synthetic fixtures do not establish live provider compatibility, real-world recommendation quality, or live model behavior.
