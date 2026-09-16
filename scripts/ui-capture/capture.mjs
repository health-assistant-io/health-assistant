/**
 * Neuronection family UI capture runner — canonical, config-driven template.
 *
 * Captures a PNG per scene per viewport (Playwright), then rebuilds the
 * Markdown gallery docs (via gallery.mjs). All repo-specific facts live in
 * `ui-capture.config.json` next to this script — edit the config, not the
 * script (same doctrine as version_manager.py / check_translations.py).
 *
 * Scene catalog: `scenes.mjs` in this directory (repo-owned, NOT overwritten
 * by the family sync script).
 *
 * Usage:
 *   node capture.mjs                          # all scenes, all viewports + gallery
 *   node capture.mjs --scene dashboard        # one scene
 *   node capture.mjs --viewport desktop       # one viewport
 *   node capture.mjs --gallery-only           # rebuild gallery from PNGs on disk
 *   node capture.mjs --base http://localhost:3000 --api http://localhost:8000/api/v1
 *   node capture.mjs --login demo@example.local:Demo1234!
 *   node capture.mjs --strict                 # fail the run on any capture error
 *   node capture.mjs --print base             # machine-readable resolved values
 *                                             # (used by capture_ui.sh)
 *   node capture.mjs --version                # family template version
 *
 * Configuration precedence (highest → lowest):
 *   1. CLI flags (--base/--api/--login/...)
 *   2. Environment variables named in config `app.env` (root .env is loaded)
 *   3. Values from `ui-capture.config.json`
 *
 * Prerequisites:
 *   - app running (the wrapper checks liveness URLs from config)
 *   - demo data seeded (wrapper runs `seed.command` from config)
 *   - Playwright + chromium in the `project.frontendDir` package
 */
import { existsSync, mkdirSync, readFileSync } from "node:fs";
import { readdir } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { generateGallery } from "./gallery.mjs";

export const TEMPLATE_VERSION = "1.1.2";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(__dirname, "..", ".."); // scripts/ui-capture → repo root
const CONFIG_PATH = join(__dirname, "ui-capture.config.json");
const ENV_FILE = join(ROOT, ".env");

const DEFAULT_VIEWPORTS = {
  desktop: { width: 1440, height: 1200, deviceScaleFactor: 1 },
  mobile: { width: 390, height: 844, deviceScaleFactor: 2 },
};

/** Minimal .env parser: KEY=VALUE lines; quotes stripped, inline ` # …` comments dropped. */
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
    if ((val.startsWith('"') && val.endsWith('"')) || (val.startsWith("'") && val.endsWith("'"))) {
      val = val.slice(1, -1);
    } else {
      const hash = val.indexOf(" #");
      if (hash >= 0) val = val.slice(0, hash).trim();
    }
    if (val.length) out[key] = val;
  }
  return out;
}

function loadConfig(explicitPath) {
  const path = explicitPath || CONFIG_PATH;
  if (!existsSync(path)) {
    console.error(`Missing config: ${path}\nCopy ui-capture.config.example.json → ui-capture.config.json and fill it in.`);
    process.exit(2);
  }
  return JSON.parse(readFileSync(path, "utf8"));
}

function parseArgs(argv) {
  const opts = {
    config: null, scene: null, viewport: null, galleryOnly: false,
    headless: null, strict: false, print: null, version: false,
    base: null, api: null, login: null, out: null, gallery: null,
  };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    switch (a) {
      case "--config": opts.config = argv[++i]; break;
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
      case "--print": opts.print = argv[++i]; break;
      case "--version": opts.version = true; break;
      case "-h":
      case "--help": printHelp(); process.exit(0); break;
      default:
        console.error(`Unknown flag: ${a}`);
        process.exit(2);
    }
  }
  return opts;
}

function printHelp() {
  console.log(`Family UI capture runner (template v${TEMPLATE_VERSION})

  --config <file>       config path (default ui-capture.config.json next to this script)
  --scene <name>        capture only one scene (by .name)
  --viewport <v>        desktop | mobile
  --base <url>          frontend base (overrides config app.base / app.env.base)
  --api <url>           backend API base (overrides config app.api / app.env.api)
  --login <e:p>         demo credentials (overrides config auth.demoEmail/Password)
  --out <dir>           screenshot output dir (default config capture.outDir)
  --gallery <file>      gallery markdown path (default config capture.gallery)
  --gallery-only        skip capture, rebuild gallery only
  --headed              show the browser (default: config capture.headless, else headless)
  --strict              fail the run on any interaction/capture/login-redirect error
  --print <key>         print one resolved value for scripting:
                          base | api | login | outDir | gallery | healthUrls |
                          seed | frontendDir | gifOrder | gifOut | gifWidth |
                          frameSeconds | holdLast
  --version             print family template version`);
}

