# Italian Localization Instructions

## 1. Native Flow and Register
- Write fluent, contemporary Italian that reads as original Italian copy. Convey
  the intended meaning naturally; never mirror English syntax, punctuation, or
  idioms mechanically.
- Use the formal `Lei` register consistently unless a repository-local file
  explicitly selects `tu`. Avoid unnecessary possessives where Italian naturally
  omits them.
- Keep the tone direct, confident, and approachable. Do not use inflated
  marketing language or literal English-style noun compounds.

## 2. Style and Grammar
- Prefer active verbs and concise sentences. Freely reorder or split English
  clauses to make the Italian flow naturally.
- UI labels, buttons, and badges must remain short and clear. Use Italian
  sentence case, not English title case, and natural Italian punctuation.
- Use established Italian forms for compound words and avoid unnecessary English
  words where a concise Italian equivalent is standard.

## 3. Avoid Literal Calques
- `under the hood` -> `Aspetti tecnici`, `Tecnologia`, or `Come funziona`
  (never `Sotto il cofano`).
- `on your machine / device` -> `sul suo computer`, `sul suo dispositivo`, or
  `in locale` (never `sulla sua macchina`).
- `bring your own [provider / AI / model]` -> `scelga il suo provider`, `usi il
  suo modello`, or `con l'IA che preferisce` (never `porti il suo ...`).
- `lives in your system tray` -> `è disponibile nell'area di notifica` (never
  `vive in ...`).
- `one [hotkey / click] away` -> `con una scorciatoia da tastiera` or `a portata
  di clic`; `text dump` -> `testo non strutturato`; `human-gated` -> `solo dopo
  la sua approvazione`; `reasons over data` -> `analizza i suoi dati`.

## 4. Terminology
- Retain established technical terms where natural: `self-hosted`, `local-first`,
  `browser`, `desktop`, `web app`, `OCR`, `embeddings`, `API`, `Docker`,
  `markdown`, `streaming`, `hotkey`, `keyring`.
- Use the locale glossary for canonical terminology. Product names and brands
  remain unchanged; descriptive `assistant` becomes `assistente` only outside
  product names.
