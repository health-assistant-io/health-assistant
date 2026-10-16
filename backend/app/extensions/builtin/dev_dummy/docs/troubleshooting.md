# Troubleshooting

If Dev Dummy stops producing data, walk through these checks in order:

1. **System enablement** — verify the integration is enabled at the system level (admin → integrations, or check the `system_integrations` table for `is_enabled = true` on the `dev_dummy` domain).
2. **Per-instance status** — the instance's `status` column on `user_integrations` should be `ACTIVE`. If it's `ERROR`, the provider raised `IntegrationAuthError` (toggle off `simulate_auth_error` in the config to resume).
3. **Sync cadence** — the Celery beat task `sync_active_integrations` runs every 60 seconds, but each instance only actually syncs when its own `sync_interval` (config-flow field, default 15 minutes) elapses.
4. **Worker logs** — look for `[dev_dummy]` log entries. Enable the UI Debug toggle on the instance to get `IntegrationDebugLog` rows with the raw payloads.
5. **Capabilities off** — every capability is gated by a config toggle. Open the instance config and confirm the ones you expect are on. The **Show Status** custom action lists what's currently enabled.
6. **Forced manual sync** — `POST /api/v1/integrations/dev_dummy/sync` triggers an out-of-cycle sync regardless of `sync_interval`. Useful for iterating during development.
7. **Cursor reset** — if a delta-sync cursor is stuck in the future, the **Reset Sync Cursor** custom action (or `POST /api/v1/integrations/dev_dummy/api/{integration_id}/reset`) clears it.
