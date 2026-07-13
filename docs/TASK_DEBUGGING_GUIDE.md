---
title: "Task Debugging Guide — Health Assistant"
description: "Debugging guide for stuck documents and OCR extraction tasks in Health Assistant, a self-hosted health records platform. Diagnose failed pipeline stages and recover data."
---

# Task Debugging Guide — Stuck Documents

**Date**: March 17, 2026

## Problem: Documents Stuck in "Processing" Status

You have 3 documents stuck in processing status for hours without error messages:
- `medical-arthritis4(4).jpg` - 149 minutes
- `medical-eyes-results-1.pdf` - 632 minutes  
- `medical-eyes-results-1.pdf` - 662 minutes

All show:
- Status: `processing` or `uploaded`
- Progress: `0%` or `10%`
- Error message: `None` (empty)

## Why This Happens

Documents get stuck without error messages when:

1. **OCR API Call Hanging**
   - The OCR provider API is slow or unresponsive
   - Network timeout not configured
   - API key invalid but not returning error

2. **File Path Issues**
   - File moved or deleted after upload
   - Permissions prevent reading file
   - Path constructed incorrectly

3. **Celery Worker Crash**
   - Worker process died during task
   - Memory exhaustion
   - Uncaught exception

4. **Configuration Missing**
   - OCR provider not configured (but you have it configured ✅)
   - Model settings invalid

## How to Fix

### Step 1: Use Task Manager UI

Navigate to: `http://localhost:3000/task-monitor`

**What you'll see:**
- Table of stuck documents
- Age indicator (red "Stalled" badge if >60 min)
- Error column (shows "-" if no error logged)
- "Retry OCR" button

**Action:**
1. Click "Retry OCR" button for each stuck document
2. Monitor progress bar (should move from 0% → 10% → 100%)
3. Wait 1-5 minutes for completion

### Step 2: Check OCR Provider Status

```bash
# Test OCR configuration
curl -X GET http://localhost:8000/api/v1/ai-config/default-for-task/ocr \
  -H "Authorization: Bearer YOUR_TOKEN"
```

**Expected:**
```json
{
  "provider": {"name": "OpenAI Production"},
  "model": {"name": "Gemini 3.1 Flash Lite"},
  "max_tokens": 4096
}
```

✅ You have this configured correctly.

### Step 3: Monitor Celery Logs

```bash
# Watch Celery task execution
journalctl -f -u celery 2>&1 | grep -i "ocr_document"

# Or check log file
cat /var/log/celery.log | grep -i "ocr_document" | tail -20
```

**Look for:**
- Task start: `Task started`
- Progress: `Progress: ocr_start`
- Errors: `ERROR` messages
- Completion: `Task completed successfully`

### Step 4: Manual Retry via API

```bash
# Login to get token
curl -X POST http://localhost:8000/api/v1/auth/login \
  -d "username=admin@health-assistant.local" -d "password=admin123"

# Retry stuck document
curl -X POST http://localhost:8000/api/v1/task-monitor/documents/retry/DOCUMENT_ID \
  -H "Authorization: Bearer YOUR_TOKEN"
```

**Response:**
```json
{"message": "Document OCR will be retried", "document_id": "..."}
```

### Step 5: Check Database Status

```python
# Connect to DB and check status
from app.core.database import AsyncSessionLocal
from app.models.document_model import DocumentModel
from sqlalchemy import select
import asyncio

async def check():
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(DocumentModel).where(DocumentModel.status == 'processing')
        )
        docs = result.scalars().all()
        for doc in docs:
            print(f'{doc.filename}: {doc.status}, {doc.progress}%')

asyncio.run(check())
```

## Improved Error Logging

The new `TaskLogger` utility (in `backend/app/workers/task_logger.py`) now captures:

- **Structured JSON logs** - Easy to parse
- **Error categorization** - config, file, api, validation, system
- **Progress tracking** - Start, progress, success, error
- **Timeout detection** - Auto-fail after 5 minutes
- **Sanitized data** - No API keys logged

## Future Prevention

### 1. Timeout Configuration
Tasks now auto-fail after 5 minutes to prevent infinite hanging.

### 2. Progress Monitoring
Task Manager UI shows:
- Age in minutes
- Stalled badge (>60 min)
- Progress bar
- Error messages

### 3. Automatic Retry
"Retry OCR" button resets status and triggers new task.

### 4. Better Logging
All OCR tasks now log:
- Start time
- Each stage (config_loaded, path_found, ocr_start, etc.)
- Error category
- Duration

## Quick Fix Commands

```bash
# 1. Retry all stuck documents
curl -X POST http://localhost:8000/api/v1/task-monitor/documents/retry/DOC_ID1 \
  -H "Authorization: Bearer TOKEN"
curl -X POST http://localhost:8000/api/v1/task-monitor/documents/retry/DOC_ID2 \
  -H "Authorization: Bearer TOKEN"

# 2. Check status
curl -X GET http://localhost:8000/api/v1/task-monitor/documents/processing \
  -H "Authorization: Bearer TOKEN"

# 3. Monitor Celery
journalctl -f -u celery | grep "ocr_document"
```

## Expected Timeline

After clicking "Retry OCR":
- **0-30 seconds**: Task starts, progress → 10%
- **30-60 seconds**: OCR processing, progress → 50%
- **60-120 seconds**: OCR completes, progress → 100%
- **Status**: Changes to "completed"

If it fails again:
1. Check Celery logs for error
2. Verify file exists in uploads directory
3. Test OCR API directly
4. Restart Celery worker: `systemctl restart celery`

## Summary

**Current State:**
- 3 documents stuck (0.5-11 hours)
- No error messages logged
- OCR provider configured ✅
- Celery running ✅

**Action:**
1. Go to `/task-monitor`
2. Click "Retry OCR" for each document
3. Wait 2 minutes
4. Check if status changes to "completed"

**If still stuck:**
- Check Celery logs
- Verify file paths
- Restart Celery worker
- Contact support with log output