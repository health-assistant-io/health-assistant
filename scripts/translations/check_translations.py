#!/usr/bin/env python3
r"""Neuronection family translation manager — canonical, config-driven.

One implementation for all family repos. Zero dependencies (Python 3.11+
stdlib only): talks to any OpenAI-compatible /chat/completions endpoint via
urllib, so no venv is required. All repo-specific facts live in
``translations.toml`` next to this script:

    [project]
    name = "App Name"

    [source]
    locale = "en"                       # source of truth

    [dictionaries]
    dir = "src/i18n/dictionaries"       # relative to the repo root
    format = "json"                     # json | ts
    locales = ["el", "de"]              # omit to auto-detect files in dir

    [prompts]
    dir = "scripts/translations/prompts"  # repo-local prompts/glossaries (optional)

    [llm]                               # defaults; secrets stay in .env
    temperature = 0.3
    batch_size = 20
    concurrency = 3

    [locales.el]                        # optional display names
    name = "Greek"

Glossary/instruction layering (later wins, per term):
    glossary.json            → repo-global terms
    glossary.<locale>.json   → repo-locale terms (overrides global)
    instructions/global.md   → applied to every locale
    instructions/<locale>.md → locale-specific guidance

Commands (gate by default, like the family CI convention):

    check_translations.py                     # gate: exit 1 on missing/extra keys
    check_translations.py --locale el         # check one locale only
    check_translations.py --fix               # LLM-translate missing keys (print)
    check_translations.py --fix --apply       # write results into the dictionaries
    check_translations.py --review            # LLM quality-review (print)
    check_translations.py --review --apply    # apply only entries marked "improved"
    check_translations.py --self-test         # offline self-tests (no config needed)
    check_translations.py --list-locales      # show source + target locales with key counts
    check_translations.py --add-locale fr --name French [--translate]
                                              # create + register a locale; --translate
                                              # fills it immediately (writes unless --dry-run)
    check_translations.py --init-env          # create .env or append only the missing
                                              # TRANSLATION_* fields (never overwrites)

Shared flags: --locale, --apply, --dry-run, --batch-size, --concurrency,
--verbose/-v, --quiet/-q.

Environment (or .env in the repo root) for --fix/--review:
    TRANSLATION_API_KEY      API key for the OpenAI-compatible provider
    TRANSLATION_BASE_URL     default https://api.openai.com/v1
    TRANSLATION_MODEL        model name
    TRANSLATION_TEMPERATURE  set empty for models that reject temperature
    TRANSLATION_REASONING    optional reasoning_effort (reasoning models)
    TRANSLATION_TIMEOUT, TRANSLATION_MAX_TOKENS, TRANSLATION_MAX_TOKENS_PARAM,
    TRANSLATION_BATCH_SIZE, TRANSLATION_CONCURRENCY, TRANSLATION_RETRIES

Exit codes: 0 success · 1 usage/config error or failed gate · 2 run completed
but some translations failed.
"""

from __future__ import annotations

import argparse
import http.client
import json
import logging
import os
import re
import shutil
import sys
import time
import tomllib
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("translations")

TEMPLATE_VERSION = "1.5.6"

LOCALE_CODE_RE = re.compile(r"^[a-z]{2,3}(?:-[A-Za-z]{2,4})?$")

ENV_REQUIRED_KEYS = ("TRANSLATION_API_KEY", "TRANSLATION_BASE_URL", "TRANSLATION_MODEL")

ENV_FRESH_BLOCK = """\
# --- Translation manager (scripts/translations/check_translations.py) — fill in values ---
TRANSLATION_API_KEY=
TRANSLATION_BASE_URL=https://api.openai.com/v1
TRANSLATION_MODEL=
# Optional overrides (uncomment to change the translations.toml defaults):
# TRANSLATION_TEMPERATURE=0.3
# TRANSLATION_REASONING=
# TRANSLATION_TIMEOUT=60
# TRANSLATION_MAX_TOKENS=4096
# TRANSLATION_MAX_TOKENS_PARAM=max_tokens
# TRANSLATION_BATCH_SIZE=20
# TRANSLATION_CONCURRENCY=3
# TRANSLATION_RETRIES=3
"""

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_FAILED = 2

BRAND_NOTE = (
    "NEVER translate brand/product names: Neuronection, Health Assistant, "
    "Career Assistant, Study Assistant, Desktop Assistant, GitHub, Docker, "
    "OpenAI, OpenRouter, Ollama, LM Studio, Whisper, LangChain, Apache-2.0, GHCR."
)


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

class Config:
    def __init__(self, root: Path, raw: dict[str, Any]):
        self.root = root
        self.project = raw.get("project", {}).get("name", "Neuronection app")
        self.source = raw.get("source", {}).get("locale", "en")
        dicts = raw.get("dictionaries", {})
        self.dict_dir = root / dicts.get("dir", "src/i18n/dictionaries")
        self.dict_format = dicts.get("format", "json")
        if self.dict_format not in ("json", "ts"):
            sys.exit(f"Error: [dictionaries].format must be 'json' or 'ts', got '{self.dict_format}'")
        self.locales = dicts.get("locales") or None
        prompts = raw.get("prompts", {})
        self.prompts_dir = root / prompts.get("dir", "scripts/translations/prompts") if prompts.get("dir") else None
        llm = raw.get("llm", {})
        self.temperature = llm.get("temperature", 0.3)
        self.timeout = llm.get("timeout", 60)
        self.max_tokens = llm.get("max_tokens", 4096)
        self.max_tokens_param = llm.get("max_tokens_param")
        self.batch_size = llm.get("batch_size", 20)
        self.concurrency = llm.get("concurrency", 3)
        self.retries = llm.get("retries", 3)
        self.fail_on_extra = raw.get("gate", {}).get("fail_on_extra", True)
        self.locale_names = {
            code: (meta.get("name") or code)
            for code, meta in raw.get("locales", {}).items()
        }

    def name_of(self, locale: str) -> str:
        return self.locale_names.get(locale, locale)

    def dict_path(self, locale: str) -> Path:
        return self.dict_dir / f"{locale}.{'ts' if self.dict_format == 'ts' else 'json'}"


