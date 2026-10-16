# Dev Dummy — Testing Scenarios

## 1. Unit tests

The unit suite at `backend/tests/test_dev_dummy_provider.py` exercises every capability directly against `DevDummyProvider` and `DevDummyConfigFlow` — no DB or HTTP required. Run it with:

```bash
cd backend
source venv/bin/activate
pytest tests/test_dev_dummy_provider.py -v
```

Coverage map is in the test module docstring (one test per capability §A–§P, plus regression guards for the previously-dead `_latest_numeric` and sensor-malfunction branches).

## 2. Manual end-to-end via the in-app UI

1. Enable the `dev_dummy` system integration (admin → integrations).
2. Create a new `dev_dummy` instance. Pick a name, a sync interval, and toggle on whichever capabilities you want to exercise.
3. Hit **Sync now**. The next sync cycle will:
   - Emit observations for every metric you enabled.
   - Fire notifications for any threshold breaches / sensor glitches.
   - Create the demo clinical event + examination.
   - Auto-apply the "Sleep Quality Score" biomarker proposal.
   - Queue the "Stress Index" concept for review (resolve from the Proposals tab).
   - Ingest one text "lab report" document.
4. Use the **Show Status** custom action to see the live cursor + capability list.

## 3. Triggering the error paths

The config-flow has three error-simulation toggles:

| Toggle | Effect |
|---|---|
| `simulate_auth_error` | Next sync raises `IntegrationAuthError` → instance status flips to `ERROR`. |
| `simulate_rate_limit` | Next sync raises `IntegrationRateLimitError` → sync skipped, status unchanged. |
| `simulate_sensor_glitch` | 5% chance per sync of an HR reading > 200 → fires the critical "sensor malfunction" notification. |

## 4. Webhook contract

The platform provisions a tokenless endpoint per instance:

```
POST /api/v1/integrations/dev_dummy/webhook/{integration_id}
```

The UUID in the URL *is* the secret. If `webhook_secret` is set in the config, `handle_webhook` additionally requires an `X-DevDummy-Signature` header equal to `HMAC-SHA256(secret, raw_body)` (hex-encoded). Send a JSON body like:

```json
{
  "metrics": [
    {"code": "8867-4", "value": 72, "unit": "bpm"},
    {"code": "94500-6", "value_string": "POSITIVE"}
  ]
}
```

## 5. Two-way API contract

Headless clients can hit the wildcard route (default auth: UUID-as-secret; optional HMAC via `user_config['api_secret']`):

```
[GET|POST] /api/v1/integrations/dev_dummy/api/{integration_id}/{path}
```

Implemented paths: `GET status`, `GET cursor`, `POST reset`, `POST echo`.
