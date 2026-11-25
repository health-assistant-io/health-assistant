# Polish Localization Instructions

## 1. Native Flow and Register
- Write clear, idiomatic contemporary Polish. Preserve the complete message and
  intent, but freely restructure sentences instead of translating English wording
  or punctuation literally.
- Prefer gender-neutral or impersonal constructions when direct address would
  force a gendered form. If the repository defines a register locally, follow it
  consistently throughout the dictionary.
- Keep the tone direct, helpful, and modern. Avoid English-style noun stacks,
  inflated marketing wording, and unnatural repetition of possessives.

## 2. Style and Grammar
- Favor concise active verbs and natural Polish word order. Split a long English
  sentence when it improves readability.
- Buttons, labels, and badges must be short and clear. Use Polish sentence case;
  do not imitate English title case or dash-heavy phrasing.
- Respect Polish inflection and agreement. Do not force glossary terms into an
  ungrammatical case; use the natural inflected form required by the sentence.

## 3. Avoid Literal Calques
- `under the hood` -> `Technologia`, `Szczegóły techniczne`, or `Jak to działa`
  (never `Pod maską`).
- `on your machine / device` -> `na swoim komputerze`, `na swoim urządzeniu`, or
  `lokalnie` (never `na swojej maszynie`).
- `bring your own [provider / AI / model]` -> `wybierz własnego dostawcę`, `użyj
  własnego modelu`, or `z AI według własnego wyboru` (never a literal `przynieś`).
- `lives in your system tray` -> `działa w obszarze powiadomień` (never `żyje w`).
- `one [hotkey / click] away` -> `skrótem klawiszowym` or `jednym kliknięciem`;
  `text dump` -> `nieuporządkowany tekst`; `human-gated` -> `dopiero po Twoim
  zatwierdzeniu`; `reasons over data` -> `analizuje Twoje dane`.

## 4. Terminology
- Retain established technical terms where natural: `self-hosted`, `local-first`,
  `browser`, `desktop`, `web app`, `OCR`, `embeddings`, `API`, `Docker`,
  `markdown`, `streaming`, `hotkey`, `keyring`.
- Use the locale glossary for canonical terminology. Product names and brands
  always remain unchanged; descriptive `assistant` becomes `asystent` only
  outside product names.
