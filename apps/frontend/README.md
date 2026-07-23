# MatoLoop Frontend

Das MatoLoop-Frontend ist eine Vue-3-SPA mit Quasar, Pinia und Vue Router. Im Devstack läuft es als eigener HMR-Prozess und leitet `/api` an das Backend weiter.

## Entwicklung

Das Frontend wird normalerweise vom Repository-Root als Teil des Devstacks gestartet:

```bash
./scripts/dev.sh up --build -d
./scripts/dev.sh logs frontend
```

Die WebUI ist anschließend unter `http://localhost:10020` erreichbar. Installierte Pakete liegen im Named Volume `frontend_node_modules`; ein zusätzlicher Cache-Pfad ist als `frontend_yarn_cache` eingebunden.

## Struktur

- `src/pages`: routbare Seiten
- `src/layouts`: gemeinsame Navigation und Seitenrahmen
- `src/components`: wiederverwendbare UI-Bausteine
- `src/stores`: Pinia-Stores für Server- und Laufzeitzustand
- `src/boot`: Quasar-Boot-Dateien, Axios und globale Directives
- `src/composables`: wiederverwendbare Composition-API-Logik
- `src/directives`: globale Vue-Directives
- `src/utils`: zustandslose Hilfsfunktionen wie Fehlerübersetzung
- `src/css`: globale Styles und Theme-Anpassungen
- `tests`: Vitest-Tests

## API-Fehler und Toasts

Das Backend liefert sprachneutrale Fehlercodes. `src/utils/api-errors.js` übersetzt diese Codes in deutsche Meldungen und formatiert optionale `field`- und `params`-Werte.

Der Axios-Response-Interceptor in `src/boot/axios.js` zeigt API-Fehler zentral als negative Quasar-Toasts an. Komponenten dürfen denselben Fehler nicht zusätzlich benachrichtigen.

Fehler außerhalb von Axios, beispielsweise `run.failed` über SSE oder ein fehlgeschlagener OAuth-Status, verwenden `notifyError` aus `src/utils/notifications.js`.

Regeln:

- Niemals `response.data`, `detail`, Arrays oder Fehlerobjekte direkt rendern.
- Niemals Backendtexte vergleichen, um Programmabläufe zu steuern.
- Neue Backendcodes in `api-errors.js` mit einer sicheren Meldung ergänzen.
- Dynamische Werte ausschließlich aus `params` einsetzen.
- Für unbekannte Codes einen generischen Fallback anhand des HTTP-Status verwenden.
- Optimistische UI-Einträge nach fehlgeschlagenen Requests zurücknehmen.

Die Code-zu-Text-Abbildung kann später durch eine i18n-Bibliothek ersetzt werden. Backendcodes und Parameter bleiben dabei unverändert.

## Bestätigungen und Dialoge

Einfache destruktive Aktionen verwenden `useConfirm` aus `src/composables/confirm.js` oder die Directive `v-confirm`. Dazu gehören insbesondere das Löschen von Sessions, Dateien oder Ordnern.

Quasar-Dialoge werden verwendet, wenn zusätzlich Eingaben oder mehrere fachliche Entscheidungen notwendig sind. Human-in-the-loop-Freigaben verwenden den persistierten Approval-Zustand und dürfen beim Schließen nicht automatisch abgelehnt werden.

## UI-Regeln

- Quasar-Komponenten und Utilities haben Vorrang vor neuem Custom CSS.
- Seiten, Dialoge und Drawer müssen auf Desktop und Mobile bedienbar sein.
- Light Mode und Dark Mode müssen beide unterstützt werden.
- Nutzerseitige Texte werden auf Deutsch geschrieben.
- Stores verwalten Serverzustand; Komponenten koordinieren Darstellung und Benutzeraktionen.
- Angezeigt wird der effektive Runtime-Zustand, nicht nur ein gespeicherter Wunschwert.
- Webfetch-Policy und Modell-Override werden getrennt angezeigt; die globale Netzwerk-Allowlist bleibt eine Hostgrenze.
- Attachment-URLs müssen authentifizierte interne MatoLoop-Pfade sein. Fremde URLs und `v-html` sind für Tool-Ergebnisse unzulässig.
- Assistant-Antworten werden ausschließlich über die zentrale Markdown-Komponente gerendert. Sie deaktiviert Raw HTML, sanitisiert das erzeugte HTML und ist die einzige zulässige `v-html`-Ausnahme.
- Die Markdown-Komponente rendert KaTeX-unterstütztes LaTeX mit `$...$`, `$$...$$`, `\\(...\\)` und `\\[...\\]`. Vertrauensabhängige KaTeX-Befehle sind deaktiviert; notwendige Styles bleiben ausschließlich innerhalb erzeugter KaTeX-Teilbäume erhalten.
- Externe Markdown-Bilder bleiben bis zu einer expliziten, sitzungsbezogenen Origin-Freigabe inaktiv. Danach muss jede konkrete URL einen Browser-Load und die Bilddecodierung bestehen; Fehler und Timeouts bleiben als Platzhalter mit Retry sichtbar. Interne Bilder werden nur über validierte MatoLoop-Attachment-Pfade automatisch geladen.

## Checks und Tests

Vom Repository-Root:

```bash
./scripts/dev.sh check frontend
./scripts/dev.sh test frontend
```

Direkt aus `apps/frontend`, wenn die Yarn-Abhängigkeiten bereits installiert sind:

```bash
yarn lint
yarn test
yarn build
```

Neue Fehlercodes benötigen Tests für die übersetzte Meldung und den sicheren Fallback. UI-Änderungen werden mindestens auf Desktop und Mobile sowie in Light Mode und Dark Mode geprüft.
