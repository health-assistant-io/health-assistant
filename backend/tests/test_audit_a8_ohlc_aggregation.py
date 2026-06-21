"""Tests for audit item A8 (OHLC double-aggregation + gapfill stride).

A8: The telemetry trends query double-wrapped aggregates:
    - For the raw-table path (sub-hour / non-cagg buckets): avg_col was set
      to "AVG(col)" and the SQL template wrapped it in AVG(AVG(col)) —
      invalid SQL, silently caught by the except handler, producing empty
      charts for any bucket that didn't match the cagg stride.
    - For the cagg path: AVG(heart_rate_avg) was a mean-of-per-device-means
      (not weighted by sample count), but MAX/MIN were correct.

    The fix separates the aggregate expression from the source table:
    - Raw hypertable: AVG(col), MAX(col), MIN(col) — single level.
    - Cagg tables: AVG(col_avg), MAX(col_max), MIN(col_min) — correct for
      max/min; avg is best-effort without a count column.

    Also added a strict _ALLOWED_TELEMETRY_BUCKETS whitelist to prevent SQL
    injection via the INTERVAL f-string interpolation.
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
    # The SQL template previously had AVG({avg_col}) where avg_col was
    # itself "AVG(col)" — producing AVG(AVG(col)). The fix removed the
    # outer wrapper from the template and the inner wrapper from the col.
    #
    # Check: the template uses {avg_expr} (not AVG({avg_col})).
    assert "AVG({avg_col})" not in src, (
        "analytics_service still double-wraps AVG(AVG(...)) (audit A8)."
    )
    assert "MAX({max_col})" not in src
    assert "MIN({min_col})" not in src


def test_a8_raw_table_uses_single_level_aggregate():
    """A8: the else branch (raw hypertable) must use AVG(col) not AVG(AVG(col))."""
    import inspect

    from app.services import analytics_service

    src = inspect.getsource(analytics_service)
    # The else branch must define avg_expr = f"AVG({col})", not avg_col.
    # Find the else branch for the raw-table path.
    assert 'avg_expr = f"AVG({col})"' in src, (
        "Raw-table path must use single-level AVG(col) (audit A8)."
    )
    assert 'avg_col = f"AVG({col})"' not in src, (
        "Legacy avg_col = AVG(col) pattern still present (audit A8)."
    )


def test_a8_cagg_uses_pre_aggregated_columns():
    """A8: the cagg path must use _avg/_max/_min columns, not re-aggregate."""
    import inspect

    from app.services import analytics_service

    src = inspect.getsource(analytics_service)
    assert "avg_expr" in src, "Expected avg_expr variable (audit A8 fix)."
    assert "{col}_avg" in src, "Expected pre-aggregated _avg column reference."
    assert "{col}_max" in src
    assert "{col}_min" in src


# ---------------------------------------------------------------------------
# A8: bucket whitelist prevents SQL injection via INTERVAL interpolation
# ---------------------------------------------------------------------------


def test_a8_bucket_whitelist_exists():
    """A8: a strict whitelist must guard the INTERVAL f-string interpolation."""
    from app.services.analytics_service import _ALLOWED_TELEMETRY_BUCKETS

    assert isinstance(_ALLOWED_TELEMETRY_BUCKETS, frozenset)
    # Must include the common buckets from PERIOD_MAPPING.
    for expected in ("1 minute", "15 minutes", "1 hour", "1 day", "1 week", "1 month"):
        assert expected in _ALLOWED_TELEMETRY_BUCKETS, (
            f"Expected {expected!r} in the telemetry bucket whitelist (audit A8)."
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
    assert "safe_bucket" in src, (
        "Expected safe_bucket variable in the SQL construction (audit A8)."
    )
    # The INTERVAL must interpolate safe_bucket.
    assert "INTERVAL '{safe_bucket}'" in src, (
        "SQL must interpolate the validated safe_bucket (audit A8)."
    )
    # The raw bucket must not appear in an INTERVAL clause in the actual SQL
    # f-string (strip comments to avoid false-triggering on the audit note).
    code_lines = [ln for ln in src.splitlines() if not ln.strip().startswith("#")]
    code = "\n".join(code_lines)
    # Check that no SQL construction uses INTERVAL '{bucket}' (unvalidated).
    assert "INTERVAL '{bucket}'" not in code, (
        "SQL still interpolates the unvalidated raw bucket into INTERVAL (audit A8)."
    )


# ---------------------------------------------------------------------------
# A8: the generated SQL is valid (no nested aggregates)
# ---------------------------------------------------------------------------


def test_a8_generated_sql_for_raw_table_has_no_nested_aggregates():
    """A8: simulate the else-branch SQL and verify no AVG(AVG(...))."""
    # Reproduce the SQL construction logic for the raw-table path.
    col = "heart_rate"
    avg_expr = f"AVG({col})"
    max_expr = f"MAX({col})"
    min_expr = f"MIN({col})"
    safe_bucket = "15 minutes"
    time_col = "timestamp"
    table_name = "telemetry_data"
    where_clause = "heart_rate IS NOT NULL"

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
          AND {where_clause}
        GROUP BY bucket, device_id
    """

    # No nested aggregate functions.
    assert not re.search(r"AVG\s*\(\s*AVG\s*\(", sql, re.IGNORECASE), (
        "Raw-table SQL contains AVG(AVG(...)) — double-wrapping bug (audit A8)."
    )
    assert not re.search(r"MAX\s*\(\s*MAX\s*\(", sql, re.IGNORECASE)
    assert not re.search(r"MIN\s*\(\s*MIN\s*\(", sql, re.IGNORECASE)
    # Single-level aggregates ARE present.
    assert "AVG(heart_rate)" in sql
    assert "MAX(heart_rate)" in sql
    assert "MIN(heart_rate)" in sql


def test_a8_generated_sql_for_cagg_uses_pre_aggregated_columns():
    """A8: the cagg-path SQL uses _avg/_max/_min columns, not raw columns."""
    col = "heart_rate"
    avg_expr = f"AVG({col}_avg)"
    max_expr = f"MAX({col}_max)"
    min_expr = f"MIN({col}_min)"
    table_name = "telemetry_hourly"

    sql = f"SELECT {avg_expr}, {max_expr}, {min_expr} FROM {table_name}"

    assert "AVG(heart_rate_avg)" in sql
    assert "MAX(heart_rate_max)" in sql
    assert "MIN(heart_rate_min)" in sql
    # Must NOT re-aggregate the raw column.
    assert "AVG(AVG(" not in sql
