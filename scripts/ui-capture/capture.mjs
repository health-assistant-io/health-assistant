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
 *   node capture.mjs --cdp http://localhost:9222   # Electron apps: attach to
 *                                                  # the real app over CDP
 *   node capture.mjs --strict                 # fail the run on any capture error
 *   node capture.mjs --print base             # machine-readable resolved values
 *                                             # (used by capture_ui.sh)
 *   node capture.mjs --version                # family template version
 *
 * Capture modes (config `app.mode`, default "url"):
 *   url  — Playwright launches its own Chromium against app.base (web SPAs).
 *   cdp  — Playwright attaches over the Chrome DevTools protocol to an
 *          already-running app (config `app.cdp`, e.g. Electron started with
 *          --remote-debugging-port). The app's real windows are navigated and
 *          captured; no separate browser is launched.
 *
 * Every run also writes `<outDir>/tour.manifest.json` (machine-readable
 * scene/caption/GIF inventory — consumed by the website tour sync and the
 * future AI-video storyboard; deterministic, no timestamps).
 *
 * Configuration precedence (highest → lowest):
 *   1. CLI flags (--base/--api/--login/--cdp/...)
 *   2. Environment variables named in config `app.env` (root .env is loaded)
 *   3. Values from `ui-capture.config.json`
 *
 * Prerequisites:
 *   - app running (the wrapper checks liveness URLs from config)
 *   - demo data seeded (wrapper runs `seed.command` from config)
 *   - Playwright in the `project.frontendDir` package (+ its chromium, url
 *     mode only — cdp mode uses the app's own browser)
 */
import { existsSync, mkdirSync, readFileSync } from "node:fs";
import { readdir } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { generateGallery } from "./gallery.mjs";

export const TEMPLATE_VERSION = "1.2.5";

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
    base: null, api: null, login: null, out: null, gallery: null, cdp: null,
  };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    switch (a) {
      case "--config": opts.config = argv[++i]; break;
      case "--scene": opts.scene = argv[++i]; break;
      case "--viewport": opts.viewport = argv[++i]; break;
      case "--base": opts.base = argv[++i]; break;
      case "--api": opts.api = argv[++i]; break;
      case "--cdp": opts.cdp = argv[++i]; break;
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
  --cdp <url>           CDP endpoint, cdp mode (overrides config app.cdp / app.env.cdp)
  --login <e:p>         demo credentials (overrides config auth.demoEmail/Password)
  --out <dir>           screenshot output dir (default config capture.outDir)
  --gallery <file>      gallery markdown path (default config capture.gallery)
  --gallery-only        skip capture, rebuild gallery only
  --headed              show the browser (default: config capture.headless, else headless)
  --strict              fail the run on any interaction/capture/login-redirect error
  --print <key>         print one resolved value for scripting:
                          base | api | login | outDir | gallery | healthUrls |
                          seed | frontendDir | gifOrder | gifOut | gifWidth |
                          frameSeconds | holdLast | mode | cdp | manifest
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

  const out = cli.out ? resolve(ROOT, cli.out) : resolve(ROOT, cfg.capture?.outDir ?? "docs/images");

  const opts = {
    cfg,
    base,
    api,
    login,
    mode: appCfg.mode ?? "url",
    cdp: cli.cdp ?? envName(appCfg.env?.cdp) ?? appCfg.cdp ?? "http://localhost:9222",
    out,
    manifest: cfg.capture?.manifest ? resolve(ROOT, cfg.capture.manifest) : join(out, "tour.manifest.json"),
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
    manifest: opts.manifest,
    healthUrls: (cfg.app?.healthUrls ?? []).map(subst).join("\n"),
    seed: cfg.seed?.command ?? "",
    frontendDir: abs(cfg.project?.frontendDir ?? "frontend"),
    gifOrder: (cfg.gif?.order ?? []).join("\n"),
    gifOut: abs(cfg.gif?.output ?? "docs/images/visual-tour.gif"),
    gifWidth: String(cfg.gif?.width ?? 800),
    frameSeconds: String(cfg.gif?.frameSeconds ?? 2),
    holdLast: String(cfg.gif?.holdLast ?? 3),
    mode: opts.mode,
    cdp: opts.mode === "cdp" ? opts.cdp : "",
    // Story-ordered "scene<TAB>file" pairs from the manifest on disk —
    // the GIF assembler's single source of truth for which PNG is which
    // scene (no filename guessing; viewport names vary per repo).
    gifFiles: (() => {
      try {
        const manifest = JSON.parse(readFileSync(opts.manifest, "utf8"));
        return manifest.scenes
          .map((s) => {
            const file = s.files.desktop ?? Object.values(s.files)[0] ?? "";
            return file ? `${s.name}\t${file}` : "";
          })
          .filter(Boolean)
          .join("\n");
      } catch {
        return "";
      }
    })(),
  };
  if (!(key in values)) {
    console.error(`Unknown --print key: ${key}. Valid: ${Object.keys(values).join(", ")}`);
    process.exit(2);
  }
  console.log(values[key]);
}

