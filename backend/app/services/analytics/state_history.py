"""Biomarker state history (per-state and multi-state timelines)."""

from app.services.analytics.types import (
    Any,
    AsyncSession,
    AsyncSessionLocal,
    BiomarkerAllowedState,
    BiomarkerDefinition,
    Observation,
    or_,
    select,
    selectinload,
)

# ---------------------------------------------------------------------------
# State biomarker history (plan state-biomarkers Step 7)
# ---------------------------------------------------------------------------


async def get_biomarker_state_history(
    tenant_id: str,
    patient_id: str,
    slug: str,
    *,
    limit: int = 500,
    db: AsyncSession | None = None,
) -> list[dict[str, Any]]:
    """Chronological state history for a single-state biomarker.

    Returns a list of ``{timestamp, state_code, state_system, display,
    is_normal, observation_id}`` sorted ascending by timestamp. Powers the
    frontend state-timeline component (step/stair chart of categorical
    results over time).

    No-op (empty list) for QUANTITY biomarkers — the numeric trends live in
    :func:`get_biomarker_trends`.
    """
    from app.services.biomarkers.observation_value_validator import _extract_coding_pair

    session = db
    if session is None:
        async with AsyncSessionLocal() as new_session:
            return await get_biomarker_state_history(
                tenant_id, patient_id, slug, limit=limit, db=new_session
            )

    # Resolve the biomarker definition (global + tenant scope).
    bio = (
        await session.execute(
            select(BiomarkerDefinition)
            .where(
                BiomarkerDefinition.slug == slug,
                or_(
                    BiomarkerDefinition.tenant_id.is_(None),
                    BiomarkerDefinition.tenant_id == tenant_id,
                ),
            )
            .options(
                selectinload(BiomarkerDefinition.allowed_states).selectinload(
                    BiomarkerAllowedState.state
                )
            )
        )
    ).scalar_one_or_none()
    if bio is None or bio.value_type != "state":
        return []

    # Build the (code, system) → is_normal lookup.
    pair_to_normal = {}
    pair_to_display = {}
    for allowed in bio.allowed_states or []:
        if allowed.state is None:
            continue
        pair = (allowed.state.code, allowed.state.system)
        pair_to_normal[pair] = allowed.is_normal
        pair_to_display[pair] = allowed.state.display

    observations = (
        (
            await session.execute(
                select(Observation)
                .where(
                    Observation.tenant_id == tenant_id,
                    Observation.patient_id == patient_id,
                    Observation.biomarker_id == bio.id,
                )
                .order_by(Observation.effective_datetime.asc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )

    history = []
    for obs in observations:
        pair = _extract_coding_pair(obs.value_codeable_concept)
        if pair is None:
            continue
        history.append(
            {
                "timestamp": obs.effective_datetime.isoformat() if obs.effective_datetime else None,
                "state_code": pair[0],
                "state_system": pair[1],
                "display": pair_to_display.get(pair, pair[0]),
                "is_normal": pair_to_normal.get(pair, False),
                "observation_id": str(obs.id),
            }
        )
    return history


async def get_multi_state_history(
    tenant_id: str,
    patient_id: str,
    slug: str,
    *,
    limit: int = 500,
    db: AsyncSession | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Per-component state history for a multi-state biomarker.

    Returns ``{component_code: [{timestamp, state_code, state_system,
    display, is_normal, observation_id}, ...]}`` — one timeline per
    sub-context (e.g. per organism in a microbiology panel). Each component
    list is sorted ascending by timestamp. Powers the multi-track swimlane
    UI.
    """
    from app.services.biomarkers.observation_value_validator import (
        _extract_coding_pair,
        _normalize_component_value,
    )

    session = db
    if session is None:
        async with AsyncSessionLocal() as new_session:
            return await get_multi_state_history(
                tenant_id, patient_id, slug, limit=limit, db=new_session
            )

    bio = (
        await session.execute(
            select(BiomarkerDefinition)
            .where(
                BiomarkerDefinition.slug == slug,
                or_(
                    BiomarkerDefinition.tenant_id.is_(None),
                    BiomarkerDefinition.tenant_id == tenant_id,
                ),
            )
            .options(
                selectinload(BiomarkerDefinition.allowed_states).selectinload(
                    BiomarkerAllowedState.state
                )
            )
        )
    ).scalar_one_or_none()
    if bio is None or not bio.supports_multi_state:
        return {}

    pair_to_normal = {}
    pair_to_display = {}
    for allowed in bio.allowed_states or []:
        if allowed.state is None:
            continue
        pair = (allowed.state.code, allowed.state.system)
        pair_to_normal[pair] = allowed.is_normal
        pair_to_display[pair] = allowed.state.display

    observations = (
        (
            await session.execute(
                select(Observation)
                .where(
                    Observation.tenant_id == tenant_id,
                    Observation.patient_id == patient_id,
                    Observation.biomarker_id == bio.id,
                )
                .order_by(Observation.effective_datetime.asc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )

    tracks: dict[str, list[dict[str, Any]]] = {}
    for obs in observations:
        ts = obs.effective_datetime.isoformat() if obs.effective_datetime else None
        for comp in obs.component or []:
            comp_code_obj, value_cc = _normalize_component_value(comp)
            if comp_code_obj is None or value_cc is None:
                continue
            # Derive a stable component key from the code.coding[0].code.
            comp_key = (
                comp_code_obj.get("coding", [{}])[0].get("code")
                if isinstance(comp_code_obj, dict)
                else None
            ) or "unknown"
            pair = _extract_coding_pair(value_cc)
            if pair is None:
                continue
            tracks.setdefault(comp_key, []).append(
                {
                    "timestamp": ts,
                    "state_code": pair[0],
                    "state_system": pair[1],
                    "display": pair_to_display.get(pair, pair[0]),
                    "is_normal": pair_to_normal.get(pair, False),
                    "observation_id": str(obs.id),
                }
            )
    return tracks
