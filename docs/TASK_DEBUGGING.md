# Task Debugging & Monitoring System — Implementation Reference

> **Scope of this doc:** implementation reference for the TaskLogger /
> TaskProgressTracker / TaskTimeoutMonitor internals (`backend/app/workers/task_logger.py`),
> the structured-log JSON schema, the monitoring-metric heuristics, and the security
> redaction rules. **Internal** (not in `docs-tree.json`).
>
> **For the operator-facing troubleshooting workflow** — "I have a stuck document, what
> do I click / curl?" — read [TASK_DEBUGGING_GUIDE.md](TASK_DEBUGGING_GUIDE.md) instead.
> That guide is a 6-step reference (UI → worker health → AI config → logs → retry → DB)
> with a symptom→cause→fix table; this doc complements it by documenting the underlying
> components.
>
> **Status:** historical (dated March 17, 2026). The components and contracts described
> here are still accurate; the surrounding thresholds (10-min stall badge / 15-min Celery
> hard limit / 20-min `cleanup_stuck_extractions` beat) are authoritative in
> [TASK_DEBUGGING_GUIDE.md](TASK_DEBUGGING_GUIDE.md) and the codebase, not here.

## Overview

The debugging + monitoring system for Celery tasks provides structured logging, error
tracking, task retry functionality, and a monitoring UI — following security best
practices.

## Components Created

### 1. Task Logger Utility (`backend/app/workers/task_logger.py`)

**Security-Focused Design:**
- No API keys or credentials in logs
- Structured JSON format for parsing
- Error categorization for monitoring
- Tenant isolation in log context

**Classes:**

#### TaskLogger
- Structured logger for Celery tasks
- Auto-redacts sensitive data (api_key, token, secret, etc.)
- Truncates long strings (>100 chars)
- Logs: start, progress, success, error
- Categorizes errors: configuration, file, api, validation, system

#### TaskProgressTracker
- Updates database status in real-time
- Tracks document and examination status
- Marks failed tasks with error messages

#### TaskTimeoutMonitor
- Detects stalled tasks (>5 minutes default)
- Prevents infinite loops and resource exhaustion
- Returns remaining seconds before timeout

### 2. Task Monitor API (`backend/app/api/v1/endpoints/task_monitor.py`)

> All endpoints are **tenant-scoped** (audit B1): a non-`SYSTEM_ADMIN`
> caller only sees rows whose `tenant_id` matches their token. Cross-
> tenant retry calls return `404` (no information leak). `SYSTEM_ADMIN`
> is the deliberate operator-visibility exception and bypasses the
> tenant filter.

**Endpoints:**

#### GET `/task-monitor/documents/processing`
- Get documents stuck in processing
- Filters: patient_id, status, limit
- Returns: id, tenant_id, filename, status, progress, age_minutes, error_message
- Security: Authenticated, tenant-scoped (or SYSTEM_ADMIN), no sensitive data

#### GET `/task-monitor/examinations/processing`
- Get examinations stuck in extraction
- Filters: patient_id, status, limit
- Returns: id, tenant_id, category, status, progress, age_minutes
- Security: Authenticated, tenant-scoped (or SYSTEM_ADMIN), aggregate metadata only

#### POST `/task-monitor/documents/retry/{document_id}`
- Retry OCR for failed document
- Validates document exists in caller's tenant (404 otherwise)
- Only allows retry for failed/processing docs
- Resets status to "uploaded" to trigger retry

#### POST `/task-monitor/examinations/retry/{examination_id}`
- Retry examination extraction
- Validates examination exists in caller's tenant (404 otherwise)
- Resets status to trigger retry

#### GET `/task-monitor/stats`
- System health statistics
- Document/examination counts by status (tenant-scoped; global for SYSTEM_ADMIN)
- Stalled task detection (>10 minutes for the UI badge; the periodic
  cleanup task uses a 20-minute threshold — see §6 below)
- Aggregate data only, no sensitive info

### 3. Task Manager UI (`frontend/src/pages/TaskManager.tsx`)

**Features:**
- Statistics dashboard (documents, examinations, system health)
- Processing documents table with:
  - Filename, status, progress bar, age, error message
  - Filter by status, search by filename
  - Retry button for failed/processing docs
- Processing examinations table with:
  - Category, status, progress bar, age
  - Filter by status, search by category
  - Retry button for failed/processing exams
- Auto-refresh, manual refresh button
- Stalled task warnings (>10 minutes)

**Access:** `/task-monitor` route

### 4. Enhanced OCR Task Logging (`backend/app/workers/tasks.py`)

**Updates:**
- Uses TaskLogger for structured logging
- TaskProgressTracker for status updates
- TaskTimeoutMonitor for stall detection
- Proper error handling with database updates
- Logs: config_loaded, path_found, dicom_processed, ocr_start, ocr_completed
- Error categories: config_check, file_check, timeout, dicom_processing

**Security:**
- No file paths >100 chars logged
- No API keys logged
- Error messages truncated (200 chars)
- Tenant ID in log context

## Usage

### 1. Monitor Processing Tasks

**Navigate to:** `/task-monitor`

**View:**
- Documents stuck in processing
- Examinations stuck in extraction
- Stalled tasks (>10 minutes highlighted)
- Error messages for failed tasks

### 2. Retry Failed Tasks

**Documents:**
```bash
POST /api/v1/task-monitor/documents/retry/{document_id}
Authorization: Bearer {token}
```

