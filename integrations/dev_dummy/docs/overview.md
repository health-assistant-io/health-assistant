# Dev Dummy Integration

The `dev_dummy` integration is an internal tool designed specifically for developers. 

## Purpose
It simulates complex OAuth flows, data pulling, and webhook pushing without requiring real external API credentials. This allows developers to test the core integration engine (Celery syncing, config flows, validation logic) rapidly on local machines.

## Features
- Simulated OAuth authentication.
- Simulated API failures (to test retry logic).
- Automatic generation of fake biomarker data (e.g., heart rate and steps).
