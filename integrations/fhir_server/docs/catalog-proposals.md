# Catalog Proposals

A hospital FHIR server carries LOINC and SNOMED codes — a high-quality source of biomarker definitions. But some remote codes won't exist in your local biomarker catalog yet. Rather than silently dropping those observations as "unmapped", the integration queues **human-in-the-loop proposals** so you can decide whether to define each one.

The principle: **AI proposes, you approve.** The integration never writes a biomarker definition directly.

## How it works

During each pull sync, the integration scans the remote observations it just fetched for standard LOINC/SNOMED codes and diffs them against your local `BiomarkerDefinition` catalog:

1. **Discover** — collect every LOINC/SNOMED code seen in the remote observations, along with the display name and unit the server reports.
2. **Diff** — drop codes already present in your local catalog.
3. **Propose** — for each unknown code, queue a *Define Biomarker* proposal card carrying the code, display, and unit.
4. **Remember** — a seen-codes cursor records every code the integration has offered (known or proposed), so it never re-offers the same code on the next sync.

## Resolving a proposal

Each proposal lands as a card in your review queue with the code, a suggested name, and the unit. You can:

- **Approve** — (optionally edit the details first) the proposal is applied through the canonical catalog write path, creating a `BiomarkerDefinition`. **Subsequent pulls of that code now map cleanly** to the new definition — the unmapped observations become first-class biomarker data.
- **Reject / dismiss** — the code is recorded as seen and never re-proposed.

Resolving a proposal (either way) advances the seen-codes cursor via a resolution callback, so the decision is sticky across syncs.

## When proposals fire

The proposal scan runs whenever the sync direction allows pulling (it scans the core Observation stream). It's capped per sync (default 20 new proposals) so a large first pull can't flood your review queue. Proposals are deduped by `(type, payload)` — re-emitting the same spec on consecutive syncs is a no-op that doesn't re-spam your inbox.

## See also

- [Pull — Full Patient Record](pull.md)
- [Overview](overview.md)
