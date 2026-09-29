/**
 * UI capture scene catalog.
 *
 * Each scene describes one screenshot to capture. The runner (capture.mjs)
 * iterates this list, authenticates, optionally runs interactions, and saves
 * a screenshot per viewport into docs/images/. The gallery generator then
 * emits two separate tour docs from this catalog: docs/screenshots.md
 * (desktop, primary) and docs/screenshots.mobile.md (mobile companion,
 * written only when mobile PNGs exist).
 *
 * Add a page = add an object here. No other code changes required.
 *
 * Scene fields:
 *   name        string   kebab-case id, used as filename prefix
 *   group       string   gallery section heading
 *   caption     string   one-line description shown under the image
 *   path        string   route (relative to base URL). May use {patientId}
 *                        which the runner resolves from GET /patients
 *   viewports   string[] subset of ["desktop","mobile"]
 *   auth        boolean  default true; set false for the login page
 *   fullPage    boolean  default true; capture whole scrollable page
 *   waitForSelector string optional selector to wait for before capture
 *   settleMs    number   extra wait after network idle (default 800)
 *   interactions  Step[] optional sequence run before capture
 *
 * Step (interaction) shapes:
 *   { action: "click",   selector: "text=Sign in" }
 *   { action: "fill",    selector: "#email", value: "..." }
 *   { action: "press",   key: "Enter" }
 *   { action: "wait",    ms: 500 }
 *   { action: "waitFor", selector: ".dashboard-grid", timeout: 10000 }
 *   { action: "navigate", path: "/documents" }
 */
export const scenes = [
  /* Demo mode auto-logins (credential-free §13) — /login immediately
     bounces to the dashboard, so there is no login screen to capture.
     Restore this scene only for non-demo captures:
      {
        name: "login",
        group: "Authentication",
        caption: "Sign-in screen — OAuth2 password grant against the FastAPI backend.",
        narration: "Sign in to your self-hosted health records — your data stays on your machine.",
        path: "/login",
        auth: false,
        fullPage: false,
        viewports: ["desktop"],
      },
  */
  {
    name: "dashboard",
    group: "Overview",
    caption: "Patient dashboard with the draggable react-grid-layout widgets.",
    narration: "The dashboard gives every patient an at-a-glance overview — arrange the widgets however you work.",
    path: "/dashboard",
    viewports: ["desktop"],
    waitForSelector: "main",
  },
  {
    name: "patients",
    group: "Clinical data",
    caption: "Patient list — tenant-scoped, paginated, with search.",
    narration: "All patients in one tenant-scoped list — search, filter, and open a record.",
    path: "/patients",
    viewports: ["desktop"],
    waitForSelector: "main",
  },
  {
    name: "patient-detail",
    group: "Clinical data",
    caption: "Patient detail view — demographics, timeline, and linked resources.",
    narration: "Each patient record ties together demographics, timeline, and every linked clinical resource.",
    path: "/patients/{patientId}",
    viewports: ["desktop"],
    waitForSelector: "main",
  },
  {
    name: "biomarkers",
    group: "Clinical data",
    caption: "Biomarker catalog — definitions, units, and reference ranges.",
    narration: "The biomarker catalog keeps units and reference ranges consistent across every measurement.",
    path: "/biomarkers",
    viewports: ["desktop"],
    waitForSelector: "main",
  },
  {
    name: "documents",
    group: "Clinical data",
    caption: "Document list — uploaded exams/reports routed through the OCR pipeline.",
    narration: "Upload an exam report and the OCR pipeline turns it into structured, searchable data.",
    path: "/documents",
    interactions: [
      { action: "wait", ms: 3000 }
    ],
    viewports: ["desktop"],
    waitForSelector: "main",
  },
  {
    name: "examinations",
    group: "Clinical data",
    caption: "Examination list — tracking patient visits, consults, and related diagnoses.",
    narration: "Examinations track every visit and consult with their related diagnoses.",
    path: "/examinations",
    viewports: ["desktop"],
    waitForSelector: "main",
  },
  {
    name: "examination-detail",
    group: "Clinical data",
    caption: "Examination detail view — structured clinical notes and linked entities.",
    narration: "Clinical notes stay structured and linked to the entities they mention.",
    path: "/examinations/{examinationId}",
    viewports: ["desktop"],
    waitForSelector: "main",
  },
  {
    name: "biomarker-detail",
    group: "Clinical data",
    caption: "Biomarker detail view — longitudinal trends and clinical significance.",
    narration: "Biomarker values become longitudinal trends you can actually read.",
    path: "/biomarkers/details/{biomarkerId}",
    interactions: [
      { action: "wait", ms: 2500 }
    ],
    viewports: ["desktop"],
    waitForSelector: "main",
  },
  {
    name: "ai-chat",
    group: "AI assistant",
    caption: "Agentic AI chat — tools, SSE streaming, and HITL task cards.",
    narration: "Ask the AI assistant about your results — it answers with your data, with human-in-the-loop control.",
    path: "/ai-assistant",
    interactions: [
      { action: "waitFor", selector: "textarea", timeout: 20000 },
      { action: "fill", selector: "textarea", value: "Provide me with the results of my latest examination", timeout: 20000 },
      { action: "press", key: "Enter" },
      { action: "wait", ms: 15000 }
    ],
    viewports: ["desktop"],
    waitForSelector: "main",
  }
];

export const groups = [
  "Authentication",
  "Overview",
  "Clinical data",
  "AI assistant"
];
