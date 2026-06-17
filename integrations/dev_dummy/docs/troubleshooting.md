# Troubleshooting

If the Dummy Integration stops generating data:
1. Verify the integration is marked as `is_enabled=True` in the `system_integrations` table.
2. Check the Celery worker logs. The `sync_active_integrations` task runs every 15 minutes.
3. If data is still missing, trigger a manual sync via the `/api/v1/integrations/{domain}/sync` REST endpoint.