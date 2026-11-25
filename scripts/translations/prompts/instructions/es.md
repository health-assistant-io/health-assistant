# Spanish Localization Instructions

## 1. Native Flow and Register
- Write idiomatic, contemporary neutral Spanish for an international audience.
  Convey the complete meaning and intent naturally; never copy English word
  order, punctuation, or idioms.
- Use the formal `usted` register consistently unless a repository-local file
  explicitly changes it. Omit subject pronouns where Spanish naturally does and
  avoid repeating `su/sus` when the referent is already clear.
- Avoid regional slang, `vosotros`, and country-specific vocabulary. Prefer
  Spanish that reads naturally in both Spain and Latin America.

## 2. Style and Grammar
- Prefer concise active verbs, natural Spanish sentence rhythm, and clear
  statements over noun-heavy or passive translations.
- UI labels and buttons must be compact. Explanatory copy may be restructured
  into shorter sentences where that improves clarity.
- Use Spanish capitalization: sentence case for headings and labels, except for
  proper names. Do not imitate English title case or dash-heavy constructions.

## 3. Avoid Literal Calques
- `under the hood` -> `Tecnología`, `Aspectos técnicos`, or `Cómo funciona`
  (never `Bajo el capó`).
- `on your machine / device` -> `en su equipo`, `en su ordenador`, or
  `localmente` (never `en su máquina`).
- `bring your own [provider / AI / model]` -> `elija su proveedor`, `use su
  propio modelo`, or `con la IA que prefiera` (never `traiga su ...`).
- `lives in your system tray` -> `está disponible en el área de notificación`
  (never `vive en ...`).
- `one [hotkey / click] away` -> `con un atajo de teclado` or `a un clic`
  (never a literal distance metaphor).
- `text dump` -> `texto sin estructurar`, `información dispersa`; `human-gated`
  -> `solo tras su aprobación`; `reasons over data` -> `analiza sus datos` or
  `fundamenta sus recomendaciones en sus datos`.

## 4. Terminology
- Retain established technical terms where natural: `self-hosted`, `local-first`,
  `browser`, `desktop`, `web app`, `OCR`, `embeddings`, `API`, `Docker`,
  `markdown`, `streaming`, `hotkey`, `keyring`.
- Use the locale glossary for canonical UI terminology. Product names and brands
  always remain unchanged; descriptive `assistant` becomes `asistente` only
  outside product names.
