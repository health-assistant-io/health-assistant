# Health Assistant — Mobile Sync Architecture

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

### Target Endpoint
`POST /api/v1/wearable/data`

### Authentication
Requests must include standard Authorization headers (e.g., `Bearer <JWT_TOKEN>`) configured against the user's specific self-hosted tenant.

### Expected JSON Payload Schema (`WearableSyncPayload`)
The backend expects a highly normalized, time-series array of `WearableDataPoint` objects.

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
*   **Data Integrity:** The backend `wearable_service.py` is configured to handle duplicate timestamps gracefully, ensuring that overlapping background syncs do not corrupt the time-series database.

---

## 6. Development Roadmap for Contributors

Developers wishing to implement or fork this Sync App should follow this sequence:

1.  Initialize a new Expo project with native module support (`npx create-expo-app --template bare-minimum health-assistant-sync`).
2.  Install the required health bridge libraries (`react-native-health-connect`, `react-native-health`).
3.  Configure `AndroidManifest.xml` and `Info.plist` with the strict health data usage descriptions required by the OS.
4.  Implement the React Native UI for capturing the server URL and securely saving the JWT.
5.  Write the data normalization mappers (e.g., converting Google's `StepsRecord` object to the standard `WearableDataPoint` JSON schema).
6.  Implement the HTTP `POST` logic connecting the frontend payload to the FastAPI `/api/v1/wearable/data` endpoint.
7.  Implement OS-level Background Tasks (`WorkManager` for Android, `Background Fetch` for iOS) to automatically push data silently throughout the day.