**Examinations:**
```bash
POST /api/v1/task-monitor/examinations/retry/{examination_id}
Authorization: Bearer {token}
```

### 3. Check Logs

**Celery logs:** Structured JSON format
```json
{
  "timestamp": "2026-03-17T10:30:00Z",
  "level": "ERROR",
  "task_name": "ocr_document",
  "task_id": "uuid",
  "tenant_id": "uuid",
  "message": "Error in file_check",
  "data": {
    "error_type": "file",
    "error_class": "FileNotFoundError",
    "error_message": "File not found..."
  },
  "duration_seconds": 2.5
}
```

**Filter logs:**
```bash
grep "ERROR" /var/log/celery.log | grep "task_name=ocr_document"
```

### 4. Check Database Status

```sql
-- Documents stuck in processing
SELECT id, filename, status, progress, created_at, error_message
FROM documents
WHERE status IN ('processing', 'uploaded')
ORDER BY created_at DESC;

-- Examinations stuck in extraction
SELECT id, category, extraction_status, extraction_progress, created_at
FROM examinations
WHERE extraction_status IN ('processing', 'aggregating', 'analyzing_text')
ORDER BY created_at DESC;
```

## Security Best Practices

### 1. Data Sanitization
- API keys, tokens, secrets redacted
- Long strings truncated
- No raw file content logged

### 2. Authentication
- All `/task-monitor/*` endpoints require authentication.
- **Tenant isolation maintained**: every list/stats query carries a `.where(Model.tenant_id == current_user.tenant_id)` predicate; retry calls fetch by `id AND tenant_id` (cross-tenant → 404, no leak).
- **`SYSTEM_ADMIN` bypasses** the tenant filter (operator visibility — they monitor the whole platform).
- See [API.md → Task Monitoring](API.md#task-monitoring-tenant-scoped-except-system_admin-b1).

### 3. Error Handling
- Error messages truncated (200 chars)
- No stack traces exposed
- Error categorization for monitoring

### 4. Access Control
- Task manager UI requires auth
- Retry actions validated
- No sensitive data exposure

## Debugging Workflow

### When Task is Stuck:

> Two thresholds are at play:
> - **UI stall badge:** the Task Manager UI highlights tasks older than **10 minutes** (`TaskTimeoutMonitor(max_duration_seconds=300)` plus the page's polling delay). This is purely visual — older than 10 min doesn't mean dead.
> - **Periodic cleanup (`cleanup_stuck_extractions`):** marks exams `failed` after **20 minutes** of inactivity (`updated_at < now - 20 min`). The 20-minute threshold is intentionally 5 minutes beyond the Celery hard `task_time_limit=900s` (15 min) so a task killed at exactly 15 min doesn't race with cleanup. The startup cleanup in `main.lifespan` uses the same 20-minute `updated_at` filter so rolling restarts don't kill in-flight exams (audits A5 + A6).

1. **Check Task Monitor UI** (`/task-monitor`)
   - Find stuck document/examination
   - Note error message
   - Check age (>10 minutes = stalled; >20 minutes = will be auto-failed on the next cleanup beat)

2. **Retry Task**
   - Click "Retry" button in UI
   - Or call API endpoint
   - Status resets to trigger retry

3. **Check Logs**
   - Search structured logs
   - Identify error category
   - Fix root cause (config, file path, etc.)

4. **Verify Fix**
   - Monitor progress bar
   - Check status updates
   - Confirm completion

## Common Issues & Solutions

### 1. OCR Provider Not Configured
**Error:** "OCR provider configuration required"
**Solution:** Configure AI providers in Settings > AI Configuration

### 2. File Not Found
**Error:** "File {path} not found"
**Solution:** Check uploads directory, verify file exists

### 3. DICOM Processing Failed
**Error:** "DICOM error: ..."
**Solution:** Check pydicom installation, verify DICOM file valid

### 4. Task Timeout
**Error:** "Task timeout after 300s"
**Solution:** Check file size, OCR processor performance, increase timeout if needed

## Monitoring Metrics

### Health Indicators:
- Processing count (should be low)
- Stalled count (should be 0)
- Age distribution (most <5 minutes)
- Error rate (should be <5%)

### Alert Thresholds:
- Stalled tasks > 0 → Investigate
- Processing age > 10 min → Retry
- Error rate > 10% → Check config
- Timeout rate > 5% → Optimize

## Files Modified/Created

### Created:
- `backend/app/workers/task_logger.py` - Logging utility
- `backend/app/api/v1/endpoints/task_monitor.py` - Monitor API
- `frontend/src/pages/TaskManager.tsx` - Monitor UI
- `docs/TASK_DEBUGGING.md` - Documentation

### Updated:
- `backend/app/workers/tasks.py` - Enhanced OCR logging
- `backend/app/api/v1/__init__.py` - Register task_monitor router
- `frontend/src/App.tsx` - Add task-monitor route

## Next Steps

### Enhancements:
1. WebSocket for real-time updates
2. Task history/audit log
3. Performance metrics (avg duration)
4. Automated retry policies
5. Slack/email alerts for stalls

### Optimization:
1. Batch status updates
2. Log aggregation (ELK stack)
3. Log retention policies
4. Dashboard analytics

## Conclusion

The debugging system provides structured visibility into task processing with security-focused design. Users can monitor, diagnose, and retry stuck tasks through the UI or API, while structured logging enables root cause analysis.