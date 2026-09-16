# UI Capture & Screenshots Pipeline

The Health Assistant project uses an automated Playwright-based pipeline to capture and maintain its visual documentation (`docs/SCREENSHOTS.md`). This ensures the UI tour stays up-to-date with codebase changes and acts as a visual regression safety net.

## 1. Quick Start

### System Dependencies

While Playwright manages the browser, the pipeline uses native tools to heavily optimize the resulting screenshots and generate the animated tour GIF. We highly recommend installing them:

**Debian/Ubuntu:**
```bash
sudo apt-get install pngquant gifsicle ffmpeg
```

**macOS (Homebrew):**
```bash
brew install pngquant gifsicle ffmpeg
```

*(Note: If these tools are missing, the script will still run and capture the screenshots, but the files will be significantly larger and the animated GIF won't be generated.)*

### Running the Pipeline

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

The "scenes" (pages to photograph) are defined declaratively in `scripts/ui-capture/scenes.mjs` (repo-owned). The capture runner itself (`scripts/ui-capture/capture.mjs` + `gallery.mjs`) is a family-standard vendored script — all project-specific behavior (URLs, credentials, viewport, GIF order, seeding) lives in `scripts/ui-capture/ui-capture.config.json`; **edit the config and scenes, not the vendored scripts**.

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

### Advanced Routing (`{patientId}`, `{examinationId}`)
If your route requires an ID (like `/patients/{patientId}`), use the token verbatim in your `path`. The capture script resolves tokens via the `pathTokens` map in `ui-capture.config.json` (each token = an API lookup + a `pick` path); lookup paths may reference tokens resolved earlier in the same route (e.g. `{examinationId}`'s lookup uses `{patientId}`).

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
2. **Frozen Clock:** Playwright freezes the browser clock to exactly `2026-06-15T10:00:00Z` (`capture.fixedNow` in `ui-capture.config.json`). 
3. **Mock AI:** the seed also creates a `mock`-type chat provider (a scripted model that drives the real graph + DB tools), so the `ai-chat` scene produces a full, deterministic assistant answer — no API key required. Skip it with `HA_AI_MOCK=0` if you want to capture against a real provider configured in the UI instead. You can chat with it from the terminal: `backend/venv/bin/python backend/scripts/mock_chat.py`.

**Important:** If you add new data to `seed_demo.py`, anchor your dates relative to `2026-06-15` so UI components like "2 days ago" or trend charts render identically on every developer's machine!

## 5. Troubleshooting

- **Empty Pages ("No Patient Selected"):** The capture script injects `window.__HA_SCREENSHOT_CAPTURE__ = true` and prefills the Zustand `patient-storage` to force a patient selection. Both come from the config (`session.windowFlag` + `session.apiEntries`) — if your new page uses a different store, add an entry there rather than touching the vendored runner.
- **Navigation Errors:** Use `./scripts/capture_ui.sh --strict` to see exact stack traces if a page is failing to load or a selector isn't found.
- **Port Conflicts:** The runner pulls `FRONTEND_PORT` and `BACKEND_PORT` from `.env`. If you run on custom ports, ensure your `.env` is accurate.
