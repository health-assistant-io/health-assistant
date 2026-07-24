# AI Ontology Mapping

The `/map` endpoint aligns raw portal metric names to standardized biomarker definitions in your catalog before you push the data. It's the difference between "Sodium", "Natrium", and "Na" all landing on the same history vs. creating three disconnected entries.

## Why mapping matters

Health portals use inconsistent naming: "Natrium (Na)", "Sodium", "Na", "Sérumsodium". If you push these raw names to `/sync`, the backend's Biomarker Engine resolves by code/name heuristics and may create duplicate, disconnected biomarker definitions. The `/map` endpoint uses the configured LLM to propose a precise alignment against the patient's existing catalog, so a subsequent `/sync` carrying the resolved `biomarker_id` links cleanly to the existing history.

## The flow

```
1. You scrape the portal and collect the raw metric names you don't recognise.
2. POST /map  →  the LLM proposes a mapping per metric:
       - map_to_existing  → an existing BiomarkerDefinition id
       - create_new       → a new definition (LOINC/SNOMED code + display)
3. Your client shows the proposals to the user for confirmation.
4. Cache the confirmed mappings locally (name → biomarker_id).
5. On subsequent /sync calls, set biomarker_id on the records that use those names.
```

`/map` never persists anything. The user must confirm the proposal — the AI proposes, you approve.

## The request

```json
{
  "unmapped_metrics": [
    { "name": "Natrium (Na)", "code": null },
    { "name": "HCT", "code": null }
  ]
}
```

Pass a `code` when the portal exposes one (a LOINC, a SNOMED, or a proprietary code); it sharpens the LLM's match.

## The response

```json
{
  "mappings": [
    {
      "original_name": "Natrium (Na)",
      "action": "map_to_existing",
      "existing_biomarker_id": "uuid-of-sodium-record",
      "new_biomarker_name": null,
      "new_biomarker_code": null,
      "new_biomarker_coding_system": "loinc"
    },
    {
      "original_name": "HCT",
      "action": "create_new",
      "existing_biomarker_id": null,
      "new_biomarker_name": "Hematocrit",
      "new_biomarker_code": "20570-8",
      "new_biomarker_coding_system": "loinc"
    }
  ]
}
```

### `map_to_existing`

The LLM found a `BiomarkerDefinition` in the patient's catalog that matches the raw name. **Cache `existing_biomarker_id` keyed by `original_name`** so every subsequent `/sync` record for that metric carries it.

### `create_new`

No existing match. The LLM proposes a new definition — a canonical display name, a candidate LOINC/SNOMED code, and the coding system. Show the proposal to the user (display name + code, prefilled). On confirm:

- record the proposed `new_biomarker_*` locally (so you know which raw name maps to which new entry), and
- push a `/sync` record that carries the proposed `code` + `coding_system` + `name`; the backend's Biomarker Engine creates the `BiomarkerDefinition` lazily and links the record to it. Subsequent `/map` calls for the same portal will then return `map_to_existing`.

### Coding systems

| `coding_system` | FHIR system | When |
|---|---|---|
| `loinc` | `http://loinc.org` | Default for clinical labs + standard vitals. |
| `snomed` | `http://snomed.info/sct` | Clinical terms/findings. |
| `custom` | `urn:uuid:health-assistant:custom-biomarker` | Proprietary portal codes / wearable IDs — prevents collisions with real clinical LOINC codes. |

## When to call `/map`

- **First sync** for a new portal — map every metric name you don't recognise.
- **New lab panel** appears in the portal — map the new metric names before pushing.
- **A mapping you cached looks wrong** — re-map and re-confirm.

You don't need to call `/map` on every sync — once a raw name has a cached `biomarker_id`, just use it on `/sync`.

## The AI isn't sure? 

The LLM may return `create_new` for a term that *should* have matched. That's why the user confirms — if they spot a better existing match, override `action` to `map_to_existing` locally and cache that id. `/map` is an aid, not an oracle.

## See also

- [API Reference — POST /map](api-reference.md#post-map)
- [API Reference — the `ClientRecord` schema](api-reference.md#the-universal-data-contract)
- [Client SDK Setup](client-setup.md)