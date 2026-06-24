/**
 * UI capture runner.
 *
 * Iterates the scene catalog (scenes.mjs), authenticates once, and captures a
 * PNG per scene per viewport into docs/images/. Optionally regenerates the
 * Markdown galleries afterwards: docs/SCREENSHOTS.md (desktop, primary) and
 * docs/SCREENSHOTS.MOBILE.md (mobile, only when mobile PNGs exist).
 *
 * Usage:
 *   node tests-e2e/ui-capture/capture.mjs                 # all scenes, all viewports, then gallery
 *   node tests-e2e/ui-capture/capture.mjs --scene dashboard
 *   node tests-e2e/ui-capture/capture.mjs --viewport desktop
 *   node tests-e2e/ui-capture/capture.mjs --gallery-only   # skip capture, just rebuild the docs
 *   node tests-e2e/ui-capture/capture.mjs --base http://localhost:3000 --api http://localhost:8000/api/v1
 *   node tests-e2e/ui-capture/capture.mjs --login demo@healthassistant.local:Demo1234!
 *   node tests-e2e/ui-capture/capture.mjs --strict         # fail the run on any interaction/capture error
 *
 * Configuration precedence (highest → lowest):
 *   1. CLI flags (--base/--api/--login/...)
 *   2. Environment variables from the root .env (HA_FRONTEND_URL, HA_API_URL,
 *      HA_DEMO_EMAIL, HA_DEMO_PASSWORD, FRONTEND_PORT, BACKEND_PORT)
 *   3. Hardcoded fallbacks (localhost:3000 / :8000, demo@healthassistant.local)
 *
 * Prerequisites:
 *   - Backend + frontend running (./scripts/run-dev.sh)
 *   - Demo data seeded (python backend/scripts/seed_demo.py)
 *   - Playwright + chromium installed: npm install && npx playwright install chromium
 */
import { existsSync, mkdirSync, readFileSync } from "node:fs";
import { readdir } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { scenes } from "./scenes.mjs";
import { generateGallery } from "./gallery.mjs";
const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(__dirname, "..", "..", ".."); // project root
const DEFAULT_OUT = join(ROOT, "docs", "images");
const DEFAULT_GALLERY = join(ROOT, "docs", "SCREENSHOTS.md");
const ENV_FILE = join(ROOT, ".env");

const VIEWPORTS = {
  desktop: { width: 1440, height: 1024, deviceScaleFactor: 1 },
  mobile: { width: 390, height: 844, deviceScaleFactor: 2 },
};

// Frozen clock so dates/charts/relative times are identical across runs —
// this is what makes screenshots diffable for visual regression. Kept ~6
// months ahead of release cadence so relative times ("2 days ago") render
// plausibly in the tour. Bump when regenerating the published gallery.
const FIXED_NOW = new Date("2026-06-15T10:00:00Z").getTime();

/**
 * Minimal .env parser: KEY=VALUE lines, ignores blanks/comments and quoted
 * values. Loads the root .env so this runner agrees with run-dev.sh on ports
 * and credentials without forcing the caller to pass --base/--api/--login.
 * Returns a plain object; does not mutate process.env (callers read explicitly).
 */
function loadDotEnv(path) {
  const out = {};
  if (!existsSync(path)) return out;
  for (const raw of readFileSync(path, "utf8").split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    const eq = line.indexOf("=");
    if (eq < 0) continue;
    const key = line.slice(0, eq).trim();
    let val = line.slice(eq + 1).trim();
    // Quoted value: strip quotes, keep content verbatim (so # / spaces survive).
    if ((val.startsWith('"') && val.endsWith('"')) || (val.startsWith("'") && val.endsWith("'"))) {
      val = val.slice(1, -1);
    } else {
      // Unquoted: an inline "  # comment" starts a comment (matches how bash
      // sources the file), e.g. `KEY=value   # explanatory note`.
      const hash = val.indexOf(" #");
      if (hash >= 0) val = val.slice(0, hash).trim();
    }
    if (val.length) out[key] = val;
  }
  return out;
}

