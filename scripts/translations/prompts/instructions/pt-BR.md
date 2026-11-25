# Brazilian Portuguese Localization Instructions

## 1. Native Flow and Register
- Write natural Brazilian Portuguese, never European Portuguese. Preserve the
  message, purpose, and tone, but express them with native Brazilian rhythm
  rather than literal English structure.
- Use approachable `você` consistently unless a repository-local file specifies
  otherwise. Avoid unnecessary `você`, `seu`, and `sua` where verb forms or
  context already make ownership clear.
- Keep the voice modern, friendly, and direct. Avoid overly formal vocabulary,
  regional slang, and English-influenced sentence construction.

## 2. Style and Grammar
- Favor active verbs and short, clear sentences. Reorder or split clauses as
  needed for natural flow.
- Buttons and UI labels stay compact. Use sentence case for headings and labels;
  do not mimic English title case or overuse em dashes.
- Use Brazilian vocabulary consistently (`arquivo`, `aplicativo`, `computador`)
  rather than European Portuguese alternatives.

## 3. Avoid Literal Calques
- `under the hood` -> `Tecnologia`, `Detalhes técnicos`, or `Como funciona`
  (never `Debaixo do capô`).
- `on your machine / device` -> `no seu computador`, `no seu dispositivo`, or
  `localmente` (never `na sua máquina`).
- `bring your own [provider / AI / model]` -> `escolha seu provedor`, `use seu
  próprio modelo`, or `com a IA da sua preferência` (never `traga seu ...`).
- `lives in your system tray` -> `fica disponível na área de notificações`
  (never `vive na ...`).
- `one [hotkey / click] away` -> `com um atalho de teclado` or `a um clique`;
  `text dump` -> `texto sem estrutura`; `human-gated` -> `após sua aprovação`;
  `reasons over data` -> `analisa seus dados`.

## 4. Terminology
- Retain established technical terms where natural: `self-hosted`, `local-first`,
  `browser`, `desktop`, `web app`, `OCR`, `embeddings`, `API`, `Docker`,
  `markdown`, `streaming`, `hotkey`, `keyring`.
- Use the locale glossary for canonical terminology. Product names and brands
  remain unchanged; descriptive `assistant` becomes `assistente` only outside
  product names.
