# Health Assistant — Mobile Sync Architecture

> **Status: RFC / design proposal — NOT implemented.** This document describes
> a hypothetical standalone React Native companion app. **No such application
> exists in this repository** — there is no `mobile/` source tree, no
> `wearable_service.py`, no `POST /api/v1/wearable/data` endpoint, and no
> `WearableSyncPayload` / `WearableDataPoint` schema. The historical
> `wearable_data` table was renamed to `telemetry_data` and is now part of the
> frequency-based routing architecture (see
> [TELEMETRY_AND_AGGREGATION.md](TELEMETRY_AND_AGGREGATION.md)).
>
> The **actual** mobile-bridge surface in the codebase is the
> [`health_assistant_bridge`](../integrations/health_assistant_bridge/)
> integration, which ships Python + TypeScript SDKs for pushing observations
> into a self-hosted instance via the standard Integrations Framework (see
> [INTEGRATIONS_FRAMEWORK.md](INTEGRATIONS_FRAMEWORK.md) and
> [INTEGRATIONS_SDK.md](INTEGRATIONS_SDK.md)). The proposal below is preserved
> for design context; anyone wishing to ship a native companion today should
> build on top of `health_assistant_bridge`, not the API surface sketched here.

## Executive Summary
This document outlines the architecture for a "headless" (minimal UI) mobile companion application built with **React Native (Expo)**. Its sole purpose is to securely read on-device health data from native health stores (Android Health Connect or iOS HealthKit) and synchronize it with a self-hosted Health Assistant backend.

By utilizing this architecture, the main Health Assistant platform remains a web-based Progressive Web App (PWA) without the burden of maintaining complex cross-platform mobile UI components, while still gaining privacy-first access to native health sensors (smartwatches, smart rings, scales).

---

## 1. Core Architecture: The "Integration Hub"

The system relies on an on-device data bridging strategy that bypasses third-party cloud aggregators entirely (e.g., Google Fit Cloud).

1.  **The Wearables (Data Sources):** Smartwatches (Garmin, Xiaomi, Apple Watch, Oura) sync their telemetry data to their respective official companion apps via Bluetooth.
2.  **The On-Device Database:** The official companion apps write this data into the operating system's central, locally encrypted health repository:
    *   Android: **Health Connect** (System-level in Android 14+, downloadable app in Android 9-13).
    *   iOS: **HealthKit**
3.  **The Sync App (The Bridge):** The custom React Native application requests user permission to read the on-device database and `POST`s the normalized data directly to the user's private Health Assistant server.

---

## 2. Technology Stack

The mobile sync application will be built using **React Native (Expo)**. This is strongly recommended over web-wrappers (like Capacitor) due to the strict background execution requirements of native OS health data synchronization.

