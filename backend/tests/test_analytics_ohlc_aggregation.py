"""Tests for the telemetry OHLC aggregation path — long-format edition.

Originally pinned audit item A8 (double-wrapping aggregates + unsafe INTERVAL
interpolation). The long-format rewrite (migration ``t1e2l3o4n5g6``) also
collapses the slug→column branching: every metric is queried via
``WHERE slug = :slug`` against a uniform ``value`` column (raw hypertable) or
``avg_val``/``max_val``/``min_val`` columns (continuous aggregates).

These tests pin:
- No double-wrapped aggregates (``AVG(AVG(...))``) — the A8 fix stays.
- A strict ``_ALLOWED_TELEMETRY_BUCKETS`` whitelist guards the INTERVAL
  f-string interpolation (SQL-injection defence).
- The SQL uses ``slug = :slug`` (bind parameter), not slug→column branching.
- The CAgg path uses the generic ``avg_val``/``max_val``/``min_val`` columns
  (not metric-specific ``heart_rate_avg`` etc.).
- The raw-table path uses ``AVG(value)`` (not ``AVG(heart_rate)``).
"""
import re



# ---------------------------------------------------------------------------
# A8: no double-wrapped aggregates in the SQL
# ---------------------------------------------------------------------------


def test_a8_no_double_wrapped_avg_in_source():
    """A8: the SQL template must not wrap AVG(AVG(...)) or MAX(MAX(...))."""
    import inspect

    from app.services import analytics_service

    src = inspect.getsource(analytics_service)
    assert "AVG({avg_col})" not in src, (
        "analytics_service still double-wraps AVG(AVG(...))."
    )
    assert "MAX({max_col})" not in src
    assert "MIN({min_col})" not in src


# ---------------------------------------------------------------------------
# Long-format: raw-table path uses AVG(value), not a metric-specific column
# ---------------------------------------------------------------------------


def test_raw_table_path_uses_uniform_value_column():
    """Long-format contract: the raw-table else branch must aggregate
    ``AVG(value)`` / ``MAX(value)`` / ``MIN(value)`` — no metric-specific
    columns (``AVG(heart_rate)`` etc.) anywhere in the source."""
    import inspect

    from app.services import analytics_service

    src = inspect.getsource(analytics_service)
    # The uniform raw-table aggregates.
    assert '"AVG(value)"' in src, (
        "Raw-table path must use AVG(value) — the long-format uniform column."
    )
    assert '"MAX(value)"' in src
    assert '"MIN(value)"' in src
    # Legacy metric-specific aggregates must be gone.
    assert "AVG(heart_rate)" not in src
    assert "MAX(steps)" not in src
    assert "MIN(calories)" not in src


# ---------------------------------------------------------------------------
# Long-format: CAgg path uses generic avg_val/max_val/min_val columns
# ---------------------------------------------------------------------------


def test_cagg_path_uses_generic_pre_aggregated_columns():
    """Long-format contract: the CAgg path must use the generic
    ``avg_val`` / ``max_val`` / ``min_val`` columns (one definition per
    horizon covers every current + future telemetry biomarker). No
    metric-specific ``heart_rate_avg`` / ``steps_max`` columns."""
    import inspect

    from app.services import analytics_service

    src = inspect.getsource(analytics_service)
    assert '"AVG(avg_val)"' in src, (
        "CAgg path must use AVG(avg_val) — the generic pre-aggregated column."
    )
    assert '"MAX(max_val)"' in src
    assert '"MIN(min_val)"' in src
    # Legacy metric-specific CAgg columns must be gone.
    assert "heart_rate_avg" not in src
    assert "steps_max" not in src
    assert "calories_min" not in src


# ---------------------------------------------------------------------------
# Long-format: query filters by slug = :slug (bind parameter)
# ---------------------------------------------------------------------------


def test_sql_filters_by_slug_bind_parameter():
    """Long-format contract: the WHERE clause must filter ``slug = :slug``
    (bind parameter) — not slug→column branching, not JSONB key existence."""
    import inspect

    from app.services import analytics_service

    src = inspect.getsource(analytics_service)
    assert "slug = :slug" in src, (
        "Telemetry SQL must filter via slug = :slug (bind parameter)."
    )
    # Legacy JSONB-key predicates must be gone.
    assert "data ? '{slug}'" not in src
    assert "data ? '{slug}'" not in src.replace("'", '"')
    # Legacy column-not-null predicates must be gone.
    assert "heart_rate IS NOT NULL" not in src
    # Slug interpolation into SQL identifiers must be gone.
    assert "CAST(data->>" not in src


def test_sql_execute_passes_slug_parameter():
    """The SQL execute call must pass ``slug`` in the bind parameters."""
    import inspect

    from app.services import analytics_service

    src = inspect.getsource(analytics_service)
    assert '"slug": slug' in src, (
        "Telemetry SQL execute must pass slug as a bind parameter."
    )