/**
 * Resolve runtime options: config values, overridden by the env vars named in
 * `app.env` (dotenv first, then real process.env), overridden by CLI flags.
 *
 * URL resolution supports two env styles: a full URL var (`app.env.base`,
 * e.g. HA_FRONTEND_URL) wins, else a port var (`app.env.basePort`, e.g.
 * FRONTEND_PORT) builds `http://localhost:<port>` (+ `app.apiPath` for the
 * API), else the static config value applies.
 */
function resolveOptions(cli) {
  const cfg = loadConfig(cli.config);
  const env = { ...loadDotEnv(ENV_FILE), ...process.env };
  const envName = (v) => (v && env[v]) || null;
  const appCfg = cfg.app ?? {};
  const apiPath = appCfg.apiPath ?? "";
  const auth = cfg.auth ?? {};

  const base =
    cli.base ??
    envName(appCfg.env?.base) ??
    (envName(appCfg.env?.basePort) ? `http://localhost:${envName(appCfg.env?.basePort)}` : null) ??
    appCfg.base ??
    "http://localhost:3000";
  const api =
    cli.api ??
    envName(appCfg.env?.api) ??
    (envName(appCfg.env?.apiPort) ? `http://localhost:${envName(appCfg.env?.apiPort)}${apiPath}` : null) ??
    appCfg.api ??
    null;

  const demoEmail = cli.login ? null : (envName(appCfg.env?.email) ?? auth.demoEmail ?? null);
  const demoPassword = cli.login ? null : (envName(appCfg.env?.password) ?? auth.demoPassword ?? null);
  const login = cli.login ?? (demoEmail && demoPassword ? `${demoEmail}:${demoPassword}` : null);

  const opts = {
    cfg,
    base,
    api,
    login,
    out: cli.out ? resolve(ROOT, cli.out) : resolve(ROOT, cfg.capture?.outDir ?? "docs/images"),
    gallery: cli.gallery ? resolve(ROOT, cli.gallery) : resolve(ROOT, cfg.capture?.gallery ?? "docs/SCREENSHOTS.md"),
    scene: cli.scene,
    viewport: cli.viewport,
    galleryOnly: cli.galleryOnly,
    headless: cli.headless === false ? false : (cfg.capture?.headless ?? true),
    strict: cli.strict,
  };
  return opts;
}

/** API root = api base minus the `app.apiPath` suffix (for /health probes etc.). */
function apiRoot(opts) {
  const apiPath = opts.cfg.app?.apiPath ?? "";
  if (!opts.api || !apiPath || !opts.api.endsWith(apiPath)) return opts.api ?? "";
  return opts.api.slice(0, -apiPath.length);
}

function printResolved(opts, key) {
  const cfg = opts.cfg;
  const abs = (p) => resolve(ROOT, p);
  const subst = (u) => String(u).replaceAll("{base}", opts.base).replaceAll("{apiRoot}", apiRoot(opts));
  const values = {
    base: opts.base,
    api: opts.api ?? "",
    login: opts.login ?? "",
    outDir: opts.out,
    gallery: opts.gallery,
    healthUrls: (cfg.app?.healthUrls ?? []).map(subst).join("\n"),
    seed: cfg.seed?.command ?? "",
    frontendDir: abs(cfg.project?.frontendDir ?? "frontend"),
    gifOrder: (cfg.gif?.order ?? []).join("\n"),
    gifOut: abs(cfg.gif?.output ?? "docs/images/visual-tour.gif"),
    gifWidth: String(cfg.gif?.width ?? 800),
    frameSeconds: String(cfg.gif?.frameSeconds ?? 2),
    holdLast: String(cfg.gif?.holdLast ?? 3),
  };
  if (!(key in values)) {
    console.error(`Unknown --print key: ${key}. Valid: ${Object.keys(values).join(", ")}`);
    process.exit(2);
  }
  console.log(values[key]);
}

/* ---------------- auth ---------------- */