const ENV = { ...loadDotEnv(ENV_FILE), ...process.env };

// Build defaults from env, falling back to hardcoded localhost values so the
// runner still works standalone outside capture_ui.sh.
const DEFAULT_FRONTEND_PORT = ENV.FRONTEND_PORT || "3000";
const DEFAULT_BACKEND_PORT = ENV.BACKEND_PORT || "8000";
const defaultDemoEmail = ENV.HA_DEMO_EMAIL || "demo@healthassistant.local";
const defaultDemoPassword = ENV.HA_DEMO_PASSWORD || "Demo1234!";

const DEFAULTS = {
  base: ENV.HA_FRONTEND_URL || `http://localhost:${DEFAULT_FRONTEND_PORT}`,
  api: ENV.HA_API_URL || `http://localhost:${DEFAULT_BACKEND_PORT}/api/v1`,
  login: `${defaultDemoEmail}:${defaultDemoPassword}`,
  out: DEFAULT_OUT,
  gallery: DEFAULT_GALLERY,
};

function parseArgs(argv) {
  // Headless mode is disabled by default because Chromium headless does not 
  // support the built-in PDF viewer plugin. We need headed mode to render
  // the PDF preview in the documents explorer correctly.
  const opts = { ...DEFAULTS, scene: null, viewport: null, galleryOnly: false, headless: false, strict: false };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    switch (a) {
      case "--scene": opts.scene = argv[++i]; break;
      case "--viewport": opts.viewport = argv[++i]; break;
      case "--base": opts.base = argv[++i]; break;
      case "--api": opts.api = argv[++i]; break;
      case "--login": opts.login = argv[++i]; break;
      case "--out": opts.out = argv[++i]; break;
      case "--gallery": opts.gallery = argv[++i]; break;
      case "--gallery-only": opts.galleryOnly = true; break;
      case "--headed": opts.headless = false; break;
      case "--strict": opts.strict = true; break;
      case "-h":
      case "--help":
        printHelp(); process.exit(0);
        break;
      default:
        console.error(`Unknown flag: ${a}`); process.exit(2);
    }
  }
  return opts;
}

function printHelp() {
  console.log(`UI capture runner

  --scene <name>        capture only one scene (by .name)
  --viewport <v>        desktop | mobile
  --base <url>          frontend base (default ${DEFAULTS.base}; env HA_FRONTEND_URL / FRONTEND_PORT)
  --api <url>           backend API base (default ${DEFAULTS.api}; env HA_API_URL / BACKEND_PORT)
  --login <e:p>         demo credentials (default ${DEFAULTS.login}; env HA_DEMO_EMAIL / HA_DEMO_PASSWORD)
  --out <dir>           screenshot output dir (default ${DEFAULTS.out})
  --gallery <file>      gallery markdown path (default ${DEFAULTS.gallery})
  --gallery-only        skip capture, rebuild gallery only
  --headed              show the browser
  --strict              fail the run on any interaction/capture/login-redirect error
                        (default: log warnings and continue — fine for a docs tour,
                         but unsafe for visual regression where a broken page could
                         silently become the new baseline)`);
}

async function login(apiBase, email, password) {
  const body = new URLSearchParams({ username: email, password, grant_type: "password" });
  const res = await fetch(`${apiBase}/auth/login`, {
    method: "POST",
    body,
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
  });
  if (!res.ok) {
    throw new Error(`Login failed (${res.status}): ${await res.text().catch(() => "")}`);
  }
  return res.json();
}

/**
 * Resolve {patientId} (and other simple tokens) in a path by hitting the API.
 * Keeps the catalog declarative — scenes reference /patients/{patientId}
 * without knowing the seeded UUID.
 */
async function resolvePath(path, apiBase, tokens) {
  const patient = await getPrimaryPatient(apiBase, tokens);
  if (!patient) return path;

  const subs = { patientId: patient.id };
  return path.replace(/\{(\w+)\}/g, (_, k) => subs[k] ?? `{${k}}`);
}

/**
 * Fetch the first patient to use as the primary demo subject.
 */