def load_config() -> Config:
    root = Path(__file__).resolve().parents[2]
    cfg_path = Path(__file__).resolve().with_name("translations.toml")
    if not cfg_path.is_file():
        sys.exit(f"Error: config not found at {cfg_path} (copy translations.toml.example)")
    with open(cfg_path, "rb") as fh:
        raw = tomllib.load(fh)
    return Config(root, raw)


def load_env(root: Path) -> None:
    env_file = root / ".env"
    if not env_file.is_file():
        return
    for line in env_file.read_text(encoding="utf-8").split("\n"):
        m = re.match(r"^\s*([A-Z0-9_]+)\s*=\s*(.*)\s*$", line)
        if m and m.group(1) not in os.environ:
            os.environ[m.group(1)] = m.group(2).strip().strip('"').strip("'")


def env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name, default)
    return value if value not in (None, "") else default


def env_number(name: str, default: float) -> float:
    raw = env(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        sys.exit(f"Error: {name} must be a number, got '{raw}'")


# --------------------------------------------------------------------------- #
# Dictionary I/O — .json and .ts, one representation: flat "dotted.numeric" keys
# --------------------------------------------------------------------------- #

def read_nested(cfg: Config, locale: str) -> Any:
    path = cfg.dict_path(locale)
    if not path.is_file():
        sys.exit(f"Error: dictionary not found at {path}")
    text = path.read_text(encoding="utf-8")
    if cfg.dict_format == "json":
        return json.loads(text)
    return parse_ts_object(text, locale)


def read_dict(cfg: Config, locale: str) -> dict[str, str]:
    return flatten(read_nested(cfg, locale))


def flatten_values(obj: Any, prefix: str = "") -> dict[str, Any]:
    """flatten() that keeps the original leaf types (numbers/booleans survive)."""
    out: dict[str, Any] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = f"{prefix}.{k}" if prefix else str(k)
            out.update(flatten_values(v, key))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out.update(flatten_values(v, f"{prefix}.{i}"))
    else:
        out[prefix] = obj
    return out


def flatten(obj: Any, prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = f"{prefix}.{k}" if prefix else str(k)
            out.update(flatten(v, key))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out.update(flatten(v, f"{prefix}.{i}"))
    else:
        out[prefix] = "" if obj is None else str(obj)
    return out


def unflatten(flat: dict[str, str]) -> Any:
    root: Any = {}
    for key, value in flat.items():
        parts = key.split(".")
        node = root
        for i, part in enumerate(parts):
            if isinstance(node, list):
                idx = int(part)
                while len(node) <= idx:
                    node.append(None)
                if i == len(parts) - 1:
                    node[idx] = value
                else:
                    if node[idx] is None:
                        node[idx] = {} if not parts[i + 1].isdigit() else []
                    node = node[idx]
            else:
                if i == len(parts) - 1:
                    node[part] = value
                else:
                    nxt = parts[i + 1]
                    child = node.get(part)
                    if not isinstance(child, (dict, list)):
                        node[part] = [] if nxt.isdigit() else {}
                    node = node[part]
    normalize_arrays(root)
    return root


def normalize_arrays(node: Any) -> None:
    if isinstance(node, dict):
        for k, v in list(node.items()):
            normalize_arrays(v)
            if isinstance(v, dict) and v and all(kk.isdigit() for kk in v):
                items = [v[str(i)] for i in range(len(v))]
                if all(str(i) in v for i in range(len(items))):
                    node[k] = items
    elif isinstance(node, list):
        for item in node:
            normalize_arrays(item)


def deep_merge(base: dict, extra: dict) -> dict:
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def overlay_translations(data: Any, translations: dict[str, str]) -> Any:
    """Type-preserving overlay of flat translation strings onto a nested dict.

    Existing leaves keep their type unless explicitly translated (so numeric /
    boolean leaves survive --apply unchanged); translation keys whose path is
    absent from the structure are added as new string leaves.
    """
    seen: set[str] = set()

    def walk(node: Any, prefix: str) -> Any:
        if isinstance(node, dict):
            return {k: walk(v, f"{prefix}.{k}" if prefix else str(k)) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(v, f"{prefix}.{i}") for i, v in enumerate(node)]
        seen.add(prefix)
        return translations[prefix] if prefix in translations else node

    merged = walk(data, "")
    pending = {k: v for k, v in translations.items() if k not in seen}
    if pending:
        merged = deep_merge(merged, unflatten(pending))
    return merged


def write_dict(cfg: Config, locale: str, data: Any, backup: bool = True) -> None:
    path = cfg.dict_path(locale)
    if backup and path.exists():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        bak = path.with_name(f"{path.name}.bak.{stamp}")
        shutil.copy2(path, bak)
        logger.info("   💾 Backed up %s -> %s", path.name, bak.name)
    if cfg.dict_format == "json":
        text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    else:
        text = serialize_ts(locale, data)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


TS_STRING_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", '"': '"', "'": "'", "`": "`"}


def ts_export_name(locale: str) -> str:
    """Locale codes may contain '-' (pt-BR) — invalid as a TS identifier."""
    name = re.sub(r"[^A-Za-z0-9_$]", "", locale)
    return name if name and not name[0].isdigit() else "_" + name


def parse_ts_object(content: str, locale: str) -> Any:
    pattern = rf"export\s+const\s+{re.escape(ts_export_name(locale))}(?:\s*:[^=]+?)?\s*=\s*\{{"
    m = re.search(pattern, content)
    if not m:
        raise ValueError(f"no 'export const {ts_export_name(locale)}' object found")
    value, _ = _ts_value(content, m.end() - 1)
    return value


def _skip_ws_comments(content: str, i: int) -> int:
    n = len(content)
    while i < n:
        if content[i] in " \t\n\r":
            i += 1
        elif content[i : i + 2] == "//":
            while i < n and content[i] != "\n":
                i += 1
        elif content[i : i + 2] == "/*":
            i += 2
            while i < n - 1 and content[i : i + 2] != "*/":
                i += 1
            i += 2
        else:
            break
    return i


def _ts_value(content: str, start: int) -> tuple[Any, int]:
    """Parse one value; returns (value, index just after the value)."""
    i = _skip_ws_comments(content, start)
    ch = content[i]
    if ch == "{":
        value, end = _ts_object(content, i)
        return value, end + 1
    if ch == "[":
        value, end = _ts_array(content, i)
        return value, end + 1
    if ch in ('"', "'", "`"):
        value, end = _ts_string(content, i)
        return value, end + 1
    m = re.match(r"[^,}\]\n]+", content[i:])
    literal = m.group(0).strip()
    if literal in ("true", "false"):
        return literal == "true", i + len(literal)
    try:
        return (int(literal) if re.fullmatch(r"-?\d+", literal) else float(literal)), i + len(literal)
    except ValueError:
        return literal, i + len(literal)


def _ts_object(content: str, start: int) -> tuple[dict, int]:
    result: dict[str, Any] = {}
    i = start + 1
    n = len(content)
    while True:
        i = _skip_ws_comments(content, i)
        if i >= n or content[i] == "}":
            return result, i
        key_m = re.match(r'(?:"((?:\\.|[^"])*)"|\'((?:\\.|[^\'])*)\'|(\w+))\s*:', content[i:])
        if not key_m:
            i += 1
            continue
        key = next(g for g in key_m.groups() if g is not None)
        key = re.sub(r"\\(.)", r"\1", key)
        i = _skip_ws_comments(content, i + key_m.end())
        value, i = _ts_value(content, i)
        result[key] = value
        i = _skip_ws_comments(content, i)
        if i < n and content[i] == ",":
            i += 1


def _ts_array(content: str, start: int) -> tuple[list, int]:
    result: list[Any] = []
    i = start + 1
    n = len(content)
    while True:
        i = _skip_ws_comments(content, i)
        if i >= n:
            return result, i
        if content[i] == "]":
            return result, i
        value, i = _ts_value(content, i)
        result.append(value)
        i = _skip_ws_comments(content, i)
        if i < n and content[i] == ",":
            i += 1


def _ts_string(content: str, start: int) -> tuple[str, int]:
    quote = content[start]
    i = start + 1
    n = len(content)
    out: list[str] = []
    while i < n:
        ch = content[i]
        if ch == "\\" and i + 1 < n:
            nxt = content[i + 1]
            if nxt == "u" and content[i + 2 : i + 4] == "{":
                close = content.find("}", i + 3)
                if close != -1:
                    try:
                        out.append(chr(int(content[i + 3 : close], 16)))
                        i = close + 1
                        continue
                    except ValueError:
                        pass
            if nxt == "u" and re.fullmatch(r"[0-9a-fA-F]{4}", content[i + 2 : i + 6]):
                out.append(chr(int(content[i + 2 : i + 6], 16)))
                i += 6
                continue
            if nxt == "x" and re.fullmatch(r"[0-9a-fA-F]{2}", content[i + 2 : i + 4]):
                out.append(chr(int(content[i + 2 : i + 4], 16)))
                i += 4
                continue
            out.append(TS_STRING_ESCAPES.get(nxt, nxt))
            i += 2
            continue
        if ch == quote:
            return "".join(out), i
        out.append(ch)
        i += 1
    return "".join(out), i


def ts_serialize_value(value: Any, indent: int) -> str:
    pad = "  " * indent
    inner = "  " * (indent + 1)
    if isinstance(value, dict):
        if not value:
            return "{}"
        lines = ["{"]
        for k, v in value.items():
            lines.append(f"{inner}{k}: {ts_serialize_value(v, indent + 1)},")
        lines.append(f"{pad}}}")
        return "\n".join(lines)
    if isinstance(value, list):
        if not value:
            return "[]"
        lines = ["["]
        for item in value:
            lines.append(f"{inner}{ts_serialize_value(item, indent + 1)},")
        lines.append(f"{pad}]")
        return "\n".join(lines)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{escaped}"'


def serialize_ts(locale: str, data: Any) -> str:
    name = ts_export_name(locale)
    lines = ['import type { Dictionary } from "./en";', "", f"export const {name}: Dictionary = {{"]
    for key, value in data.items():
        lines.append(f"  {key}: {ts_serialize_value(value, 1)},")
    lines += ["};", ""]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Glossary & prompt loading (central family files, repo-local overrides)
# --------------------------------------------------------------------------- #

def load_glossary(cfg: Config, locale: str) -> str:
    """Family glossary files (byte-identical across repos, drift-checked) with
    optional repo-local overrides: ``*.local.json`` merges on top, never the
    other way around."""
    terms: dict[str, str] = {}
    if cfg.prompts_dir:
        for name in (
            "glossary.json",
            "glossary.local.json",
            f"glossary.{locale}.json",
            f"glossary.{locale}.local.json",
        ):
            path = cfg.prompts_dir / name
            if path.is_file():
                try:
                    terms.update(json.loads(path.read_text(encoding="utf-8")))
                except (OSError, json.JSONDecodeError) as e:
                    logger.warning("Could not read %s: %s", path, e)
    if not terms:
        return "(none)"
    return json.dumps(terms, ensure_ascii=False, indent=2)


def load_instructions(cfg: Config, locale: str) -> str:
    """global.md → local.md → <locale>.md → <locale>.local.md (later wins)."""
    parts: list[str] = []
    if cfg.prompts_dir:
        for name in ("global.md", "local.md", f"{locale}.md", f"{locale}.local.md"):
            path = cfg.prompts_dir / "instructions" / name
            if path.is_file():
                parts.append(path.read_text(encoding="utf-8").strip())
    return "\n\n".join(parts) or "(none)"


def load_prompt(cfg: Config, name: str, kind: str) -> str:
    if cfg.prompts_dir:
        path = cfg.prompts_dir / name
        if path.is_file():
            return path.read_text(encoding="utf-8")
        sys.exit(
            f"Error: {kind} prompt not found at {path}.\n"
            f"Copy the shared prompts/ tree into the repo's translations directory."
        )
    sys.exit(f"Error: [prompts].dir not set in translations.toml — required for --{kind}")


# --------------------------------------------------------------------------- #
# LLM client (OpenAI-compatible, stdlib urllib)
# --------------------------------------------------------------------------- #

class LLM:
    def __init__(self, cfg: Config):
        self.api_key = env("TRANSLATION_API_KEY")
        self.base_url = (env("TRANSLATION_BASE_URL", "https://api.openai.com/v1") or "").rstrip("/")
        self.model = env("TRANSLATION_MODEL")
        self.temperature = env_number("TRANSLATION_TEMPERATURE", cfg.temperature)
        self.timeout = int(env_number("TRANSLATION_TIMEOUT", cfg.timeout))
        self.max_tokens = int(env_number("TRANSLATION_MAX_TOKENS", cfg.max_tokens))
        self.max_tokens_param = env("TRANSLATION_MAX_TOKENS_PARAM", cfg.max_tokens_param)
        self.reasoning = env("TRANSLATION_REASONING")
        self.retries = int(env_number("TRANSLATION_RETRIES", cfg.retries))
        if not self.api_key or not self.model:
            sys.exit(
                "Error: --fix/--review require TRANSLATION_API_KEY and TRANSLATION_MODEL (env or .env).\n"
                "Run: python3 scripts/translations/check_translations.py --init-env   "
                "# create/append the .env template, then fill in the values"
            )
        # Reasoning models (gpt-5.x, o-series) reject `temperature`.
        self.send_temperature = not re.match(r"^(gpt-[5-9]|o\d)", self.model, re.I)
        # Modern OpenAI reasoning models require `max_completion_tokens`; retain
        # `max_tokens` for other OpenAI-compatible APIs unless explicitly configured.
        if self.max_tokens_param is None:
            self.max_tokens_param = (
                "max_completion_tokens"
                if re.match(r"^(gpt-[5-9]|o\d)", self.model, re.I)
                else "max_tokens"
            )

    def body(self, prompt: str) -> dict:
        body: dict[str, Any] = {
            "model": self.model,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "user", "content": prompt}],
        }
        if self.send_temperature:
            body["temperature"] = self.temperature
        if self.max_tokens_param != "none":
            body[self.max_tokens_param] = self.max_tokens
        if self.reasoning:
            body["reasoning_effort"] = self.reasoning
        return body

    RETRYABLE_HTTP = {429, 500, 502, 503, 504}

    def complete(self, prompt: str) -> tuple[str, dict]:
        """POST one chat completion, retrying transient failures with backoff.

        Retries: 429/5xx, network and timeout errors. Auth/unknown-model/
        malformed-request (4xx except 429) fail immediately.
        """
        body = json.dumps(self.body(prompt)).encode("utf-8")
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            req = urllib.request.Request(
                f"{self.base_url}/chat/completions",
                data=body,
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"},
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as res:
                    payload = json.loads(res.read().decode("utf-8"))
                content = (payload.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()
                return content, payload.get("usage") or {}
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "replace")[:300]
                if e.code in self.RETRYABLE_HTTP and attempt < self.retries:
                    last = RuntimeError(f"HTTP {e.code}")
                else:
                    raise RuntimeError(f"LLM request failed ({e.code}): {detail}") from e
            except (urllib.error.URLError, http.client.HTTPException, OSError, TimeoutError) as e:
                if attempt < self.retries:
                    last = e
                else:
                    raise RuntimeError(f"LLM request failed: {e}") from e
            delay = min(30.0, 2.0 * (attempt + 1))
            logger.warning("   ⚠️  attempt %d/%d failed (%s) — retrying in %.0fs",
                           attempt + 1, self.retries + 1, last, delay)
            time.sleep(delay)
        raise RuntimeError(f"LLM request failed after {self.retries + 1} attempts: {last}")


def parse_json_response(content: str) -> Any:
    return json.loads(re.sub(r"^```json\s*|```\s*$", "", content).strip() or "{}")


def render_prompt(template: str, cfg: Config, locale: str, items: Any) -> str:
    return (
        template
        .replace("{{project}}", cfg.project)
        .replace("{{source_locale}}", cfg.source)
        .replace("{{target_locale}}", locale)
        .replace("{{target_name}}", cfg.name_of(locale))
        .replace("{{glossary}}", load_glossary(cfg, locale))
        .replace("{{instructions}}", load_instructions(cfg, locale))
        .replace("{{items}}", json.dumps(items, ensure_ascii=False, indent=2))
    )


# --------------------------------------------------------------------------- #
# Guardrails
# --------------------------------------------------------------------------- #

def tags_of(s: str) -> str:
    return "".join(sorted(re.findall(r"<[^>]+>", s)))


PLACEHOLDER_RE = re.compile(r"\{\{[^{}]*\}\}|\{[^{}]+\}")


def placeholders_of(s: str) -> str:
    """Canonical form of every placeholder ({{i18next}} and {single} styles)."""
    return "".join(sorted(PLACEHOLDER_RE.findall(s)))


def guard_translation(key: str, source: str, candidate: Any) -> str | None:
    """Return the cleaned candidate, or None when it must be rejected."""
    if not isinstance(candidate, str):
        logger.warning("   ⚠️  %s: non-string response — skipped", key)
        return None
    candidate = candidate.strip()
    if not candidate:
        logger.warning("   ⚠️  %s: empty translation — skipped", key)
        return None
    if tags_of(source) != tags_of(candidate):
        logger.warning("   ⚠️  %s: inline markup would change — skipped", key)
        return None
    if placeholders_of(source) != placeholders_of(candidate):
        logger.warning("   ⚠️  %s: placeholders would change (%s → %s) — skipped",
                       key, placeholders_of(source) or "none", placeholders_of(candidate) or "none")
        return None
    return candidate


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #

def target_locales(cfg: Config, only: str | None) -> list[str]:
    locales = cfg.locales
    if locales is None:
        locales = sorted(
            p.stem for p in cfg.dict_dir.glob("*.ts")
        ) if cfg.dict_format == "ts" else sorted(
            p.stem for p in cfg.dict_dir.glob("*.json")
        )
    locales = [l for l in locales if l != cfg.source]
    if only:
        locales = [only]
    return locales


def compare(source_flat: dict[str, str], locale_flat: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    missing = {k: v for k, v in source_flat.items() if k not in locale_flat or locale_flat[k].strip() == ""}
    extra = [k for k in locale_flat if k not in source_flat]
    return missing, extra


def run_check(cfg: Config, llm: LLM | None, locales: list[str], args) -> int:
    source_flat = read_dict(cfg, cfg.source)
    exit_code = EXIT_OK
    total_missing = 0

    for locale in locales:
        path = cfg.dict_path(locale)
        if not path.is_file():
            print(f"❌ {locale}: dictionary missing ({path.name})")
            exit_code = EXIT_USAGE
            continue
        locale_flat = read_dict(cfg, locale)
        missing, extra = compare(source_flat, locale_flat)
        if not missing and not extra:
            print(f"✅ {locale}: complete ({len(locale_flat)} keys)")
            continue
        if missing:
            total_missing += len(missing)
            print(f"❌ {locale}: {len(missing)} missing/empty key(s):")
            for k in missing:
                print(f"   • {k}")
        if extra:
            level = "❌" if cfg.fail_on_extra else "⚠️ "
            print(f"{level} {locale}: {len(extra)} extra key(s) not in {cfg.source}:")
            for k in extra:
                print(f"   • {k}")
            if cfg.fail_on_extra:
                exit_code = EXIT_USAGE

    for locale in locales:
        if not cfg.dict_path(locale).is_file():
            print(f"⚠️  locale '{locale}' has no dictionary file")
    if total_missing:
        if not args.fix and not args.review:
            print("\nRun with --fix to auto-generate translations.")
        return EXIT_USAGE
    if exit_code != EXIT_OK:
        return exit_code
    print("\n✅ All translations are complete!")
    return EXIT_OK


def run_fix(cfg: Config, llm: LLM, locales: list[str], args) -> int:
    source_data = read_nested(cfg, cfg.source)
    source_values = flatten_values(source_data)
    source_flat = flatten(source_data)
    template = load_prompt(cfg, "translate.md", "fix")
    batch_size = max(1, int(args.batch_size))
    had_usage = False
    had_fail = False

    for locale in locales:
        if not cfg.dict_path(locale).is_file():
            print(f"❌ {locale}: dictionary missing ({cfg.dict_path(locale).name})")
            had_usage = True
            continue
        locale_data = read_nested(cfg, locale)
        locale_flat = flatten(locale_data)
        missing = {k: v for k, v in compare(source_flat, locale_flat)[0].items()
                   if isinstance(source_values.get(k), str)}
        # Non-string source leaves (numbers, booleans) are copied verbatim,
        # never offered to the LLM.
        verbatim = {k: source_values[k] for k in source_values
                    if k not in locale_flat and not isinstance(source_values[k], str)}
        if verbatim:
            print(f"   • {locale}: copying {len(verbatim)} non-string leaf(s) verbatim")
        translations: dict[str, str] = {}
        failures = 0
        tokens = {"prompt": 0, "completion": 0}

        if missing:
            keys = list(missing)
            batches = [keys[i : i + batch_size] for i in range(0, len(keys), batch_size)]
            print(f"🔧 {locale}: translating {len(keys)} key(s) with {llm.model} in {len(batches)} batch(es) of ≤{batch_size}…")

            def work(chunk: list[str]) -> tuple[dict[str, str], int, int]:
                prompt = render_prompt(template, cfg, locale, {k: missing[k] for k in chunk})
                content, usage = llm.complete(prompt)
                parsed = parse_json_response(content)
                if not isinstance(parsed, dict):
                    raise ValueError("unexpected response shape")
                return parsed, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)

            with ThreadPoolExecutor(max_workers=max(1, int(args.concurrency))) as pool:
                futures = {pool.submit(work, chunk): chunk for chunk in batches}
                for future in as_completed(futures):
                    chunk = futures[future]
                    try:
                        parsed, pt, ct = future.result()
                    except (RuntimeError, ValueError, json.JSONDecodeError) as e:
                        logger.warning("   ⚠️  batch of %d key(s) failed: %s", len(chunk), e)
                        failures += len(chunk)
                        continue
                    tokens["prompt"] += pt
                    tokens["completion"] += ct
                    for key in chunk:
                        guarded = guard_translation(key, missing[key], parsed.get(key))
                        if guarded is not None:
                            translations[key] = guarded
                        else:
                            failures += 1

            for key in sorted(translations):
                print(f"   • {key}: \"{translations[key][:80]}\"")
        else:
            print(f"✅ {locale}: nothing to translate")

        if failures:
            had_fail = True
            print(f"   ⚠️  {failures} key(s) failed or rejected (kept as-is).")
        print(f"   ↑{tokens['prompt']} ↓{tokens['completion']} tok")

        payload = {**verbatim, **translations}
        if payload and args.apply and not args.dry_run:
            write_dict(cfg, locale, overlay_translations(locale_data, payload))
            print(f"   ✍️  written to dictionaries/{cfg.dict_path(locale).name}\n")
        elif payload:
            print("   (dry run — re-run with --apply to write)\n")
    if had_usage:
        return EXIT_USAGE
    return EXIT_FAILED if had_fail else EXIT_OK


def run_review(cfg: Config, llm: LLM, locales: list[str], args) -> int:
    source_values = flatten_values(read_nested(cfg, cfg.source))
    source_flat = flatten(source_values)
    template = load_prompt(cfg, "review.md", "review")
    batch_size = max(1, int(args.batch_size))
    had_usage = False
    had_fail = False

    for locale in locales:
        if not cfg.dict_path(locale).is_file():
            print(f"❌ {locale}: dictionary missing ({cfg.dict_path(locale).name})")
            had_usage = True
            continue
        locale_data = read_nested(cfg, locale)
        locale_flat = flatten(locale_data)
        missing, _ = compare(source_flat, locale_flat)
        if missing:
            logger.warning("   ⚠️  %s: %d key(s) missing — reviewing existing keys only (run --fix first)", locale, len(missing))
        items = {
            k: {"en": v, "current": locale_flat[k]}
            for k, v in source_flat.items()
            if k in locale_flat and locale_flat[k].strip() != ""
            and isinstance(source_values.get(k), str)
        }
        if not items:
            print(f"✅ {locale}: nothing to review")
            continue
        keys = list(items)
        batches = [keys[i : i + batch_size] for i in range(0, len(keys), batch_size)]
        print(f"🔍 {locale}: reviewing {len(keys)} key(s) with {llm.model} in {len(batches)} batch(es) of ≤{batch_size}…")

        improvements: dict[str, str] = {}
        ok_count = 0
        failed = 0
        failed_batches = 0
        tokens = {"prompt": 0, "completion": 0}
        started = time.time()

        def work(chunk: list[str]) -> tuple[dict, int, int]:
            prompt = render_prompt(template, cfg, locale, {k: items[k] for k in chunk})
            content, usage = llm.complete(prompt)
            parsed = parse_json_response(content)
            reviews = parsed.get("reviews") if isinstance(parsed, dict) else None
            if not isinstance(reviews, dict):
                raise ValueError("unexpected response shape")
            return reviews, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)

        with ThreadPoolExecutor(max_workers=max(1, int(args.concurrency))) as pool:
            futures = {pool.submit(work, chunk): chunk for chunk in batches}
            for future in as_completed(futures):
                chunk = futures[future]
                try:
                    reviews, pt, ct = future.result()
                except (RuntimeError, ValueError, json.JSONDecodeError) as e:
                    logger.warning("   ⚠️  batch of %d key(s) failed: %s", len(chunk), e)
                    failed += len(chunk)
                    failed_batches += 1
                    continue
                tokens["prompt"] += pt
                tokens["completion"] += ct
                for key in chunk:
                    verdict = reviews.get(key) or {}
                    if verdict.get("verdict") != "improved":
                        ok_count += 1
                        continue
                    guarded = guard_translation(key, items[key]["en"], verdict.get("improved"))
                    if guarded is None or guarded == items[key]["current"]:
                        ok_count += 1
                        continue
                    improvements[key] = guarded
                    reason = verdict.get("reason", "no reason given")
                    print(f"   • {key}\n     - {items[key]['current']}\n     + {guarded}\n     ({reason})")

        reviewed = ok_count + len(improvements)
        pct = lambda part: f"{round(part / reviewed * 100)}%" if reviewed else "—"
        print(
            f"📊 {locale}: {reviewed}/{len(items)} reviewed — {ok_count} ok ({pct(ok_count)}), "
            f"{len(improvements)} improved ({pct(len(improvements))}) · "
            f"{len(batches) - failed_batches}/{len(batches)} batches ok · "
            f"{time.time() - started:.0f}s · ↑{tokens['prompt']} ↓{tokens['completion']} tok"
        )
        if failed:
            had_fail = True

        if improvements and args.apply and not args.dry_run:
            write_dict(cfg, locale, overlay_translations(locale_data, improvements))
            print(f"\n   ✍️  written to dictionaries/{cfg.dict_path(locale).name}")
        elif improvements:
            print("\n   (dry run — re-run with --apply to write)")
    if had_usage:
        return EXIT_USAGE
    return EXIT_FAILED if had_fail else EXIT_OK