*   **Framework:** React Native / Expo (Managed Workflow with Custom Dev Clients).
*   **Health Bridges:** 
    *   [`react-native-health-connect`](https://github.com/matinzd/react-native-health-connect) for deep integration with Android's modern Health Connect API.
    *   [`react-native-health`](https://github.com/agencyenterprise/react-native-health) for iOS HealthKit access.
*   **Background Tasks:** `expo-background-fetch` and `expo-task-manager` to allow silent syncing without opening the app.
*   **Secure Storage:** `expo-secure-store` to encrypt the user's JWT and Server URL on the device.

---

## 3. Mobile App Flow & Minimal UI

The mobile application is intentionally bare-bones. It only requires three core screens to operate effectively:

1.  **Server Authentication Screen:**
    *   Input: `Server URL` (e.g., `http://192.168.1.100:8000` or `https://health.mydomain.com`).
    *   Input: Authentication Token / Credentials.
    *   Action: Validates connection and stores credentials securely.
2.  **Permissions Screen:**
    *   Action: "Grant Health Access" button. This triggers the native OS permission prompt asking the user to allow read access to specific datasets (Heart Rate, Steps, Blood Pressure, Active Energy).
3.  **Sync Status Dashboard (Main View):**
    *   Displays: "Last Successful Sync: 10 minutes ago".
    *   Action: "Force Sync Now" button.
    *   Toggle: "Enable Background Syncing" (Registers OS-level background workers).

---

## 4. Backend Communication Protocol & Endpoints

The mobile app must read the fragmented native data formats (which vary heavily between Apple and Google schemas) and standardize them before sending them to the backend API.

> **The endpoint and payload schema below are PROPOSED — they do not exist in the
> backend today.** A native companion app built on top of the shipped
> `health_assistant_bridge` integration would instead push FHIR `Observation`
> bundles via the Integrations Framework's standard sync pipeline; the
> schema sketched here is what a hypothetical dedicated endpoint might accept.

### Proposed target endpoint
`POST /api/v1/wearable/data` *(not implemented — see banner above)*

### Authentication
Requests must include standard Authorization headers (e.g., `Bearer <JWT_TOKEN>`) configured against the user's specific self-hosted tenant.

### Proposed JSON Payload Schema (`WearableSyncPayload`)
The proposed payload is a highly normalized, time-series array of `WearableDataPoint` objects.

```json
{
  "device_id": "iPhone_15_Pro_Max",
  "points": [
    {
      "timestamp": "2026-06-11T14:30:00Z",
      "heart_rate": 72.5,
      "steps": 150,
      "calories": null,
      "data": {
        "spo2": 98.0,
        "sleep_stage": "deep"
      }
    }
  ]
}
```

#### Field Definitions:
*   `device_id` (String): An identifier to track which phone/watch sourced the data.
*   `points` (Array): Time-series metrics.
    *   `timestamp` (String): ISO 8601 UTC timestamp.
    *   `heart_rate` (Float, Optional): Beats per minute.
    *   `steps` (Float, Optional): Step count for that specific timestamp interval.
    *   `calories` (Float, Optional): Active kilocalories burned.
    *   `data` (Object, Optional): A dynamic JSON payload to capture unstandardized or advanced metrics (e.g., blood oxygen, HRV, specific sleep metadata) that the backend can parse flexibly.

---

## 5. Security & Privacy Keypoints

*   **Zero Cloud Dependency:** The mobile app communicates *directly* via the local network or internet to the user's self-hosted FastAPI instance. No data passes through Google Fit Cloud or Apple iCloud telemetry.
*   **Granular Permissions:** The app should only request `READ` permissions for the specific metrics it intends to sync, adhering to the principle of least privilege required by both Google Play and Apple App Store review guidelines.
*   **Data Integrity:** A production backend integration would need to handle duplicate timestamps gracefully (the shipped `IntegrationSyncService.run_sync` already does this via per-integration dedup). The proposed `wearable_service.py` does not exist in the codebase — see the banner at the top of this document.

---

## 6. Development Roadmap for Contributors

Developers wishing to implement or fork this Sync App should follow this sequence:

1.  Initialize a new Expo project with native module support (`npx create-expo-app --template bare-minimum health-assistant-sync`).
2.  Install the required health bridge libraries (`react-native-health-connect`, `react-native-health`).
3.  Configure `AndroidManifest.xml` and `Info.plist` with the strict health data usage descriptions required by the OS.
4.  Implement the React Native UI for capturing the server URL and securely saving the JWT.
5.  Write the data normalization mappers (e.g., converting Google's `StepsRecord` object to the standard `WearableDataPoint` JSON schema).
6.  Implement the HTTP `POST` logic connecting the frontend payload to the FastAPI backend. *(Note: the proposed `/api/v1/wearable/data` endpoint does not exist today — a real implementation should push FHIR `Observation` bundles via the shipped `health_assistant_bridge` integration instead; see [INTEGRATIONS_SDK.md](INTEGRATIONS_SDK.md).)*
7.  Implement OS-level Background Tasks (`WorkManager` for Android, `Background Fetch` for iOS) to automatically push data silently throughout the day.