# ruff: noqa: SIM108,E501 -- ternary and long lines; reflow when touched
"""Biomarker trends (slug resolution, OHLC buckets, series assembly)."""

import logging

from app.services.analytics.status import (
    _get_observation_status,
)
from app.services.analytics.types import (
    _ALLOWED_TELEMETRY_BUCKETS,
    UTC,
    AsyncSession,
    BiomarkerAllowedState,
    BiomarkerDefinition,
    BiomarkerValueType,
    DocumentModel,
    ExaminationModel,
    Observation,
    datetime,
    is_safe_slug,
    re,
    select,
    selectinload,
    units_are_compatible,
)

logger = logging.getLogger(__name__)


async def get_biomarker_trends(
    tenant_id: str,
    biomarker_codes: str | None = None,
    period: str = "last-6-months",
    aggregation: str | None = None,
    patient_id: str | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    db: AsyncSession = None,
) -> dict:
    if not db:
        return {"biomarkers": []}

    query = select(Observation).where(Observation.tenant_id == tenant_id)
    if patient_id:
        query = query.where(Observation.subject["reference"].as_string() == f"Patient/{patient_id}")

    from app.models.biomarker_model import Unit

    if biomarker_codes:
        codes = [c.strip() for c in biomarker_codes.split(",")]
        import uuid

        from sqlalchemy import or_

        # Resolve the requested codes/slugs against the definition catalog so we
        # can expand the filter to include the matched definitions' names,
        # aliases, and LOINC codes. This is essential for catching UNMAPPED
        # observations (biomarker_id is NULL) whose stored code.text is the raw
        # lab label (e.g. "WBC", "Leukocytes") rather than the canonical slug.
        # Without this, the detail page shows "No historical data" even though
        # the unfiltered trends page resolves the same data via fallback.
        uuid_codes = []
        text_codes = []
        for c in codes:
            try:
                uuid.UUID(c)
                uuid_codes.append(c)
            except ValueError:
                text_codes.append(c)

        expanded_terms = list(text_codes)
        matched_def_ids = []

        def_filters = []
        if text_codes:
            def_filters.append(
                or_(
                    *[BiomarkerDefinition.slug.ilike(f"%{c}%") for c in text_codes],
                    *[BiomarkerDefinition.name.ilike(f"%{c}%") for c in text_codes],
                    *[BiomarkerDefinition.code.ilike(f"%{c}%") for c in text_codes],
                )
            )
        if uuid_codes:
            def_filters.append(BiomarkerDefinition.id.in_([uuid.UUID(u) for u in uuid_codes]))

        if def_filters:
            def_filter = or_(*def_filters)
            def_res = await db.execute(select(BiomarkerDefinition).where(def_filter))
            for b in def_res.scalars().all():
                matched_def_ids.append(b.id)
                if b.name:
                    expanded_terms.append(b.name)
                if b.aliases:
                    expanded_terms.extend(a for a in b.aliases if a)

        # Deduplicate while preserving order
        seen = set()
        expanded_terms = [
            t for t in expanded_terms if not (t.lower() in seen or seen.add(t.lower()))
        ]

        # Join with BiomarkerDefinition to filter by slug (mapped observations)
        query = query.outerjoin(
            BiomarkerDefinition, Observation.biomarker_id == BiomarkerDefinition.id
        )

        filter_clauses = []
        # Match by expanded text terms (raw codes + definition names/aliases)
        for term in expanded_terms:
            filter_clauses.append(Observation.code["text"].as_string().ilike(f"%{term}%"))
        # Match by slug via the join (mapped observations)
        for code in text_codes:
            filter_clauses.append(BiomarkerDefinition.slug.ilike(f"%{code}%"))
        # Match by biomarker_id directly (mapped observations)
        if matched_def_ids:
            filter_clauses.append(Observation.biomarker_id.in_(matched_def_ids))

        query = query.where(or_(*filter_clauses))

    if start_date:
        query = query.where(Observation.effective_datetime >= start_date)
    if end_date:
        query = query.where(Observation.effective_datetime <= end_date)

    # Limit the total number of observations fetched to prevent massive unpaginated queries crashing the server
    # 2000 is usually enough for a single patient's trend history
    query = query.limit(2000)

    result = await db.execute(query)
    observations = result.scalars().all()

    # Fetch Biomarker Definitions and their Groups
    bio_defs_query = (
        select(BiomarkerDefinition, Unit.symbol.label("unit_symbol"))
        .outerjoin(Unit, BiomarkerDefinition.preferred_unit_id == Unit.id)
        .options(selectinload(BiomarkerDefinition.reference_ranges))
        # allowed_states is needed to (a) detect STATE biomarkers and (b)
        # resolve display + is_normal for the trends response. Without this
        # eager load, every state observation would be silently dropped
        # (the numeric-only branch below returns None for them).
        .options(
            selectinload(BiomarkerDefinition.allowed_states).selectinload(
                BiomarkerAllowedState.state
            )
        )
    )
    bio_defs_res = await db.execute(bio_defs_query)
    bio_defs_rows = bio_defs_res.all()
    bio_map_def = {}
    for b, symbol in bio_defs_rows:
        # Attach symbol to the object dynamically for easier access in loop
        b.preferred_unit_symbol = symbol
        bio_map_def[b.id] = b

    bio_slug_map = {b.slug: b for b in bio_map_def.values()}
    bio_name_map = {b.name.lower(): b for b in bio_map_def.values()}
    # Also include aliases in the name map for better matching
    for b in bio_map_def.values():
        if b.aliases:
            for alias in b.aliases:
                if alias.lower() not in bio_name_map:
                    bio_name_map[alias.lower()] = b

    # Stratified reference ranges (audit B9/F3): load the patient once so the
    # fallback range below can be resolved by sex/age instead of using the
    # single global definition range (which gave wrong status for anyone
    # outside the "default" demographic).
    _patient_ctx = None
    if patient_id:
        import uuid as _uuid

        from app.models.fhir import Patient

        try:
            _pid = _uuid.UUID(str(patient_id))
        except ValueError:
            _pid = None
        if _pid is not None:
            _patient_ctx = (
                await db.execute(select(Patient).where(Patient.id == _pid))
            ).scalar_one_or_none()

    # Fetch clinical groups via concept_edges (MEMBER_OF) — replaces the old
    # BiomarkerGroup/BiomarkerGroupMember tables. A biomarker may belong to one
    # or more panel concepts; we group by the destination concept's name.
    from app.models.concept_model import Concept, ConceptEdge
    from app.models.enums import (
        ConceptRelationType,
        EdgeApprovalStatus,
        EdgeEndpointType,
    )

    edges_res = await db.execute(
        select(ConceptEdge).where(
            ConceptEdge.src_type == EdgeEndpointType.BIOMARKER,
            ConceptEdge.relation == ConceptRelationType.MEMBER_OF,
            ConceptEdge.status == EdgeApprovalStatus.APPROVED,
        )
    )
    all_edges = edges_res.scalars().all()
    panel_ids = {e.dst_id for e in all_edges}

    panel_name_map: dict = {}
    if panel_ids:
        panel_res = await db.execute(
            select(Concept.id, Concept.name).where(Concept.id.in_(panel_ids))
        )
        panel_name_map = dict(panel_res.all())

    # Map biomarker_id to list of group (panel) names
    bio_to_groups = {}
    for edge in all_edges:
        g_name = panel_name_map.get(edge.dst_id)
        if g_name:
            bio_to_groups.setdefault(edge.src_id, []).append(g_name)

    # Optimize queries by fetching everything we need for exam info in one go
    # Only fetch doc_ids we actually care about
    doc_ids = list({obs.document_id for obs in observations if obs.document_id})
    exam_info_map = {}

    if doc_ids:
        from app.models.concept_model import Concept as _Concept

        # Do it in chunks if there are too many documents
        chunk_size = 500
        for i in range(0, len(doc_ids), chunk_size):
            chunk = doc_ids[i : i + chunk_size]
            doc_query = await db.execute(
                select(
                    DocumentModel.id,
                    ExaminationModel.id.label("exam_id"),
                    ExaminationModel.examination_date,
                    _Concept.name.label("exam_category"),
                    DocumentModel.entities,
                )
                .outerjoin(
                    ExaminationModel,
                    DocumentModel.examination_id == ExaminationModel.id,
                )
                .outerjoin(
                    _Concept,
                    ExaminationModel.category_concept_id == _Concept.id,
                )
                .where(DocumentModel.id.in_(chunk))
            )
            for row in doc_query.all():
                doc_id = row[0]
                exam_id = row[1]
                exam_date = row[2]
                exam_cat = row[3]
                entities = row[4]

                category = "other"
                if entities and "document_category" in entities:
                    from app.core.constants import DOCUMENT_CATEGORIES

                    cat_name = entities["document_category"].lower()
                    for cat in DOCUMENT_CATEGORIES:
                        if cat["name"].lower() in cat_name or cat["id"] in cat_name:
                            category = cat["id"]
                            break

                exam_info_map[doc_id] = {
                    "date": exam_date,
                    "category": category,
                    "exam_id": str(exam_id) if exam_id else None,
                    "exam_name": exam_cat or "General Visit",
                }

    trends = {}
    from datetime import datetime

    for obs in observations:
        name = obs.code.get("text", "Unknown")
        # Get definition info
        b_def = bio_map_def.get(obs.biomarker_id)

        # Use slug as key if available, otherwise fallback to lowercase name
        if b_def and b_def.slug:
            key = b_def.slug
        else:
            key = name.lower()
            # If we don't have a direct b_def by ID, try to find it by slug or name/alias
            if not b_def:
                # Try by slug
                b_def = bio_slug_map.get(key)
                if not b_def:
                    # Try by direct name/alias match
                    b_def = bio_name_map.get(key)

                # Try variants (e.g. "White Blood Cell (WBC)" -> "White Blood Cell")
                if not b_def and "(" in key:
                    stripped = re.sub(r"\(.*?\)", "", key).strip()
                    b_def = bio_name_map.get(stripped) or bio_slug_map.get(stripped)

                # Try stripping plural
                if not b_def and key.endswith("s"):
                    b_def = bio_name_map.get(key[:-1]) or bio_slug_map.get(key[:-1])

                if b_def:
                    # Update key to the official slug
                    key = b_def.slug

        # Prefer the canonical biomarker definition name (clean, standardized)
        # over the raw observation code.text, which may be unformatted lab/OCR
        # output (e.g. "GLUCOSE (FASTING)"). Keeps the list consistent with the
        # biomarker detail page, which shows the definition name.
        if b_def and b_def.name:
            name = b_def.name

        if key not in trends:
            trends[key] = []

        technical_category = b_def.category if b_def else "other"

        # Clinical Groups mapping with fallback to technical category
        clinical_groups = bio_to_groups.get(obs.biomarker_id) if obs.biomarker_id else None
        if not clinical_groups and b_def:
            clinical_groups = bio_to_groups.get(b_def.id)

        if not clinical_groups:
            if b_def and b_def.category:
                # Convert technical category (e.g. 'blood_laboratory') to readable (e.g. 'Blood Laboratory')
                fallback = b_def.category.replace("_", " ").replace("-", " ").title()
                clinical_groups = [fallback]
            else:
                clinical_groups = ["Uncategorized"]

        # STATE biomarkers carry their value in valueCodeableConcept (the
        # numeric columns are NULL by design — see fhir_service.create_observation).
        # Resolve to a (display, code, is_normal) tuple so the trends response
        # has the same richness as the QUANTITY path. Falls through to the
        # numeric branch when the coding can't be parsed (defensive — shouldn't
        # happen for validated writes).
        state_display: str | None = None
        state_code: str | None = None
        state_system: str | None = None
        state_is_normal: bool | None = None
        is_state_biomarker = (
            b_def is not None and getattr(b_def, "value_type", None) == BiomarkerValueType.STATE
        )
        if is_state_biomarker:
            from app.services.biomarkers.observation_value_validator import _extract_coding_pair

            pair = _extract_coding_pair(obs.value_codeable_concept)
            if pair is not None:
                state_code, state_system = pair
                # Resolve display + is_normal from the biomarker's allowed_states.
                for allowed in b_def.allowed_states or []:
                    if (
                        allowed.state is not None
                        and allowed.state.code == state_code
                        and allowed.state.system == state_system
                    ):
                        state_display = allowed.state.display or state_code
                        state_is_normal = bool(allowed.is_normal)
                        break
                if state_display is None:
                    # Fall back to the coding's own display, then the bare code.
                    coding = (
                        obs.value_codeable_concept.get("coding")
                        if isinstance(obs.value_codeable_concept, dict)
                        else None
                    )
                    if isinstance(coding, list) and coding and isinstance(coding[0], dict):
                        state_display = coding[0].get("display") or state_code
                    else:
                        state_display = state_code
                # Use the display string as ``val`` so downstream consumers
                # (KPI strip, history table) that expect a value get something
                # meaningful. The dedicated state_* fields carry the
                # machine-readable contract.
                val = state_display
            else:
                val = None
        else:
            val = getattr(obs, "normalized_value", None)
            if val is None:
                val = getattr(obs, "raw_value", None)
            if val is None and obs.value_quantity:
                val = obs.value_quantity.get("value")

        if val is not None:
            obs_date = obs.effective_datetime
            source_category = "other"
            exam_id = None
            exam_name = None

            # Get reference range
            ref_range_min = None
            ref_range_max = None
            ref_range_text = "--"

            # Current observation unit
            obs_unit_symbol = obs.value_quantity.get("unit", "") if obs.value_quantity else ""

            if (
                obs.reference_range
                and isinstance(obs.reference_range, list)
                and len(obs.reference_range) > 0
            ):
                low = obs.reference_range[0].get("low", {}).get("value")
                high = obs.reference_range[0].get("high", {}).get("value")
                ref_range_min = low
                ref_range_max = high
            elif b_def:
                # Fallback to global definition
                # Use smarter unit compatibility check
                pref_unit = getattr(b_def, "preferred_unit_symbol", None)
                units_match = False
                if pref_unit and obs_unit_symbol:
                    units_match = units_are_compatible(obs_unit_symbol, pref_unit)
                elif not pref_unit and not obs_unit_symbol:
                    # Both missing units
                    units_match = True

                # Leniency: If we matched the biomarker and the definition has a range,
                # we show it even if units don't perfectly match (best effort),
                # unless they are obviously different (e.g. mass vs molar).
                # For now, we allow it if the observation itself has no range.
                if units_match or (ref_range_min is None and ref_range_max is None):
                    # Stratified resolution (audit B9/F3): pick the most-specific
                    # reference range for this patient's sex/age/unit instead of
                    # the single global definition range. Falls back to the global
                    # range when no stratified row matches (resolver contract).
                    from app.services.biomarkers.reference_ranges import pick_reference_range

                    resolved = pick_reference_range(
                        b_def,
                        getattr(b_def, "reference_ranges", None) or [],
                        sex=getattr(_patient_ctx, "gender", None),
                        age=getattr(_patient_ctx, "age", None),
                        unit_id=getattr(obs, "raw_unit_id", None),
                    )
                    if resolved is not None:
                        ref_range_min = resolved.low
                        ref_range_max = resolved.high

            if ref_range_min is not None and ref_range_max is not None:
                ref_range_text = f"{ref_range_min} - {ref_range_max}"
            elif ref_range_min is not None:
                ref_range_text = f"> {ref_range_min}"
            elif ref_range_max is not None:
                ref_range_text = f"< {ref_range_max}"

            # Priority 1: Use direct examination_id if present
            exam_id = str(obs.examination_id) if obs.examination_id else None

            # Priority 2: Use document linkage if direct exam_id is missing
            if not exam_id and obs.document_id and obs.document_id in exam_info_map:
                info = exam_info_map[obs.document_id]
                exam_date = info["date"]
                source_category = info["category"]
                exam_id = info["exam_id"]
                exam_name = info["exam_name"]
                if exam_date:
                    obs_date = datetime.combine(exam_date, datetime.min.time(), tzinfo=UTC)
            elif exam_id:
                # If we have direct exam_id, we might still want the name from the map if it's there
                # Or we could fetch it, but for now we try to find it in the map
                for info in exam_info_map.values():
                    if info["exam_id"] == exam_id:
                        exam_name = info["exam_name"]
                        source_category = info["category"]
                        break

            # Determine source type and source name
            source_type = "unknown"
            source_name = "Manual Entry"
            source_id = None

            if exam_id:
                source_type = "examination"
                source_name = exam_name or "Clinical Examination"
            elif obs.document_id:
                source_type = "document"
                source_name = "Uploaded Document"
            elif obs.performer and isinstance(obs.performer, list) and len(obs.performer) > 0:
                p = obs.performer[0]
                if p.get("type") == "Integration":
                    source_type = "integration"
                    source_name = p.get("display") or "Integration"
                    ref = p.get("reference", "")
                    if ref.startswith("Integration/"):
                        source_id = ref.split("/")[1]
                    else:
                        source_id = source_name  # Fallback to domain if no UUID stored

            # For STATE biomarkers we already resolved (display, is_normal)
            # above; skip the numeric status computation (it would also try
            # to read obs.biomarker.allowed_states, which may not be eager-
            # loaded on the Observation relationship).
            if is_state_biomarker:
                status = (
                    "Normal"
                    if state_is_normal is True
                    else "Abnormal"
                    if state_is_normal is False
                    else "Unknown"
                )
            else:
                status = await _get_observation_status(name, val, obs, ref_range_min, ref_range_max)

            trends[key].append(
                {
                    "observation_id": str(obs.id),
                    "date": obs_date.isoformat() if obs_date else "",
                    "value": val,
                    "unit": obs.value_quantity.get("unit", "") if obs.value_quantity else "",
                    "name": name,
                    "status": status,
                    "biomarker_id": str(obs.biomarker_id)
                    if obs.biomarker_id
                    else (str(b_def.id) if b_def else None),
                    "reference_range_min": ref_range_min,
                    "reference_range_max": ref_range_max,
                    "reference_range_text": ref_range_text,
                    "source_type": source_type,
                    "source_name": source_name,
                    "source_id": source_id,
                    "source_category": source_category,
                    "technical_category": technical_category,
                    "clinical_groups": clinical_groups,
                    "examination_id": exam_id,
                    "examination_name": exam_name,
                    # STATE-only contract (mirrors the useBiomarkers
                    # observations path). QUANTITY rows leave these null so
                    # the frontend can branch on presence.
                    "value_type": "state" if is_state_biomarker else "quantity",
                    "state": state_code if is_state_biomarker else None,
                    "state_display": state_display if is_state_biomarker else None,
                    "state_system": state_system if is_state_biomarker else None,
                    "state_is_normal": state_is_normal if is_state_biomarker else None,
                }
            )

    # Telemetry Data Aggregation using TimescaleDB
    from datetime import datetime, timedelta

    from sqlalchemy import text

    now = datetime.now(UTC)

    PERIOD_MAPPING = {
        "last-1-hour": {"delta": timedelta(hours=1), "bucket": "1 minute"},
        "last-6-hours": {"delta": timedelta(hours=6), "bucket": "5 minutes"},
        "last-24-hours": {"delta": timedelta(days=1), "bucket": "15 minutes"},
        "last-7-days": {"delta": timedelta(days=7), "bucket": "1 hour"},
        "last-30-days": {"delta": timedelta(days=30), "bucket": "1 day"},
        "last-90-days": {"delta": timedelta(days=90), "bucket": "1 day"},
        "last-12-months": {"delta": timedelta(days=365), "bucket": "1 week"},
        "last-year": {"delta": timedelta(days=365), "bucket": "1 week"},
        "last-6-months": {"delta": timedelta(days=180), "bucket": "1 day"},
        "all-time": {"delta": timedelta(days=3650), "bucket": "1 month"},
    }

    config = PERIOD_MAPPING.get(period, PERIOD_MAPPING["last-6-months"])

    effective_start_date = start_date if start_date else (now - config["delta"])
    effective_end_date = end_date if end_date else now

    bucket = aggregation if aggregation else config["bucket"]
    if start_date and end_date and not aggregation:
        # Auto-calculate a sensible bucket if custom dates are provided without one
        delta_days = (effective_end_date - effective_start_date).days
        if delta_days <= 1:
            bucket = "15 minutes"
        elif delta_days <= 7:
            bucket = "1 hour"
        elif delta_days <= 90:
            bucket = "1 day"
        elif delta_days <= 365:
            bucket = "1 week"
        else:
            bucket = "1 month"

    telemetry_slugs = []
    telemetry_defs = {}
    for b in bio_map_def.values():
        if getattr(b, "is_telemetry", False):
            telemetry_slugs.append(b.slug)
            telemetry_defs[b.slug] = b

    if biomarker_codes:
        codes = [c.strip() for c in biomarker_codes.split(",")]
        import uuid

        uuid_codes = []
        text_codes = []
        for c in codes:
            try:
                uuid.UUID(c)
                uuid_codes.append(c)
            except ValueError:
                text_codes.append(c)

        if uuid_codes:
            # Resolve UUIDs to slugs for telemetry querying
            def_res = await db.execute(
                select(BiomarkerDefinition.slug).where(
                    BiomarkerDefinition.id.in_([uuid.UUID(u) for u in uuid_codes])
                )
            )
            text_codes.extend([s for s in def_res.scalars().all() if s])

        telemetry_to_query = [
            s for s in telemetry_slugs if any(c.lower() in s.lower() for c in text_codes)
        ]
    else:
        telemetry_to_query = telemetry_slugs

    for slug in telemetry_to_query:
        # Defense-in-depth: even though ``slug`` is now a bind parameter
        # (not an identifier/literal), refuse to query for obviously bogus
        # slugs. Schema validation should have prevented them at write time,
        # but rows may pre-date that guard or arrive via the AI pipeline /
        # import path.
        if not is_safe_slug(slug):
            logger.warning(
                "Skipping telemetry query for unsafe biomarker slug %r",
                slug,
            )
            continue

        # Long-format hypertable: pick the source table by requested bucket.
        # When the bucket exactly matches a continuous aggregate's resolution,
        # query the CAgg (pre-computed, fast on long horizons). Otherwise
        # fall back to the raw hypertable with live ``time_bucket_gapfill``.
        # The CAgg path covers every current and future telemetry biomarker
        # via ``WHERE slug = :slug`` — no per-metric branching.
        if bucket == "1 hour":
            table_name = "telemetry_hourly"
            time_col = "bucket"
            avg_expr = "AVG(avg_val)"
            max_expr = "MAX(max_val)"
            min_expr = "MIN(min_val)"
        elif bucket == "1 day":
            table_name = "telemetry_daily"
            time_col = "bucket"
            avg_expr = "AVG(avg_val)"
            max_expr = "MAX(max_val)"
            min_expr = "MIN(min_val)"
        elif bucket == "1 month":
            table_name = "telemetry_monthly"
            time_col = "bucket"
            avg_expr = "AVG(avg_val)"
            max_expr = "MAX(max_val)"
            min_expr = "MIN(min_val)"
        else:
            # Raw hypertable — single-level aggregation (no double-wrapping).
            table_name = "telemetry_data"
            time_col = "timestamp"
            avg_expr = "AVG(value)"
            max_expr = "MAX(value)"
            min_expr = "MIN(value)"

        # Validate the bucket interval against a strict whitelist.
        # The value is interpolated into INTERVAL '{bucket}' (PostgreSQL
        # INTERVAL literals don't accept bind parameters). The default comes
        # from PERIOD_MAPPING but an API caller can pass aggregation=...,
        # so we must guard against arbitrary strings here.
        safe_bucket = bucket if bucket in _ALLOWED_TELEMETRY_BUCKETS else "1 hour"

        sql = f"""
            SELECT
                time_bucket_gapfill(
                    INTERVAL '{safe_bucket}',
                    {time_col},
                    CAST(:start_date AS timestamptz),
                    CAST(:end_date AS timestamptz)
                ) AS bucket,
                device_id,
                {avg_expr} as avg_val,
                {max_expr} as max_val,
                {min_expr} as min_val
            FROM {table_name}
            WHERE tenant_id = CAST(:tenant_id AS uuid)
              AND {time_col} >= CAST(:start_date AS timestamptz)
              AND {time_col} <= CAST(:end_date AS timestamptz)
              AND slug = :slug
            GROUP BY 1, device_id
            ORDER BY 1
        """

        try:
            res = await db.execute(
                text(sql),
                {
                    "tenant_id": tenant_id,
                    "start_date": effective_start_date,
                    "end_date": effective_end_date,
                    "slug": slug,
                },
            )
            rows = res.all()

            if rows:
                if slug not in trends:
                    trends[slug] = []

                b_def = telemetry_defs[slug]
                technical_category = b_def.category if b_def else "other"

                clinical_groups = bio_to_groups.get(b_def.id) if b_def else None
                if not clinical_groups:
                    if b_def and b_def.category:
                        clinical_groups = [
                            b_def.category.replace("_", " ").replace("-", " ").title()
                        ]
                    else:
                        clinical_groups = ["Telemetry"]

                for row in rows:
                    bucket_date = row.bucket
                    device_id = row.device_id
                    avg_val = row.avg_val
                    max_val = row.max_val
                    min_val = row.min_val

                    if avg_val is None:
                        continue  # Gapfill returned null

                    trends[slug].append(
                        {
                            "date": bucket_date.isoformat() if bucket_date else "",
                            "value": round(avg_val, 2),
                            "max_value": round(max_val, 2),
                            "min_value": round(min_val, 2),
                            "unit": getattr(b_def, "preferred_unit_symbol", ""),
                            "name": b_def.name if b_def else slug,
                            "status": "Normal",
                            "biomarker_id": str(b_def.id) if b_def else None,
                            "reference_range_min": b_def.reference_range_min if b_def else None,
                            "reference_range_max": b_def.reference_range_max if b_def else None,
                            "reference_range_text": f"{b_def.reference_range_min} - {b_def.reference_range_max}"
                            if b_def and b_def.reference_range_min
                            else "--",
                            "source_type": "telemetry",
                            "source_name": device_id or "IoT Device",
                            "source_id": None,
                            "source_category": "telemetry",
                            "technical_category": technical_category,
                            "clinical_groups": clinical_groups,
                            "examination_id": None,
                            "examination_name": None,
                        }
                    )
        except Exception as e:
            logger.error(f"Failed to query telemetry data for '{slug}': {e}", exc_info=True)
            # If timescale is not perfectly configured or missing data, skip
            pass

    for key in trends:
        trends[key].sort(key=lambda x: x["date"])

    return {"biomarkers": trends}