def run_list_locales(cfg: Config) -> int:
    if not cfg.dict_dir.is_dir():
        sys.exit(f"Error: dictionary dir not found: {cfg.dict_dir}")
    src_path = cfg.dict_path(cfg.source)
    src_keys = len(flatten(read_nested(cfg, cfg.source))) if src_path.is_file() else 0
    print(f"* {cfg.source} (source) — {src_keys} keys")
    for locale in target_locales(cfg, None):
        path = cfg.dict_path(locale)
        if not path.is_file():
            print(f"  {locale} ({cfg.name_of(locale)}) — NO FILE")
        else:
            print(f"  {locale} ({cfg.name_of(locale)}) — {len(read_dict(cfg, locale))} keys")
    return EXIT_OK


# --------------------------------------------------------------------------- #
# Init .env (create / append-only)
# --------------------------------------------------------------------------- #

def env_keys_present(text: str) -> set[str]:
    """Uncommented KEY=value lines only — commented-out lines don't count."""
    return set(re.findall(r"^([A-Z0-9_]+)\s*=", text, re.M))


def missing_env_keys(text: str) -> list[str]:
    present = env_keys_present(text)
    return [k for k in ENV_REQUIRED_KEYS if k not in present]


def run_init_env(cfg: Config) -> int:
    env_path = cfg.root / ".env"
    existing = env_path.read_text(encoding="utf-8") if env_path.is_file() else ""
    missing = missing_env_keys(existing)
    if not missing:
        print(f"✅ {env_path}: all TRANSLATION_* keys already present — nothing changed")
        return EXIT_OK

    if not existing:
        content = ENV_FRESH_BLOCK
        print(f"✅ created {env_path}")
    else:
        sep = "" if existing.endswith("\n") else "\n"
        lines = ["", "# --- Translation manager (scripts/translations/check_translations.py) — fill in values ---"]
        lines += [f"{k}=" + ("https://api.openai.com/v1" if k == "TRANSLATION_BASE_URL" else "")
                  for k in missing]
        content = existing + sep + "\n".join(lines) + "\n"
        print(f"✅ {env_path}: appended {len(missing)} missing field(s) — existing lines untouched")
    env_path.write_text(content, encoding="utf-8")

    gitignore = cfg.root / ".gitignore"
    if not gitignore.is_file() or ".env" not in gitignore.read_text(encoding="utf-8"):
        print("⚠️  .env is not covered by .gitignore — add it before committing secrets")
    print("Fill in TRANSLATION_API_KEY and TRANSLATION_MODEL, then re-run your command.")
    return EXIT_OK


