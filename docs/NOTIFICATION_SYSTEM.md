# Notification Framework

Health Assistant features a modular, asynchronous notification system designed to handle medical reminders, clinical alerts, and system events. The system is FHIR-compliant and supports multiple delivery channels.

## Core Architecture

The system is built on three main database models:

1.  **NotificationTrigger**: Defines the rules for when a notification should be generated.
    *   `TIME`: Scheduled for a specific moment (e.g., examination).
    *   `RECURRING`: Recurring schedule (e.g., daily medication).
    *   `EVENT`: Triggered by system-wide events (e.g., "biomarker_update").
2.  **Notification**: The actual message instance delivered to a patient (Maps to FHIR `Communication`).
3.  **NotificationSubscription**: Stores Web Push (VAPID) credentials for PWA integration.

## How it Works

1.  **Trigger Creation**: Triggers can be created manually via API or automatically (e.g., when a medication is added).
2.  **Processing**: A periodic Celery task (`check_notification_triggers`) runs every minute to find due triggers.
3.  **Generation**: When a trigger is due, a `Notification` record is created.
4.  **Delivery**: A background task (`deliver_notification`) attempts to send the notification via the best available channel (Web Push, In-App).
5.  **PWA Integration**: Native system notifications are shown via a Service Worker (`sw.ts`).

---

## Auth & Tenant Isolation

All `/api/v1/notifications/*` endpoints require authentication (Bearer
JWT) and are **tenant-scoped** — a caller can only see notifications,
triggers, and subscriptions whose `tenant_id` matches their own. Cross-
tenant calls return `404` (no leak that the row exists in another
tenant). Patient-scoped routes (`list`, `create_trigger`, `list_triggers`,
`get_alert_history`) additionally call `check_patient_access` so a
`USER`-role caller can only touch patients assigned to them; `ADMIN`/
`MANAGER` see the tenant-wide view.

Endpoints:

| Method | Path | Notes |
|---|---|---|
| `GET`  | `/notifications?vapid-public-key` | Public (no auth). |
| `POST` | `/notifications/subscribe` | Auth + tenant-scoped (user-bound). |
| `GET`  | `/notifications?patient_id=...` | Auth + tenant + patient-access. |
| `PATCH`| `/notifications/{id}/read` | Auth + tenant-scoped. |
| `PATCH`| `/notifications/{id}/delivered` | **Auth + tenant-scoped.** Previously had no auth at all — the frontend service worker now needs to send the session JWT (audit B2). |
| `POST` | `/notifications/triggers` | Auth + tenant + patient-access. |
| `GET`  | `/notifications/triggers?patient_id=...` | Auth + tenant + patient-access. |
| `DELETE`| `/notifications/triggers/{id}` | Auth + tenant-scoped (cross-tenant delete is a no-op; success returned to avoid leaking existence). |
| `POST` | `/notifications/triggers/{id}/test` | Auth + tenant-scoped (cross-tenant → 404). |

At the service layer, `NotificationManager.mark_as_read` /
`mark_as_delivered` / `get_active_notifications` accept an optional
`tenant_id` parameter and constrain the UPDATE/SELECT with it. The
mark_* methods return the actual `rowcount > 0` so an endpoint can
distinguish a successful update from a no-op cross-tenant call (they
previously returned `True` unconditionally, masking cross-tenant
no-ops).

---

## How to Add New Notification Types

To extend the system with a new type of notification (e.g., a "Heart Rate Alarm"), follow these steps:

### 1. Backend: Add to Enum
Add the new type to the `NotificationType` enum.

```python
# backend/app/models/notification.py

class NotificationType(enum.Enum):
    ...
    HEART_RATE_ALARM = "heart_rate_alarm" # Add this
```

### 2. Backend: Add Event Hook
In your service logic, call `NotificationManager.trigger_event` when the condition is met.

```python
# backend/app/services/wearable_service.py

from app.services.notification_manager import NotificationManager

async def process_heart_rate(patient_id, bpm, tenant_id):
    if bpm > 150:
        await NotificationManager.trigger_event(
            event_name="high_heart_rate",
            patient_id=patient_id,
            tenant_id=tenant_id,
            data={"bpm": bpm}
        )
```

### 3. Backend: Configure Trigger
Ensure a `NotificationTrigger` exists for the patient that matches the `event_name`.

```python
await NotificationManager.create_trigger(
    patient_id=patient_id,
    notification_type=NotificationType.HEART_RATE_ALARM,
    trigger_type=TriggerType.EVENT,
    config={"event_name": "high_heart_rate"},
    title="High Heart Rate Detected",
    body=f"Your heart rate is currently very high. Please rest.",
    tenant_id=tenant_id
)
```

### 4. Frontend: Add Icon Mapping
Map the new type to an icon in the Notification Center.

```tsx
// frontend/src/components/layout/NotificationBell.tsx

const getIcon = (type: string) => {
  switch (type) {
    ...
    case 'heart_rate_alarm': 
      return <Activity className="w-4 h-4 text-orange-500" />;
    ...
  }
};
```

---

## Delivery Channels

*   **In-App**: Stored in the database and fetched via polling (30s interval) in the `NotificationBell` component.
*   **Web Push**: Native OS notifications via VAPID. Requires user permission in Settings.
*   **Email (Extensible)**: Logic placeholder exists in the `deliver_notification` task.

---

## Notification Management & Debugging

Health Assistant includes a built-in **Notification Center** (accessible via the sidebar) to help users and developers manage and debug notifications.

### 1. Notification Center Tabs
*   **Active Triggers**: Shows all currently scheduled rules (e.g., upcoming medication doses). You can see the `Next Run` time, which is updated automatically after every execution.
*   **Delivery History**: A log of all generated notifications, their delivery channel (Push/In-App), and status (`Pending`, `Delivered`, `Failed`, `Read`).

---

## Status Definitions

*   **Pending**: The notification has been created but the background worker has not processed it yet.
*   **Delivered**: The worker has successfully dispatched the message to the delivery channel (e.g., accepted by the Web Push service or stored for In-App display).
*   **Read**: The user has clicked or viewed the notification in the application.
*   **Failed**: An error occurred during the delivery attempt (e.g., invalid subscription keys).

### 2. Manual Testing
To verify that your browser/device is correctly receiving notifications without waiting for a scheduled time:
1.  Go to the **Notification Center**.
2.  Switch to the **Active Triggers** tab.
3.  Locate a trigger and click the **Play (Test)** icon.
4.  The system will bypass the schedule and immediately attempt delivery to all your registered devices.

### 3. Multi-Device Delivery
The system supports broadcasting to multiple devices. If a user is logged into the PWA on both a mobile phone and a desktop, and has enabled permissions on both, a single trigger will fire a native notification on **both devices simultaneously**.

---

## Configuration & Setup (VAPID)

Native browser notifications require **VAPID** (Voluntary Application Server Identification) keys to securely communicate with browser push services (Google, Apple, Mozilla).

### 1. Generate Keys
If you are setting up the project for the first time, generate a new key pair:
```bash
npx web-push generate-vapid-keys
```

### 2. Update Environment Variables
Add the generated keys to your `backend/.env` file:
```env
VAPID_PUBLIC_KEY=your_long_public_key_string
VAPID_PRIVATE_KEY=your_long_private_key_string
VAPID_ADMIN_EMAIL=your-email@example.com
```

### 3. Enable Permissions
Users must explicitly grant permission in the application:
1.  Navigate to **Settings** -> **Profile**.
2.  Click **"Configure browser permissions"** under the Notifications section.
3.  Accept the browser prompt.

