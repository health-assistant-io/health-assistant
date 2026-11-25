# French Localization Instructions

## 1. Native Flow and Register
- Write natural, contemporary French that reads as original French copy. Preserve
  meaning and nuance, but never mirror English word order, idioms, or marketing
  formulations mechanically.
- Use the formal `vous` register consistently unless a repository-local file
  explicitly selects `tu`. Avoid unnecessary repetition of `vous` and `votre`.
- Keep the tone confident, direct, and approachable. Avoid bureaucratic phrasing
  and overly literal English-style noun compounds.

## 2. Style and Grammar
- Prefer direct verbs and short, readable sentences. Freely split or reorder a
  long English sentence when French rhythm requires it.
- Keep buttons, labels, and badges compact; use sentence case rather than English
  title case. Follow standard French typography and punctuation.
- Use inclusive wording only where it is natural and does not make the UI copy
  longer or less clear.

## 3. Avoid Literal Calques
- `under the hood` -> `Aspects techniques`, `Technologie`, or `Comment ça
  marche` (never `Sous le capot`).
- `on your machine / device` -> `sur votre ordinateur`, `sur votre appareil`,
  or `en local` (never `sur votre machine`).
- `bring your own [provider / AI / model]` -> `choisissez votre fournisseur`,
  `utilisez votre propre modèle`, or `avec l'IA de votre choix` (never a literal
  `apportez votre ...`).
- `lives in your system tray` -> `est disponible dans la zone de notification`
  (never `vit dans ...`).
- `one [hotkey / click] away` -> `accessible par raccourci clavier` or `en un
  clic`; `text dump` -> `texte non structuré`; `human-gated` -> `après votre
  validation`; `reasons over data` -> `analyse vos données`.

## 4. Terminology
- Retain established technical terms where natural: `self-hosted`, `local-first`,
  `browser`, `desktop`, `web app`, `OCR`, `embeddings`, `API`, `Docker`,
  `markdown`, `streaming`, `hotkey`, `keyring`.
- Use the locale glossary for canonical UI terminology. Product names and brands
  remain unchanged; translate descriptive `assistant` only outside product names.