# --------------------------------------------------------------------------- #
# Add a locale
# --------------------------------------------------------------------------- #

def add_locale_to_toml(text: str, code: str, name: str | None) -> tuple[str, list[str]]:
    """Register a locale in translations.toml content (pure; returns new text).

    Adds the code to an explicit ``locales = [...]`` list (auto-detect setups
    need no edit) and appends a ``[locales.<code>]`` display-name section.
    """
    notes: list[str] = []
    m = re.search(r"^locales\s*=\s*\[([^\]]*)\]\s*$", text, re.M)
    if m:
        items = [i.strip().strip("\"'") for i in m.group(1).split(",") if i.strip()]
        if code in items:
            notes.append(f"locales list already contains '{code}'")
        else:
            items.append(code)
            new_line = "locales = [" + ", ".join(f'"{i}"' for i in items) + "]"
            text = text[: m.start()] + new_line + text[m.end():]
            notes.append(f"locales list: +\"{code}\"")
    else:
        notes.append("no explicit locales list — auto-detect picks up the new file")
    if name:
        header = f"[locales.{code}]"
        if re.search(rf"^{re.escape(header)}\s*$", text, re.M):
            notes.append(f"{header} already exists — name not changed")
        else:
            text = text.rstrip("\n") + f"\n\n{header}\nname = \"{name}\"\n"
            notes.append(f"{header} name = \"{name}\" appended")
    return text, notes


