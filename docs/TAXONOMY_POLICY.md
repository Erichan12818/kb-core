# Taxonomy Policy

`taxonomy_policy.md` is an optional file in your `kb_root`. It gives kb-core a small set of human rules for classification, tag cleanup, and review boundaries.

Keep the file short. It should steer the self-evolving taxonomy without turning every ingest into a manual filing exercise.

## Location

Put the policy at:

```text
<kb_root>/taxonomy_policy.md
```

With the default local config, that is:

```text
./vault/taxonomy_policy.md
```

With Docker Compose, the container sees it at:

```text
/vault/taxonomy_policy.md
```

## What To Include

Use the policy for durable rules that should affect many files:

- Preferred category names and what belongs in each category.
- Aliases that should be folded into a preferred category or tag.
- Tags that should never be created.
- Topics that require local-only classification or human review.
- Rules for duplicate, stale, or low-value files.

Avoid putting one-off filing instructions, private project names, secrets, or long operational notes in this file. Those belong in normal KB notes.

## Suggested Template

```markdown
# Taxonomy Policy

## Preferred Categories

- engineering: implementation notes, debugging records, architecture decisions, release notes
- research: source material, comparisons, market notes, technical evaluation
- operations: runbooks, deployment notes, incident notes, recurring maintenance
- learning: courses, summaries, exercises, reading notes
- inbox: uncategorized material that needs review

## Category Rules

- Use `engineering` for codebase-specific decisions and debugging records.
- Use `operations` only for repeatable procedures or production/runtime notes.
- Use `inbox` when the classifier is uncertain instead of inventing a narrow category.

## Preferred Tags

- architecture
- debugging
- release
- runbook
- evaluation
- glossary

## Aliases

- `ops`, `deployment`, and `maintenance` should map to `operations` when they describe procedures.
- `dev`, `implementation`, and `coding` should map to `engineering` when they describe code work.

## Forbidden Tags

- misc
- random
- todo
- important

## Sensitive Routing

- Credential-related, client-related, financial, legal, or personal identity material must use local-only classification.
- Files that contain access tokens, private keys, or webhook URLs must be skipped rather than classified.

## Audit Rules

- Low-risk tag cleanup can be applied automatically when it only removes forbidden tags or merges obvious aliases.
- Category merges, category splits, and duplicate-file deletion require human review.
- Files older than 90 days with no related links should be listed for review, not deleted automatically.
```

## How kb-core Uses It

Loop 1 reads the policy when classifying new or changed files. It should prefer existing categories and tags unless the content clearly needs a new one.

Loop 2 reads the policy during taxonomy audit. It can propose category splits, category merges, tag retirement, and duplicate groups, but high-risk changes should remain reviewable.

Loop 3 does not call an LLM. It renders the catalog and MOC files from the current index and taxonomy state.
