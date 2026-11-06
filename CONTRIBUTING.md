# Contributing

Thanks for contributing to Health Assistant — part of the Neuronection
assistant family.

## Ground rules

1. **Never commit untested code.** Run the verification gate (see the repo
   AGENTS.md / CI) before every commit; tests for new behavior land in the
   same commit.
2. **Docs ship with code.** Behavior/schema/API changes update docs and
   `CHANGELOG.md` (`## [Unreleased]`) in the same commit.
3. **No comments in code** unless requested; mimic existing style.
4. **Secrets stay out.** `.env` is gitignored; never commit keys or tokens.
5. Shared UI comes from `@neuronection/assistant-ui` (first-party family
   library — see the repo AGENTS.md); styling via `--as-*` tokens.

## Development setup

```bash
# backend (from core/backend)
uv sync
uv run pytest
uv run ruff check .

# frontend (from core/frontend)
npm ci
npm run build
npm run lint
```

## Pull requests

- One logical change per PR; verification gate green in CI.
- User-visible changes include a changelog entry.
- AI-related changes follow the repo's tracked AI/architecture docs
  (structured outputs, audited calls, schema-validated model output).