def run_add_locale(cfg: Config, args) -> int:
    code = args.add_locale
    if not LOCALE_CODE_RE.match(code):
        sys.exit(f"Error: invalid locale code '{code}' (expected like 'el', 'de', 'pt-BR')")
    if code == cfg.source:
        sys.exit(f"Error: '{code}' is the source locale")
    if cfg.dict_path(code).is_file():
        sys.exit(f"Error: dictionary already exists: {cfg.dict_path(code)}")

    path = cfg.dict_path(code)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "{}\n" if cfg.dict_format == "json" else serialize_ts(code, {}),
        encoding="utf-8")
    print(f"✅ created {path} ({cfg.dict_format})")

    toml_path = Path(__file__).resolve().with_name("translations.toml")
    text, notes = add_locale_to_toml(toml_path.read_text(encoding="utf-8"), code, args.name)
    toml_path.write_text(text, encoding="utf-8")
    for note in notes:
        print(f"   • {note}")

    print(f"\nNext: python3 {Path(__file__).name} --fix --locale {code} --apply")
    if args.translate:
        if not args.dry_run:
            args.apply = True
        args.locale = code
        return run_fix(cfg, LLM(cfg), [code], args)
    return EXIT_OK


# --------------------------------------------------------------------------- #
# Self-test (offline, no config / network)
# --------------------------------------------------------------------------- #

