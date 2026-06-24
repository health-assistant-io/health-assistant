// Global type declarations for the frontend application

// Sentinel set by frontend/tests-e2e/ui-capture/capture.mjs via addInitScript
// before page scripts run, so the SPA can suppress dev-only UI during
// screenshot capture (PWA "App is ready for offline use" toast, etc.).
// Undefined in normal user sessions — opt-in via the capture tooling only.
interface Window {
  __HA_SCREENSHOT_CAPTURE__?: boolean;
}