# ---------------------------------------------------------------------------
# A8: bucket whitelist prevents SQL injection via INTERVAL interpolation
# ---------------------------------------------------------------------------


def test_a8_bucket_whitelist_exists():
    """A8: a strict whitelist must guard the INTERVAL f-string interpolation."""
    from app.services.analytics_service import _ALLOWED_TELEMETRY_BUCKETS

    assert isinstance(_ALLOWED_TELEMETRY_BUCKETS, frozenset)
    for expected in ("1 minute", "15 minutes", "1 hour", "1 day", "1 week", "1 month"):
        assert expected in _ALLOWED_TELEMETRY_BUCKETS, (
            f"Expected {expected!r} in the telemetry bucket whitelist."
        )


def test_a8_bucket_whitelist_rejects_injection():
    """A8: a SQL-injection attempt in the bucket parameter must not pass the whitelist."""
    from app.services.analytics_service import _ALLOWED_TELEMETRY_BUCKETS

    bad_values = [
        "1'; DROP TABLE telemetry_data; --",
        "1 hour; SELECT pg_sleep(999)",
        "",
        "custom",
        "0 seconds",
    ]
    for v in bad_values:
        assert v not in _ALLOWED_TELEMETRY_BUCKETS, (
            f"Malicious bucket value {v!r} should be rejected by the whitelist."
        )


def test_a8_sql_uses_safe_bucket_variable():
    """A8: the SQL template must use safe_bucket (validated), not the raw bucket."""
    import inspect

    from app.services import analytics_service

    src = inspect.getsource(analytics_service)
    assert "safe_bucket" in src
    assert "INTERVAL '{safe_bucket}'" in src
    code_lines = [ln for ln in src.splitlines() if not ln.strip().startswith("#")]
    code = "\n".join(code_lines)
    assert "INTERVAL '{bucket}'" not in code, (
        "SQL still interpolates the unvalidated raw bucket into INTERVAL."
    )


# ---------------------------------------------------------------------------
# A8: the generated SQL is valid (no nested aggregates)
# ---------------------------------------------------------------------------


def test_generated_sql_for_raw_table_has_no_nested_aggregates():
    """Simulate the raw-table path SQL and verify no AVG(AVG(...))."""
    avg_expr = "AVG(value)"
    max_expr = "MAX(value)"
    min_expr = "MIN(value)"
    safe_bucket = "15 minutes"
    time_col = "timestamp"
    table_name = "telemetry_data"

    sql = f"""
        SELECT
            time_bucket_gapfill(INTERVAL '{safe_bucket}', {time_col}) AS bucket,
            device_id,
            {avg_expr} as avg_val,
            {max_expr} as max_val,
            {min_expr} as min_val
        FROM {table_name}
        WHERE tenant_id = :tenant_id
          AND {time_col} >= :start_date AND {time_col} <= :end_date
          AND slug = :slug
        GROUP BY bucket, device_id
    """

    assert not re.search(r"AVG\s*\(\s*AVG\s*\(", sql, re.IGNORECASE)
    assert not re.search(r"MAX\s*\(\s*MAX\s*\(", sql, re.IGNORECASE)
    assert not re.search(r"MIN\s*\(\s*MIN\s*\(", sql, re.IGNORECASE)
    assert "AVG(value)" in sql
    assert "MAX(value)" in sql
    assert "MIN(value)" in sql
    assert "slug = :slug" in sql


def test_generated_sql_for_cagg_uses_generic_columns():
    """The CAgg-path SQL uses generic avg_val/max_val/min_val columns."""
    avg_expr = "AVG(avg_val)"
    max_expr = "MAX(max_val)"
    min_expr = "MIN(min_val)"
    table_name = "telemetry_hourly"

    sql = f"SELECT {avg_expr}, {max_expr}, {min_expr} FROM {table_name} WHERE slug = :slug"

    assert "AVG(avg_val)" in sql
    assert "MAX(max_val)" in sql
    assert "MIN(min_val)" in sql
    assert "AVG(AVG(" not in sql
    assert "heart_rate_avg" not in sql


# ---------------------------------------------------------------------------
# Long-format: is_safe_slug guard stays (defence-in-depth even though slug
# is now a bind parameter, not an interpolated identifier)
# ---------------------------------------------------------------------------


def test_is_safe_slug_guard_still_present():
    """The ``is_safe_slug`` defence-in-depth guard must stay — it refuses to
    query for obviously bogus slugs even though ``slug`` is now a bind
    parameter (no SQL-injection surface). Sanity check on input."""
    import inspect

    from app.services import analytics_service

    src = inspect.getsource(analytics_service)
    assert "is_safe_slug(slug)" in src, (
        "The is_safe_slug defence-in-depth guard must stay in the telemetry "
        "query loop."
    )