def run_self_test() -> int:
    logging.disable(logging.CRITICAL)
    failures: list[str] = []
    total = 0

    def check(name: str, cond: bool) -> None:
        nonlocal total
        total += 1
        print(f"  {'✅' if cond else '❌'} {name}")
        if not cond:
            failures.append(name)

    print("self-test:")

    sample = {"a": {"b": "x", "c": ["p", "q"]}, "d": "y"}
    flat = flatten(sample)
    check("flatten dotted/array keys",
          flat == {"a.b": "x", "a.c.0": "p", "a.c.1": "q", "d": "y"})
    check("unflatten round-trip", unflatten(flat) == sample)
    check("unflatten numeric objects become arrays",
          unflatten({"a.0": "x", "a.1": "y"}) == {"a": ["x", "y"]})

    ts_source = r'''import type { Dictionary } from "./en";
// header comment
export const el: Dictionary = {
  nav: { docs: "Εγχειρίδιο" /* inline */, home: 'Αρχική' },
  list: ["α", "β"],
  esc: "line\nbreak \u201cquoted\u201d back\\slash {keep}",
  num: 5,
  flag: true,
};
'''
    parsed = parse_ts_object(ts_source, "el")
    check("ts parse escapes/newlines",
          parsed["esc"] == "line\nbreak \u201cquoted\u201d back\\slash {keep}")
    check("ts parse quotes/comments/lists/scalars",
          parsed["nav"] == {"docs": "Εγχειρίδιο", "home": "Αρχική"}
          and parsed["list"] == ["α", "β"] and parsed["num"] == 5 and parsed["flag"] is True)
    check("ts serialize round-trip",
          parse_ts_object(serialize_ts("el", parsed), "el") == parsed)
    check("ts export name sanitizing",
          ts_export_name("pt-BR") == "ptBR" and ts_export_name("en") == "en"
          and parse_ts_object(serialize_ts("pt-BR", parsed), "pt-BR") == parsed)

    typed = {"num": 5, "flag": True, "s": "old", "arr": ["a"], "n": {"keep": 1.5}}
    overlaid = overlay_translations(typed, {"s": "new", "n.added": "x", "brand": "new-root"})
    check("overlay preserves typed leaves, adds absent keys",
          overlaid == {"num": 5, "flag": True, "s": "new", "arr": ["a"],
                       "n": {"keep": 1.5, "added": "x"}, "brand": "new-root"})
    check("flatten_values keeps types",
          flatten_values(typed) == {"num": 5, "flag": True, "s": "old",
                                    "arr.0": "a", "n.keep": 1.5})
    check("verbatim typed copies round-trip through unflatten",
          unflatten({"num": 5, "flag": True, "n.keep": 1.5})
          == {"num": 5, "flag": True, "n": {"keep": 1.5}})

    check("placeholder extraction",
          placeholders_of("Hi {{name}}, see {link}!") == "{link}{{name}}")
    check("guard accepts valid translation",
          guard_translation("k", "Use {{query}} <b>here</b>", "Χρησιμοποιήστε {{query}} <b>εδώ</b>")
          == "Χρησιμοποιήστε {{query}} <b>εδώ</b>")
    check("guard rejects dropped placeholder",
          guard_translation("k", "Use {{query}} here", "Χρησιμοποιήστε {{qury}} εδώ") is None)
    check("guard rejects changed markup",
          guard_translation("k", "<b>bold</b>", "<i>bold</i>") is None)
    check("guard rejects empty/non-string",
          guard_translation("k", "src", "   ") is None and guard_translation("k", "src", 5) is None)

    cfg = Config(Path("."), {
        "project": {"name": "SelfTest"},
        "source": {"locale": "en"},
        "dictionaries": {"dir": ".", "format": "json"},
        "locales": {"el": {"name": "Greek"}},
    })
    rendered = render_prompt(
        "{{project}} {{source_locale}} {{target_locale}} {{target_name}} {{glossary}} {{instructions}} {{items}}",
        cfg, "el", {"k": "v"})
    check("prompt placeholders substituted",
          "{{" not in rendered and "SelfTest" in rendered and "Greek" in rendered)

    missing, extra = compare({"a": "1", "b": "2", "c": "3"}, {"a": "1", "b": "  ", "z": "9"})
    check("compare missing/empty/extra", set(missing) == {"b", "c"} and extra == ["z"])

    check("locale code validation",
          all(LOCALE_CODE_RE.match(c) for c in ("el", "de", "pt-BR", "fil"))
          and not any(LOCALE_CODE_RE.match(c) for c in ("EN", "e1", "-fr", "el-")))
    toml_text = '[dictionaries]\nlocales = ["de"]\nformat = "json"\n'
    new_text, notes = add_locale_to_toml(toml_text, "fr", "French")
    check("toml edit: list + name section",
          'locales = ["de", "fr"]' in new_text
          and '[locales.fr]\nname = "French"' in new_text
          and len(notes) == 2)
    new_text2, notes2 = add_locale_to_toml(new_text, "fr", "French")
    check("toml edit: idempotent", new_text2 == new_text
          and any("already" in n for n in notes2))
    auto_text, auto_notes = add_locale_to_toml('[dictionaries]\nformat = "json"\n', "fr", None)
    check("toml edit: auto-detect setup untouched",
          auto_text == '[dictionaries]\nformat = "json"\n' and len(auto_notes) == 1)

    env_text = "# TRANSLATION_MODEL=commented-out\nTRANSLATION_API_KEY=abc\nOTHER=1\n"
    check("env keys: uncommented only",
          env_keys_present(env_text) == {"TRANSLATION_API_KEY", "OTHER"})
    check("env missing fields",
          missing_env_keys(env_text) == ["TRANSLATION_BASE_URL", "TRANSLATION_MODEL"]
          and missing_env_keys(ENV_FRESH_BLOCK) == [])

    logging.disable(logging.NOTSET)
    if failures:
        print(f"\nself-test FAILED: {len(failures)}/{total} check(s): {', '.join(failures)}")
        return EXIT_FAILED
    print(f"\nself-test passed ({total} checks)")
    return EXIT_OK


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #

