# Recall evaluation

kb-core evaluates retrieval quality without answer generation. The query set is
`kb/eval_queries.yaml` (override with `--queries`); each query records an
expected category and one or more expected keywords.

## What the modes mean

| Mode | Flag | What it measures |
|---|---|---|
| Global | *(none)* | One search over the whole collection. |
| Two-stage | `--two-stage` | Guess a category from the taxonomy first, search inside it when confident, otherwise fall back to a grouped global search. |
| Oracle-filtered | `--use-expected-category-filter` | Search inside the correct category, supplied by the query set. This is the ceiling, not something a real query gets for free. |

## Results

Measured 2026-07-29 against the maintainer's corpus (78 documents, 10
categories, 1288 vectors), `top_k=3`.

| Metric | Global | Two-stage | Oracle-filtered |
|---|---:|---:|---:|
| Top-1 in expected category | 8/10 | **9/10** | 10/10 |
| Expected category @3 | 8/10 | **9/10** | 10/10 |
| Expected keyword hit | 9/10 | 8/10 | 9/10 |

Two-stage closes part of the gap between an unaided global search and the
oracle ceiling: routing improves by one query, and the mode reports its guess
and confidence so a caller can widen the search when the guess looks wrong.

It is not free. Keyword hit drops by one, which is the expected shape of the
trade: narrowing to a category can exclude a document that would have matched
on wording alone. Queries where confidence stays low are deliberately left as
grouped global results rather than forced into a category.

## Reproducing

```bash
python -m kb.eval                                  # global
python -m kb.eval --two-stage                      # two-stage routing
python -m kb.eval --use-expected-category-filter   # oracle ceiling
```

Category guesses come from the categories, aliases, descriptions, and subtopics
in the runtime `state/TAXONOMY.json`. The evaluator holds no query-to-category
overrides, so these numbers reflect routing that works on any corpus rather
than a lookup table fitted to this one.

## Reading these numbers

They describe one corpus. The shipped query set targets the maintainer's
knowledge base — its categories and expected keywords will not match yours, so
running it unchanged against a different corpus measures nothing useful. To
evaluate your own install, point `--queries` at questions you actually ask and
categories you actually keep.

An earlier baseline recorded on 2026-06-30 (global top-1 3/10, oracle 10/10) is
not comparable to the table above: the corpus has since grown and been
reorganised, including a category split. The three modes above were all
measured in the same run on the same data, which is the only comparison that
carries meaning here.