async function getPrimaryPatient(apiBase, tokens) {
  if (!tokens) return null;
  const res = await fetch(`${apiBase}/patients?limit=1`, {
    headers: { Authorization: `Bearer ${tokens.access_token}` },
  });
  if (!res.ok) return null;
  const list = await res.json();
  return Array.isArray(list) ? list[0] : list?.items?.[0];
}

/**
 * Run one interaction step. In lenient mode (default) errors are logged and
 * swallowed so a flaky selector doesn't abort the whole tour; in --strict mode
 * they throw, surfacing broken pages that would otherwise be captured as a
 * regression baseline. Returns true on success, false on a swallowed error.
 */
async function runStep(page, step, base, strict) {
  const tryRun = async (fn, label) => {
    try { await fn(); return true; }
    catch (e) {
      console.warn(`    ${label}: ${e.message}`);
      if (strict) throw new Error(`step "${label}" failed: ${e.message}`);
      return false;
    }
  };
  switch (step.action) {
    case "click":
      await tryRun(() => page.click(step.selector, { timeout: step.timeout ?? 10000 }), `click ${step.selector}`);
      break;
    case "fill":
      await tryRun(() => page.fill(step.selector, step.value, { timeout: step.timeout ?? 10000 }), `fill ${step.selector}`);
      break;
    case "press":
      await tryRun(() => page.press(step.selector ?? "body", step.key), `press ${step.key}`);
      break;
    case "wait":
      await page.waitForTimeout(step.ms ?? 500);
      break;
    case "waitFor":
      await tryRun(() => page.waitForSelector(step.selector, { timeout: step.timeout ?? 10000 }), `waitFor ${step.selector}`);
      break;
    case "navigate":
      await tryRun(() => page.goto(`${base}${step.path}`, { waitUntil: "networkidle", timeout: 30000 }), `navigate ${step.path}`);
      break;
    default:
      console.warn(`    unknown interaction: ${step.action}`);
      if (strict) throw new Error(`unknown interaction: ${step.action}`);
  }
}

async function captureScene(browser, scene, opts, tokens) {
  const captured = [];
  const primaryPatient = await getPrimaryPatient(opts.api, tokens);

  for (const vpName of scene.viewports) {
    if (opts.viewport && opts.viewport !== vpName) continue;
    const vp = VIEWPORTS[vpName];
    const context = await browser.newContext({
      viewport: { width: vp.width, height: vp.height },
      deviceScaleFactor: vp.deviceScaleFactor,
    });

    // addInitScript runs before page scripts on every navigation, so the SPA
    // finds the tokens in localStorage and skips the /login redirect. We also set
    // a global sentinel so the app suppresses dev-only UI during capture (PWA
    // "App is ready for offline use" toast, see src/main.tsx + App.tsx).
    // We also inject the patient-storage state so the primary patient is selected.
    const initScriptArgs = [
      tokens ? tokens.access_token : null,
      tokens ? tokens.refresh_token : null,
      primaryPatient
    ];
    await context.addInitScript(([a, r, p]) => {
      try {
        window.__HA_SCREENSHOT_CAPTURE__ = true;
        if (a) {
          localStorage.setItem("accessToken", a);
          localStorage.setItem("refreshToken", r);
        }
        if (p) {
          // Zustand persist store format for patient-storage
          const patientState = {
            state: {
              patients: [p],
              currentPatient: p
            },
            version: 0
          };
          localStorage.setItem("patient-storage", JSON.stringify(patientState));
        }
      } catch {}
    }, initScriptArgs);


    const page = await context.newPage();

    // Fixed clock for reproducible dates/relative times.
    try { await page.clock.install({ now: FIXED_NOW }); } catch {}

    const path = await resolvePath(scene.path, opts.api, tokens);
    const url = `${opts.base}${path}`;
    const gotoErr = await page.goto(url, { waitUntil: "networkidle", timeout: 30000 }).then(() => null).catch((e) => {
      console.warn(`  ⚠ goto ${url}: ${e.message}`);
      return e;
    });
    if (gotoErr && opts.strict) {
      throw new Error(`navigation to ${url} failed: ${gotoErr.message}`);
    }

    if (tokens && page.url().includes("/login")) {
      const redirErr = `${scene.name} [${vpName}] ended on /login — token may be invalid or route guarded.`;
      console.warn(`  ⚠ ${redirErr}`);
      if (opts.strict) throw new Error(redirErr);
    }

    if (scene.interactions) {
      for (const step of scene.interactions) await runStep(page, step, opts.base, opts.strict);
    }

    if (scene.waitForSelector) {
      const found = await page.waitForSelector(scene.waitForSelector, { timeout: 15000 }).then(() => true).catch(() => false);
      if (!found && opts.strict) {
        throw new Error(`waitForSelector "${scene.waitForSelector}" not found before capture.`);
      }
    }
    await page.waitForTimeout(scene.settleMs ?? 800);

    const filename = `${scene.name}-${vpName}.png`;
    const filepath = join(opts.out, filename);
    const fullPage = scene.fullPage ?? true;

    if (scene.capture === "element" && scene.selector) {
      const el = await page.$(scene.selector);
      if (el) await el.screenshot({ path: filepath });
      else {
        console.warn(`  ⚠ ${scene.name} [${vpName}] selector "${scene.selector}" not found; fullPage fallback.`);
        if (opts.strict) throw new Error(`element selector not found: ${scene.selector}`);
        await page.screenshot({ path: filepath, fullPage });
      }
    } else {
      await page.screenshot({ path: filepath, fullPage });
    }

    captured.push({ viewport: vpName, file: filename });
    console.log(`  ✓ ${scene.name} [${vpName}] → ${filename}`);
    await context.close();
  }
  return captured;
}

