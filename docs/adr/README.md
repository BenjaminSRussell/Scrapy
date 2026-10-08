# Architecture Decision Records

An ADR records one significant decision: the context, what was decided, and what
follows from it. ADRs are append-only. To change a decision, write a new ADR that
supersedes the old one, and set the old one's status to `Superseded by ADR-NNNN`.

## When to write one

Write an ADR for a decision that is expensive to reverse or that contributors will keep
asking about. Examples: where configuration lives, table or topic contracts, choosing
a storage or messaging technology, cross-stage delivery semantics.

## How

1. Copy [`0000-template.md`](0000-template.md) to `NNNN-short-title.md`, using the next
   free number.
2. Fill it in, starting with status `Proposed`, and open a PR. It becomes `Accepted`
   when the PR merges.
3. Add it to the index below.

## Index

| ADR | Title | Status |
|-----|-------|--------|
| [0001](0001-config-yml-is-canonical.md) | `Scraping_project/config.yml` is the canonical configuration | Accepted |
