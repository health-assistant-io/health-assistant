# UI Capture & Screenshots Pipeline

The Health Assistant project uses an automated Playwright-based pipeline to capture and maintain its visual documentation (`docs/SCREENSHOTS.md`). This ensures the UI tour stays up-to-date with codebase changes and acts as a visual regression safety net.

## 1. Quick Start

To capture all scenes and rebuild the `SCREENSHOTS.md` gallery, ensure your stack is running, and simply execute the wrapper script from the project root:

```bash
# Starts the stack (if not already running)
./scripts/run-dev.sh

# Seeds the data and runs the Playwright pipeline
./scripts/capture_ui.sh
```

By default, this will:
1. Seed the database (`backend/scripts/seed_demo.py`) with rich, deterministic clinical data.
2. Install Playwright chromium (if missing).
3. Authenticate against the frontend.
4. Capture `desktop` screenshots for all defined scenes into `docs/images/`.
5. Auto-generate the `docs/SCREENSHOTS.md` gallery file.

### Useful Flags

```bash
# Capture only a specific scene
./scripts/capture_ui.sh --scene dashboard

# Capture only the mobile viewport (will create docs/SCREENSHOTS.MOBILE.md)
./scripts/capture_ui.sh --viewport mobile

# Fail immediately if a page throws an error (useful in CI)
./scripts/capture_ui.sh --strict

# Regenerate the markdown gallery without taking new screenshots
./scripts/capture_ui.sh --gallery-only
```

## 2. Adding or Editing a Scene

The "scenes" (pages to photograph) are defined declaratively in `frontend/tests-e2e/ui-capture/scenes.mjs`. 

To add a new screenshot to the gallery, simply add an object to the `scenes` array in that file.

```javascript
export const scenes = [
  {
    name: "my-new-feature",             // kebab-case identifier, used for the filename
    group: "Overview",                  // The section in the markdown gallery
    caption: "A short description of this feature.",
    path: "/my-feature-route",          // The frontend route
    viewports: ["desktop"],             // "desktop", "mobile", or both
    waitForSelector: ".main-content",   // Optional CSS selector to wait for before capture
    interactions: [                     // Optional clicks/typing before capturing
      { action: "click", selector: "button.expand-details" },
      { action: "wait", ms: 500 }
    ]
  }
];
```

### Advanced Routing (`{patientId}`)
If your route requires an ID (like `/patients/{patientId}`), use `{patientId}` verbatim in your `path`. The capture script will automatically query the API for the primary demo patient and inject their UUID into the path before navigating.

## 3. Interaction Steps
The `interactions` array lets you manipulate the page before taking the photo.
Supported actions:
- `{ action: "click", selector: ".my-btn" }`
- `{ action: "fill", selector: "#input-id", value: "hello" }`
- `{ action: "press", key: "Enter" }`
- `{ action: "waitFor", selector: ".modal-open", timeout: 5000 }`
- `{ action: "wait", ms: 1000 }` (Hard pause, use sparingly)

## 4. Deterministic Data & Time Freezing

For screenshots to be useful for Visual Regression Testing, they must be perfectly reproducible byte-for-byte unless the code changes.
To achieve this:
1. **Idempotent Data:** `backend/scripts/seed_demo.py` creates a fixed patient (Maria Papadopoulou) with exact, non-random observations.
2. **Frozen Clock:** Playwright freezes the browser clock to exactly `2026-06-15T10:00:00Z` (`FIXED_NOW` in `capture.mjs`). 

**Important:** If you add new data to `seed_demo.py`, anchor your dates relative to `2026-06-15` so UI components like "2 days ago" or trend charts render identically on every developer's machine!

## 5. Troubleshooting

- **Empty Pages ("No Patient Selected"):** The capture script injects `window.__HA_SCREENSHOT_CAPTURE__ = true` and prefills the Zustand `patient-storage` to force a patient selection. If your new page uses a different store, you might need to update the `addInitScript` block in `capture.mjs`.
- **Navigation Errors:** Use `./scripts/capture_ui.sh --strict` to see exact stack traces if a page is failing to load or a selector isn't found.
- **Port Conflicts:** The runner pulls `FRONTEND_PORT` and `BACKEND_PORT` from `.env`. If you run on custom ports, ensure your `.env` is accurate.