async function main() {
  const opts = parseArgs(process.argv.slice(2));

  if (!existsSync(opts.out)) mkdirSync(opts.out, { recursive: true });

  if (opts.galleryOnly) {
    const files = await readdir(opts.out);
    const { desktop, mobile } = await generateGallery(scenes, { out: opts.out, gallery: opts.gallery, files });
    console.log(`Gallery: ${desktop ?? "(no desktop screenshots)"}${mobile ? ` + ${mobile}` : ""}`);
    return;
  }

  const selected = opts.scene ? scenes.filter((s) => s.name === opts.scene) : scenes;
  if (opts.scene && selected.length === 0) {
    console.error(`No scene named "${opts.scene}". Available: ${scenes.map((s) => s.name).join(", ")}`);
    process.exit(2);
  }

  // One login shared by all authed scenes.
  let tokens = null;
  const needsAuth = selected.some((s) => s.auth !== false);
  if (needsAuth) {
    const [email, password] = opts.login.split(":");
    console.log(`Authenticating as ${email}…`);
    tokens = await login(opts.api, email, password);
  }

  console.log(`Capturing ${selected.length} scene(s)…`);
  const { chromium } = await import("playwright");
  const browser = await chromium.launch({ headless: opts.headless });
  const results = [];
  for (const scene of selected) {
    console.log(`\n▸ ${scene.name}: ${scene.caption}`);
    try {
      const captured = await captureScene(browser, scene, opts, scene.auth === false ? null : tokens);
      results.push({ scene, captured });
    } catch (e) {
      console.error(`  ✗ ${scene.name} failed: ${e.message}`);
      results.push({ scene, captured: [], error: e.message });
    }
  }
  await browser.close();

  // Always rebuild the galleries so the docs reflect what's on disk.
  const files = await readdir(opts.out);
  const { desktop, mobile } = await generateGallery(scenes, { out: opts.out, gallery: opts.gallery, files });
  console.log(`\nDone. ${results.reduce((n, r) => n + r.captured.length, 0)} screenshot(s) in ${opts.out}`);
  console.log(`Gallery: ${desktop ?? "(no desktop screenshots)"}${mobile ? ` + ${mobile}` : ""}`);

  const errored = results.filter((r) => r.error);
  if (errored.length) process.exit(1);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