/* ---------------- auth ---------------- */

/** Parse one `Set-Cookie` header line into a Playwright cookie object. */
function parseSetCookie(line, base) {
  const [pair, ...attrs] = line.split(";");
  const eq = pair.indexOf("=");
  const name = pair.slice(0, eq).trim();
  const value = pair.slice(eq + 1).trim();
  const cookie = {
    name,
    value,
    domain: new URL(base).hostname,
    path: "/",
  };
  for (const attr of attrs) {
    const [k, v] = attr.split("=");
    const key = k.trim().toLowerCase();
    switch (key) {
      case "path": cookie.path = v.trim(); break;
      case "domain": cookie.domain = v.trim().replace(/^\./, ""); break;
      case "httponly": cookie.httpOnly = true; break;
      case "samesite": {
        const s = (v ?? "").trim();
        cookie.sameSite = s === "None" || s === "Strict" ? s : "Lax";
        break;
      }
      // expires/max-age dropped (session cookies are enough for capture) and
      // `secure` dropped unless the target is https — Chromium accepts secure
      // cookies on http://localhost but other hosts refuse them.
      case "secure": if (base.startsWith("https")) cookie.secure = true; break;
    }
  }
  return cookie;
}

async function login(opts, email, password) {
  const type = opts.cfg.auth?.type ?? "oauth2-password";
  const endpoint = opts.cfg.auth?.endpoint ?? "/auth/login";
  const url = `${opts.api}${endpoint}`;
  let res;
  if (type === "bearer-json" || type === "cookie-session") {
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
  if (type === "cookie-session") {
    // auth-kit style session cookies (nx_access/nx_refresh/nx_csrf…). The
    // SPA does its own CSRF double-submit from the JS-readable cookie; the
    // runner just carries the jar.
    const setCookies = res.headers.getSetCookie?.() ?? [];
    const cookies = setCookies
      .map((line) => parseSetCookie(line, opts.base))
      .filter((c) => c.name && c.value);
    if (cookies.length === 0) {
      throw new Error("Login succeeded but no session cookies were set — is this really a cookie-session app?");
    }
    return { __cookies: cookies };
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

/**
 * Select an item from an API payload: `match` finds the first array element
 * whose fields equal the given values (e.g. a profile by name), then `pick`
 * extracts from that element; without `match`, `pick` applies to the payload.
 */
function selectItem(data, spec) {
  if (spec?.match && Array.isArray(data)) {
    const item = data.find((it) =>
      it != null && Object.entries(spec.match).every(([k, v]) => it[k] === v),
    );
    return pickValue(item, spec?.pick);
  }
  return pickValue(data, spec?.pick);
}

async function apiGet(opts, path, tokens, extraHeaders = {}) {
  const headers = { ...extraHeaders };
  if (tokens?.access_token) headers.Authorization = `Bearer ${tokens.access_token}`;
  else if (tokens?.__cookies) {
    headers.Cookie = tokens.__cookies.map((c) => `${c.name}=${c.value}`).join("; ");
  }
  const res = await fetch(`${opts.api}${path}`, { headers });
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
      const data = await apiGet(opts, specPath, tokens, await buildApiHeaders(stack));
      const value = selectItem(data, spec);
      if (value == null) return null;
      resolved[name] = String(value);
      return resolved[name];
    } finally {
      stack.delete(name);
    }
  }

  /**
   * session.apiHeaders — extra headers for runner-side API lookups, values
   * may reference {token} placeholders (resolved on demand, cycle-guarded).
   * Entries that cannot be resolved yet (e.g. a token's own bootstrap lookup
   * referencing itself) are omitted rather than sent as literals.
   */
  async function buildApiHeaders(stack) {
    const out = {};
    for (const [k, v] of Object.entries(opts.cfg.session?.apiHeaders ?? {})) {
      const hv = await substitute(v, stack);
      if (!/\{/.test(hv)) out[k] = hv;
    }
    return out;
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
    // Resolve session.apiHeaders against the token machinery (on demand);
    // entries whose placeholders cannot resolve are omitted, not sent raw.
    const extraHeaders = {};
    for (const [k, v] of Object.entries(session.apiHeaders ?? {})) {
      const hv = await resolvePath(opts, v, tokens);
      if (!/\{/.test(hv)) extraHeaders[k] = hv;
    }
    const data = await apiGet(opts, entry.path, tokens, extraHeaders);
    const item = selectItem(data, entry);
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

async function runStep(page, step, base, strict, issues = null) {
  const tryRun = async (fn, label) => {
    try {
      await fn();
      return true;
    } catch (e) {
      console.warn(`    ${label}: ${e.message}`);
      issues?.warnings?.push?.(`${label}: ${e.message}`);
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
      issues?.warnings?.push?.(`unknown interaction: ${step.action}`);
      if (strict) throw new Error(`unknown interaction: ${step.action}`);
  }
}

/**
 * Verify the injected session actually survived navigation — the DOM can
 * render a perfectly good-looking *logged-out* page (in-place login overlays
 * render at the same URL), which the URL-based /login check cannot catch.
 * `auth.sessionCheck` names a standard API probe (e.g. "/auth/me") fetched
 * same-origin from the page; 401/403/network-error = the capture would show
 * the logged-out UI, which is never a valid tour frame.
 */
async function probeSession(page, scene, vpName, opts, tokens, issues) {
  const check = opts.cfg.auth?.sessionCheck;
  if (!check) return;
  const meUrl = `${opts.base}${opts.cfg.app?.apiPath ?? ""}${check}`;
  const status = await page.evaluate(async ({ u, bearer }) => {
    try {
      const r = await fetch(u, {
        credentials: "include",
        headers: bearer ? { Authorization: `Bearer ${bearer}` } : {},
      });
      return r.status;
    } catch {
      return 0;
    }
  }, { u: meUrl, bearer: tokens?.access_token ?? null }).catch(() => 0);
  if (status !== 200) {
    const msg = `${scene.name} [${vpName}] session probe failed (HTTP ${status} from ${meUrl}) — page shows the logged-out UI`;
    console.warn(`  ✗ ${msg}`);
    issues.errors.push(msg);
  }
}

/**
 * Drive one scene on one prepared page: navigate, guard-check, run
 * interactions, settle, screenshot. Shared by url mode (fresh context per
 * viewport) and cdp mode (the app's real window, reused).
 * Returns the captured filename, or null when nothing was written.
 */
async function captureOnPage(page, scene, vpName, opts, tokens, issues) {
  const path = await resolvePath(opts, scene.path, tokens);
  const url = `${opts.base}${path}`;
  const gotoErr = await page.goto(url, { waitUntil: "networkidle", timeout: 30000 }).then(() => null).catch((e) => {
    console.warn(`  ⚠ goto ${url}: ${e.message}`);
    issues.warnings.push(`goto ${url}: ${e.message}`);
    return e;
  });
  if (gotoErr && opts.strict) {
    throw new Error(`navigation to ${url} failed: ${gotoErr.message}`);
  }

  // SPA auth boot can hijack the first deep-link navigation (bootstrap
  // bounces to the landing route — e.g. demo auto-login → dashboard). Once
  // the session is bootstrapped, a second navigation lands on the requested
  // route. Path comparison ignores trailing slashes.
  const norm = (u) => { try { const p = new URL(u); return p.pathname.replace(/\/+$/, "") + p.search; } catch { return u; } };
  if (norm(page.url()) !== norm(url)) {
    await page.waitForTimeout(400);
    const second = await page.goto(url, { waitUntil: "networkidle", timeout: 30000 }).then(() => null).catch((e) => e);
    if (second && opts.strict) {
      throw new Error(`re-navigation to ${url} failed: ${second.message}`);
    }
  }

  if (tokens && page.url().includes("/login")) {
    const redirErr = `${scene.name} [${vpName}] ended on /login — token may be invalid or route guarded.`;
    console.warn(`  ⚠ ${redirErr}`);
    issues.errors.push(redirErr);
    if (opts.strict) throw new Error(redirErr);
  }

  if (tokens && scene.auth !== false) {
    await probeSession(page, scene, vpName, opts, tokens, issues);
  }

  if (scene.interactions) {
    for (const step of scene.interactions) await runStep(page, step, opts.base, opts.strict, issues);
  }

  if (scene.waitForSelector) {
    const found = await page.waitForSelector(scene.waitForSelector, { timeout: 15000 }).then(() => true).catch(() => false);
    if (!found) {
      const msg = `waitForSelector "${scene.waitForSelector}" not found before capture.`;
      if (opts.strict) throw new Error(msg);
      console.warn(`  ⚠ ${scene.name} [${vpName}] ${msg}`);
      issues.warnings.push(`${scene.name} [${vpName}] ${msg}`);
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
      await shootFullPage(page, filepath, fullPage);
    }
  } else {
    await shootFullPage(page, filepath, fullPage);
  }
  console.log(`  ✓ ${scene.name} [${vpName}] → ${filename}`);
  return filename;
}

/**
 * Screenshot helper — full-page shots are taken by RESIZING the viewport
 * to the content height, never via Playwright's `fullPage: true`.
 *
 * Chromium's capture-beyond-viewport (what fullPage uses) rasterizes
 * composited/transitioned layers from stale paint state: tab strips and
 * similar components come out washed-out or ghost earlier pages, while a
 * DOM-level readiness check passes happily. Resizing routes the render
 * through the normal paint path, so what readiness verified is what the
 * pixels show. Verified A/B on career's CV builder (2026-09).
 */
async function shootFullPage(page, filepath, fullPage) {
  if (!fullPage) {
    await page.screenshot({ path: filepath });
    return;
  }
  const vp = page.viewportSize();
  const height = await page.evaluate(
    () => Math.min(document.documentElement.scrollHeight, 30000),
  );
  if (vp && height > vp.height) {
    await page.setViewportSize({ width: vp.width, height });
    await page.waitForTimeout(250); // settle re-layout before the pixels
  }
  await page.screenshot({ path: filepath });
  if (vp && height > vp.height) await page.setViewportSize(vp);
}

/** Install the session store + capture flag (context- or page-level init script). */
async function installCaptureInitScript(target, windowFlag, store) {
  // Runs before any page script on every navigation: the SPA finds its
  // session in localStorage and skips any auth redirect; the window flag
  // lets the app suppress dev-only UI (toasts, update banners) during capture.
  await target.addInitScript(
    ([flag, entries]) => {
      try {
        window[flag] = true;
        for (const [k, v] of Object.entries(entries)) localStorage.setItem(k, v);
      } catch {}
    },
    [windowFlag, store],
  );
}

/** Fixed clock so dates/charts/relative times are identical across runs —
 *  this is what makes screenshots diffable for visual regression. */
async function installFixedClock(page, opts) {
  const fixedNow = opts.cfg.capture?.fixedNow ? Date.parse(opts.cfg.capture.fixedNow) : null;
  if (fixedNow != null && !Number.isNaN(fixedNow)) {
    try { await page.clock.install({ now: fixedNow }); } catch {}
  }
}

/** url mode — Playwright's own Chromium, fresh context per viewport. */
async function captureScene(browser, scene, opts, tokens, issues) {
  const captured = [];
  const store = await buildSessionStore(opts, tokens);
  const viewports = { ...DEFAULT_VIEWPORTS, ...(opts.cfg.capture?.viewports ?? {}) };
  const windowFlag = opts.cfg.session?.windowFlag ?? "__UI_CAPTURE__";

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
      // Suppress entrance animations for deterministic captures (apps that
      // honor prefers-reduced-motion, e.g. framer-motion based UIs).
      ...(opts.cfg.capture?.reducedMotion ? { reducedMotion: "reduce" } : {}),
    });

    // cookie-session apps: carry the login jar into the fresh context.
    if (tokens?.__cookies) await context.addCookies(tokens.__cookies);

    await installCaptureInitScript(context, windowFlag, store);

    const page = await context.newPage();
    await installFixedClock(page, opts);

    const filename = await captureOnPage(page, scene, vpName, opts, tokens, issues);
    if (filename) captured.push({ viewport: vpName, file: filename });
    await context.close();
  }
  return captured;
}

/**
 * cdp mode — capture a scene in the app's real window (Electron etc.):
 * attach over the DevTools protocol, pick the window to drive (config
 * `app.cdpPage`, a URL substring; default = first page), apply the session
 * store as a page-level init script, resize via viewport emulation, then
 * reuse the shared per-scene logic. The window is never closed.
 *
 * Init scripts accumulate on a reused page — each scene adds one; they run
 * in install order on every navigation and the latest scene's entries are
 * written last, so stale keys are always overwritten with current values.
 */
async function captureSceneCdp(browser, scene, opts, tokens, issues) {
  const captured = [];
  const context = browser.contexts()[0];
  if (!context) throw new Error("cdp mode: no browser context — is the remote-debugging endpoint reachable?");
  const pages = context.pages();
  if (pages.length === 0) throw new Error("cdp mode: no open pages — is the app window open?");
  // Window selection: explicit config matcher first, then any page already on
  // the app base URL (the window the scene navigates), else the first page.
  const matcher = opts.cfg.app?.cdpPage;
  const matched = matcher ? pages.find((p) => p.url().includes(matcher)) : undefined;
  const page = matched ?? pages.find((p) => opts.base && p.url().startsWith(opts.base)) ?? pages[0];
  if (matcher && !matched) {
    console.warn(`  ⚠ no CDP page matches "${matcher}" — using ${page.url()}`);
  }

  const store = await buildSessionStore(opts, tokens);
  const windowFlag = opts.cfg.session?.windowFlag ?? "__UI_CAPTURE__";
  const viewports = { ...DEFAULT_VIEWPORTS, ...(opts.cfg.capture?.viewports ?? {}) };

  // cookie-session apps: push the login jar into the app's own context.
  if (tokens?.__cookies) await context.addCookies(tokens.__cookies);
  await installCaptureInitScript(page, windowFlag, store);
  await installFixedClock(page, opts);

  for (const vpName of scene.viewports) {
    if (opts.viewport && opts.viewport !== vpName) continue;
    const vp = viewports[vpName];
    if (!vp) {
      console.warn(`  ⚠ unknown viewport "${vpName}" — skipping`);
      continue;
    }
    // Viewport emulation resizes the captured content; the OS window keeps
    // its own size (Electron) — screenshots still reflect the emulated size.
    try {
      await page.setViewportSize({ width: vp.width, height: vp.height });
    } catch {
      console.warn("  ⚠ setViewportSize failed — capturing at the window's own size");
    }
    const filename = await captureOnPage(page, scene, vpName, opts, tokens, issues);
    if (filename) captured.push({ viewport: vpName, file: filename });
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
 * them under the interop `default` key). Falls back to `@playwright/test`
 * (same exports) for pnpm repos where only the test runner is a direct dep.
 */
async function loadPlaywright(opts) {
  const { createRequire } = await import("node:module");
  const frontendDir = resolve(ROOT, opts.cfg.project?.frontendDir ?? "frontend");
  const req = createRequire(join(frontendDir, "package.json"));
  try {
    return req("playwright");
  } catch {
    return req("@playwright/test");
  }
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
    const { desktop, mobile } = await generateGallery(scenes, groups, {
      cfg: opts.cfg, out: opts.out, gallery: opts.gallery, files,
      root: ROOT, templateVersion: TEMPLATE_VERSION, manifest: opts.manifest,
    });
    console.log(`Gallery: ${desktop ?? "(no desktop screenshots)"}${mobile ? ` + ${mobile}` : ""}`);
    return;
  }

  const selected = opts.scene ? scenes.filter((s) => s.name === opts.scene) : scenes;
  if (opts.scene && selected.length === 0) {
    console.error(`No scene named "${opts.scene}". Available: ${scenes.map((s) => s.name).join(", ")}`);
    process.exit(2);
  }

  // One login shared by all authed scenes (auth.type "none" never logs in).
  let tokens = null;
  const needsAuth = opts.cfg.auth?.type !== "none" && selected.some((s) => s.auth !== false);
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
  const captureFn = opts.mode === "cdp" ? captureSceneCdp : captureScene;
  const browser =
    opts.mode === "cdp"
      ? await chromium.connectOverCDP(opts.cdp, { timeout: 15000 })
      : await chromium.launch({ headless: opts.headless });
  const results = [];
  const issues = { errors: [], warnings: [] };
  for (const scene of selected) {
    console.log(`\n▸ ${scene.name}: ${scene.caption}`);
    try {
      const captured = await captureFn(browser, scene, opts, scene.auth === false ? null : tokens, issues);
      results.push({ scene, captured });
    } catch (e) {
      console.error(`  ✗ ${scene.name} failed: ${e.message}`);
      issues.errors.push(`${scene.name}: ${e.message}`);
      results.push({ scene, captured: [], error: e.message });
    }
  }
  await browser.close(); // cdp: disconnects only — the app keeps running

  // Always rebuild the galleries so the docs reflect what's on disk.
  const files = await readdir(opts.out);
  const { desktop, mobile } = await generateGallery(scenes, groups, {
    cfg: opts.cfg, out: opts.out, gallery: opts.gallery, files,
    root: ROOT, templateVersion: TEMPLATE_VERSION, manifest: opts.manifest,
  });
  console.log(`\nDone. ${results.reduce((n, r) => n + r.captured.length, 0)} screenshot(s) in ${opts.out}`);
  console.log(`Gallery: ${desktop ?? "(no desktop screenshots)"}${mobile ? ` + ${mobile}` : ""}`);
  console.log(`Manifest: ${opts.manifest}`);

  // Loud report: a tour with hidden failures is worse than no tour.
  if (issues.errors.length || issues.warnings.length) {
    console.log(`\nCapture report:`);
    for (const e of issues.errors) console.log(`  ✗ ${e}`);
    for (const w of issues.warnings) console.log(`  ⚠ ${w}`);
    console.log(`${issues.errors.length} error(s), ${issues.warnings.length} warning(s)`);
  }

  const errored = results.filter((r) => r.error);
  if (errored.length || issues.errors.length) process.exit(1);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