def setup_logging(verbose: bool, quiet: bool) -> None:
    level = logging.WARNING if quiet else (logging.DEBUG if verbose else logging.INFO)
    logging.basicConfig(level=level, format="%(message)s")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Neuronection translation manager (family-unified)",
        epilog=(
            "examples:\n"
            "  # see what languages exist (key counts per locale)\n"
            "  %(prog)s --list-locales\n"
            "\n"
            "  # add a new language end-to-end: creates the dictionary, registers\n"
            "  # it in translations.toml, then translates all keys in one go\n"
            "  %(prog)s --add-locale fr --name French --translate\n"
            "\n"
            "  # translate ONLY the missing keys (after UI changes added new keys)\n"
            "  %(prog)s --fix                       # preview, nothing written\n"
            "  %(prog)s --fix --apply               # write into the dictionaries\n"
            "  %(prog)s --fix --locale de --apply   # one locale only\n"
            "\n"
            "  # improve EXISTING translations (advisory quality pass over all keys;\n"
            "  # --apply writes only entries the model marked \"improved\")\n"
            "  %(prog)s --review --locale el        # one locale, proposals printed\n"
            "  %(prog)s --review --apply            # all locales, apply improvements\n"
            "\n"
            "  # first-time LLM setup (creates .env / appends missing fields only)\n"
            "  %(prog)s --init-env                  # then fill TRANSLATION_API_KEY/MODEL\n"
            "\n"
            "  # safety & diagnostics: --dry-run (with --apply), --self-test, --version\n"
            "\n"
            "Config: translations.toml next to this script (family-unified script —\n"
            "edit the config, not the script)\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version",
                        version=f"check_translations {TEMPLATE_VERSION} (family template)")
    parser.add_argument("--self-test", action="store_true",
                        help="run offline self-tests of the parsing/guard helpers and exit")
    parser.add_argument("--init-env", action="store_true",
                        help="create .env, or append only missing TRANSLATION_* fields (never overwrites)")
    parser.add_argument("--list-locales", action="store_true",
                        help="list source + target locales with key counts and exit")
    parser.add_argument("--fix", action="store_true", help="LLM-translate missing keys")
    parser.add_argument("--review", action="store_true", help="LLM quality-review of existing translations")
    parser.add_argument("--locale", help="limit to one locale (e.g. el)")
    parser.add_argument("--add-locale", metavar="CODE",
                        help="create an empty dictionary for CODE and register it in translations.toml")
    parser.add_argument("--name", help="display name for --add-locale (e.g. 'French')")
    parser.add_argument("--translate", action="store_true",
                        help="with --add-locale: fill the new locale immediately (writes unless --dry-run)")
    parser.add_argument("--apply", action="store_true", help="write results into the dictionaries")
    parser.add_argument("--dry-run", action="store_true", help="with --apply: print instead of write")
    parser.add_argument("--batch-size", type=int, default=None, help="keys per request")
    parser.add_argument("--concurrency", type=int, default=None, help="parallel requests")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--quiet", "-q", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        return run_self_test()

    cfg = load_config()
    load_env(cfg.root)

    if args.init_env:
        return run_init_env(cfg)

    if args.list_locales:
        return run_list_locales(cfg)

    setup_logging(args.verbose, args.quiet)
    args.batch_size = args.batch_size or cfg.batch_size
    args.concurrency = args.concurrency or cfg.concurrency

    if args.fix and args.review:
        parser.error("--fix and --review are mutually exclusive")
    if args.apply and not (args.fix or args.review or args.add_locale):
        parser.error("--apply requires --fix or --review")
    if args.translate and not args.add_locale:
        parser.error("--translate requires --add-locale")
    if args.name and not args.add_locale:
        parser.error("--name requires --add-locale")

    if args.add_locale:
        return run_add_locale(cfg, args)
    if not cfg.dict_dir.is_dir():
        sys.exit(f"Error: dictionary dir not found: {cfg.dict_dir}")
    if not cfg.dict_path(cfg.source).is_file():
        sys.exit(f"Error: source dictionary not found: {cfg.dict_path(cfg.source)}")

    locales = target_locales(cfg, args.locale)

    llm = None
    if args.fix or args.review:
        llm = LLM(cfg)

    if args.review:
        return run_review(cfg, llm, locales, args)
    if args.fix:
        return run_fix(cfg, llm, locales, args)
    return run_check(cfg, llm, locales, args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(130)
