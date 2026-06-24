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
  {
    name: "login",
    group: "Authentication",
    caption: "Sign-in screen — OAuth2 password grant against the FastAPI backend.",
    path: "/login",
    auth: false,
    fullPage: false,
    viewports: ["desktop"],
  },
  {
    name: "dashboard",
    group: "Overview",
    caption: "Patient dashboard with the draggable react-grid-layout widgets.",
    path: "/dashboard",
    viewports: ["desktop"],
    waitForSelector: "main",
  },
  {
    name: "patients",
    group: "Clinical data",
    caption: "Patient list — tenant-scoped, paginated, with search.",
    path: "/patients",
    viewports: ["desktop"],
    waitForSelector: "main",
  },
  {
    name: "patient-detail",
    group: "Clinical data",
    caption: "Patient detail view — demographics, timeline, and linked resources.",
    path: "/patients/{patientId}",
    viewports: ["desktop"],
    waitForSelector: "main",
  },
  {
    name: "biomarkers",
    group: "Clinical data",
    caption: "Biomarker catalog — definitions, units, and reference ranges.",
    path: "/biomarkers",
    viewports: ["desktop"],
    waitForSelector: "main",
  },
  {
    name: "documents",
    group: "Clinical data",
    caption: "Document list — uploaded exams/reports routed through the OCR pipeline.",
    path: "/documents",
    interactions: [
      { action: "wait", ms: 5000 }
    ],
    viewports: ["desktop"],
    waitForSelector: "main",
  },
  {
    name: "examinations",
    group: "Clinical data",
    caption: "Examination list — tracking patient visits, consults, and related diagnoses.",
    path: "/examinations",
    viewports: ["desktop"],
    waitForSelector: "main",
  },
  {
    name: "examination-detail",
    group: "Clinical data",
    caption: "Examination detail view — structured clinical notes and linked entities.",
    path: "/examinations/{examinationId}",
    viewports: ["desktop"],
    waitForSelector: "main",
  },
  {
    name: "biomarker-detail",
    group: "Clinical data",
    caption: "Biomarker detail view — longitudinal trends and clinical significance.",
    path: "/biomarkers/catalog",
    interactions: [
      { action: "waitFor", selector: "a[href^='/biomarkers/details/']" },
      { action: "click", selector: "text=Total Cholesterol" },
      { action: "waitFor", selector: "h1" },
      { action: "wait", ms: 1000 }
    ],
    viewports: ["desktop"],
    waitForSelector: "main",
  },
  {
    name: "ai-chat",
    group: "AI assistant",
    caption: "Agentic AI chat — tools, SSE streaming, and HITL task cards.",
    path: "/ai-assistant",
    interactions: [
      { action: "fill", selector: "textarea", value: "Can you list my latest biomarker results?" },
      { action: "press", key: "Enter" },
      { action: "wait", ms: 3000 }
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