async function login(opts, email, password) {
  const type = opts.cfg.auth?.type ?? "oauth2-password";
  const endpoint = opts.cfg.auth?.endpoint ?? "/auth/login";
  const url = `${opts.api}${endpoint}`;
  let res;
  if (type === "bearer-json") {
    res = await fetch(url, {
      method: "POST",
      body: JSON.stringify({ email, password }),
      headers: { "Content-Type": "application/json" },
    });
  } else {
    const body = new URLSearchParams({ username: email, password, grant_type: "password" });
    res = await fetch(url, { method: "POST", body, headers: { "Content-Type": "application/x-www-form-urlencoded" } });
  }
  if (!res.ok) {
    throw new Error(`Login failed (${res.status}): ${await res.text().catch(() => "")}`);
  }
  return res.json();
}

/* ---------------- API helpers ---------------- */

/** Resolve "items[0].id"-style pick paths (root arrays work: "[0].id"). */
function pickValue(obj, pick) {
  if (!pick) return obj;
  const path = String(pick).replace(/\[(\d+)\]/g, ".$1");
  return path
    .split(".")
    .filter(Boolean)
    .reduce((acc, k) => (acc == null ? undefined : acc[k]), obj);
}

async function apiGet(opts, path, tokens) {
  const res = await fetch(`${opts.api}${path}`, {
    headers: tokens?.access_token ? { Authorization: `Bearer ${tokens.access_token}` } : {},
  });
  if (!res.ok) return null;
  try {
    return await res.json();
  } catch {
    return null;
  }
}

/**
 * Resolve {token} placeholders in a scene path from config `pathTokens`:
 *   { "patientId": { "path": "/patients?limit=1", "pick": "items[0].id" } }
 * Tokens referenced by a scene route are resolved on demand — including
 * tokens that only appear inside another token's lookup path (e.g.
 * `{examinationId}`'s lookup references `{patientId}` even though the scene
 * route itself doesn't). Unresolvable tokens are left in place (the
 * navigation will fail visibly).
 */
async function resolvePath(opts, path, tokens) {
  const resolved = {};

  async function substitute(str, stack) {
    for (const m of str.matchAll(/\{(\w+)\}/g)) await resolveToken(m[1], stack);
    return str.replace(/\{(\w+)\}/g, (_, k) => resolved[k] ?? `{${k}}`);
  }

  async function resolveToken(name, stack) {
    if (name in resolved) return resolved[name];
    if (stack.has(name)) return null; // cycle guard
    stack.add(name);
    try {
      const spec = opts.cfg.pathTokens?.[name];
      if (!spec) return null;
      const specPath = await substitute(spec.path, stack);
      const data = await apiGet(opts, specPath, tokens);
      const value = pickValue(data, spec.pick);
      if (value == null) return null;
      resolved[name] = String(value);
      return resolved[name];
    } finally {
      stack.delete(name);
    }
  }

  return substitute(path, new Set());
}

/**
 * Build the localStorage entries injected before page scripts run:
 * - `session.tokenKeys`: localStorageKey → token field name (e.g. accessToken → access_token)
 * - `session.apiEntries`: fetch an object from the API and (optionally) splice it
 *   into a `wrap` template — the literal string "{item}" is replaced by the picked object.
 * Returns { key: stringValue } ready for localStorage.setItem.
 */
async function buildSessionStore(opts, tokens) {
  const session = opts.cfg.session ?? {};
  const store = {};
  for (const [lsKey, tokenField] of Object.entries(session.tokenKeys ?? {})) {
    if (tokens?.[tokenField] != null) store[lsKey] = String(tokens[tokenField]);
  }
  for (const entry of session.apiEntries ?? []) {
    const data = await apiGet(opts, entry.path, tokens);
    const item = pickValue(data, entry.pick);
    if (item == null) continue;
    if (entry.wrap != null) {
      store[entry.key] = JSON.stringify(entry.wrap).split('"{item}"').join(JSON.stringify(item));
    } else if (typeof item === "string") {
      store[entry.key] = item;
    } else {
      store[entry.key] = JSON.stringify(item);
    }
  }
  return store;
}

/* ---------------- capture ---------------- */

