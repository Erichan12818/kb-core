# Recall evaluation

kb-core evaluates retrieval quality without answer generation. The baseline set
is defined in `kb/eval_queries.yaml`; each query records an expected category
and one or more expected keywords.

## Before / after

The “after” column is intentionally left open until the two-stage mode has been
run against the user's Qdrant collection.

| Metric | Before: global | Before: expected-category filtered | After: two-stage |
|---|---:|---:|---:|
| Top-1 expected category | 3/10 | 10/10 | TBD / 待填 |
| Expected category @3 | 6/10 | 10/10 | TBD / 待填 |
| Expected category @5 | 7/10 | Not recorded | TBD / 待填 |
| Expected keyword hit | 10/10 | 9/10 | TBD / 待填 |

Run the new routing mode with:

```bash
python -m kb.eval --two-stage --top-k 3
python -m kb.eval --two-stage --top-k 5
```

For comparison, the original global and oracle-filtered modes remain available:

```bash
python -m kb.eval --top-k 3
python -m kb.eval --use-expected-category-filter --top-k 3
```

Category guesses are derived from the categories, aliases, descriptions, and
subtopics loaded from the runtime `state/TAXONOMY.json`. The evaluator does not
contain query-to-category overrides.
