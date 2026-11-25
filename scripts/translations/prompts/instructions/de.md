# German Localization Instructions

## 1. Native Flow and Meaning
- Write natural, idiomatic German as an articulate native speaker would. Convey
  the full meaning and intent, but never translate word-for-word or mechanically
  reproduce English syntax.
- Freely reorder clauses, split overly long sentences, and prefer direct verbs
  over noun-heavy or passive constructions. The result must read as originally
  written in German, not translated copy.
- Keep UI labels, buttons, and badges short and clear. Marketing and feature
  copy should be confident, modern, and concrete without exaggerated claims.

## 2. Register and Grammar
- Use one form of address (`du` or `Sie`) consistently. The repository's
  `instructions/de.local.md` determines which form applies.
- Follow standard German capitalization for nouns, correct compound-word
  spelling, and natural German punctuation. Do not mechanically retain English
  em-dash-heavy sentence structure where a full stop or colon reads better.

## 3. Avoid Literal Calques
- `under the hood` -> `Technische Grundlagen`, `Technik im Überblick`, or
  `So funktioniert es` (never `Unter der Haube`).
- `on your machine / device` -> `auf Ihrem Rechner`, `auf Ihrem Computer`, or
  `lokal auf Ihrem Gerät` (never `auf Ihrer Maschine`).
- `bring your own [provider / AI / model]` -> `Wählen Sie Ihren Anbieter`,
  `Nutzen Sie Ihr eigenes Modell`, or `mit dem KI-Anbieter Ihrer Wahl` (never
  literal `Bringen Sie ... mit`).
- `lives in your system tray` -> `läuft im Infobereich`, `ist im
  Benachrichtigungsbereich verfügbar`, or `liegt im System-Tray` (never `lebt
  im ...`).
- `one [hotkey / click] away` -> `per Tastenkürzel sofort erreichbar`, `nur
  einen Klick entfernt`, or `direkt per Tastenkürzel` (never `ein Hotkey
  entfernt`).
- `deep [profile / insight]` -> `detailliertes / umfassendes Profil` or
  `aussagekräftige Einblicke` (never `tiefes Profil`).
- `text dump` -> `unstrukturierter Text`, `ungeordnete Textsammlung`, or
  `Textwüste` when appropriate (never a literal `Text-Haufen`).
- `human-gated` -> `erst nach Ihrer Freigabe`, `mit Ihrer ausdrücklichen
  Zustimmung` (never a literal `menschlich abgesichert`).
- `reasons over [data]` -> `wertet Daten aus`, `begründet seine Vorschläge
  anhand Ihrer Daten` (never a literal translation of `reasoning over`).

## 4. Terminology
- Keep established technical terms in English where that is natural in German:
  `Browser`, `Desktop`, `Open Source`, `Release`, `Live`, `Dashboard`,
  `Feedback`, `Login`, `Newsletter`, `OCR`, `Embeddings`, `Markdown`,
  `Streaming`, `API`, `Docker`, `Hotkey`, `System-Tray`.
- Prefer established German compounds where they read more naturally:
  `self-hosted` -> `selbst gehostet`, `Web App` -> `Web-App`, `Desktop App`
  -> `Desktop-App`, `settings` -> `Einstellungen`, `documentation` ->
  `Dokumentation`.
- Product names and brands (`Neuronection`, `Health Assistant`, `Study
  Assistant`, `Career Assistant`, `Desktop Assistant`, `OpenAI`, `Whisper`,
  etc.) always remain unchanged. Translate descriptive `assistant` as
  `Assistent` only when it is not part of a product name.
