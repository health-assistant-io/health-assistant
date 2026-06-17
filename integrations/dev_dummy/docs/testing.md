# Testing Scenarios

Use this integration to trigger the following core system behaviors:

1. **Config Flow Rejection:** Pass invalid credentials during setup to test the frontend's error rendering.
2. **Sync Failures:** The dummy integration has a built-in random failure rate (usually 10%) during `pull_data()` to ensure Celery task retries function correctly.
3. **Data Throttling:** Ensure the system properly handles rate-limit exceptions.