async function runStep(page, step, base, strict) {
  const tryRun = async (fn, label) => {
    try {
      await fn();
      return true;
    } catch (e) {
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
  const store = await buildSessionStore(opts, tokens);
  const viewports = { ...DEFAULT_VIEWPORTS, ...(opts.cfg.capture?.viewports ?? {}) };
  const windowFlag = opts.cfg.session?.windowFlag ?? "__UI_CAPTURE__";
  const fixedNow = opts.cfg.capture?.fixedNow ? Date.parse(opts.cfg.capture.fixedNow) : null;

  for (const vpName of scene.viewports) {
    if (opts.viewport && opts.viewport !== vpName) continue;
    const vp = viewports[vpName];
    if (!vp) {
      console.warn(`  ⚠ unknown viewport "${vpName}" — skipping`);
      continue;
    }
    const context = await browser.newContext({
      viewport: { width: vp.width, height: vp.height },
      deviceScaleFactor: vp.deviceScaleFactor ?? 1,
    });

    // Runs before any page script on every navigation: the SPA finds its
    // session in localStorage and skips any auth redirect; the window flag
    // lets the app suppress dev-only UI (toasts, update banners) during capture.
    await context.addInitScript(
      ([flag, entries]) => {
        try {
          window[flag] = true;
          for (const [k, v] of Object.entries(entries)) localStorage.setItem(k, v);
        } catch {}
      },
      [windowFlag, store],
    );

    const page = await context.newPage();

    // Fixed clock so dates/charts/relative times are identical across runs —
    // this is what makes screenshots diffable for visual regression.
    if (fixedNow != null && !Number.isNaN(fixedNow)) {
      try { await page.clock.install({ now: fixedNow }); } catch {}
    }

    const path = await resolvePath(opts, scene.path, tokens);
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
    await page.waitForTimeout(scene.settleMs ?? opts.cfg.capture?.settleMs ?? 800);

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

/** Import project-owned scenes.mjs from the template directory. */
async function loadScenes() {
  const scenesPath = join(__dirname, "scenes.mjs");
  if (!existsSync(scenesPath)) {
    console.error(`Missing scene catalog: ${scenesPath}\nCopy scenes.example.mjs → scenes.mjs and define your scenes.`);
    process.exit(2);
  }
  return import(pathToFileURL(scenesPath).href);
}

/**
 * Load Playwright resolved from the frontend package (works from any cwd).
 * Uses createRequire on purpose: the frontend package's `require.resolve`
 * anchor finds its node_modules, and Playwright's CJS entry exposes the
 * browser launchers directly (importing the CJS file by path would bury
 * them under the interop `default` key).
 */
async function loadPlaywright(opts) {
  const { createRequire } = await import("node:module");
  const frontendDir = resolve(ROOT, opts.cfg.project?.frontendDir ?? "frontend");
  const req = createRequire(join(frontendDir, "package.json"));
  return req("playwright");
}

async function main() {
  const cli = parseArgs(process.argv.slice(2));
  if (cli.version) {
    console.log(`ui-capture family template v${TEMPLATE_VERSION}`);
    return;
  }
  const opts = resolveOptions(cli);
  if (cli.print) {
    printResolved(opts, cli.print);
    return;
  }

  if (!existsSync(opts.out)) mkdirSync(opts.out, { recursive: true });

  const { scenes, groups } = await loadScenes();

  if (opts.galleryOnly) {
    const files = await readdir(opts.out);
    const { desktop, mobile } = await generateGallery(scenes, groups, { cfg: opts.cfg, out: opts.out, gallery: opts.gallery, files });
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
    if (!opts.api || !opts.login) {
      console.error("Auth needed but api/login unresolved — set config app.api + auth credentials (or pass --api/--login).");
      process.exit(2);
    }
    const [email, ...rest] = opts.login.split(":");
    console.log(`Authenticating as ${email}…`);
    tokens = await login(opts, email, rest.join(":"));
  }

  console.log(`Capturing ${selected.length} scene(s)…`);
  const { chromium } = await loadPlaywright(opts);
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
  const { desktop, mobile } = await generateGallery(scenes, groups, { cfg: opts.cfg, out: opts.out, gallery: opts.gallery, files });
  console.log(`\nDone. ${results.reduce((n, r) => n + r.captured.length, 0)} screenshot(s) in ${opts.out}`);
  console.log(`Gallery: ${desktop ?? "(no desktop screenshots)"}${mobile ? ` + ${mobile}` : ""}`);

  const errored = results.filter((r) => r.error);
  if (errored.length) process.exit(1);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
