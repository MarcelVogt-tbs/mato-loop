<template>
  <section>
    <header class="settings-section-header">
      <div>
        <h2 class="text-h6 text-weight-bold q-my-none">Tools</h2>
        <p class="text-body2 app-muted q-mb-none q-mt-xs">
          Werkzeuge des Agents gruppiert und global steuern.
        </p>
      </div>
      <q-btn
        color="primary"
        unelevated
        no-caps
        icon="save"
        label="Speichern"
        :loading="tools.saving"
        :disable="tools.loading || !tools.loaded"
        @click="save"
      />
    </header>

    <q-form ref="formRef" @submit.prevent="save">
      <q-banner v-if="tools.loadFailed" rounded class="bg-negative text-white q-mb-md">
        <template #avatar><q-icon name="error_outline" /></template>
        Die Tool-Einstellungen konnten nicht geladen werden.
        <template #action>
          <q-btn flat no-caps color="white" label="Erneut laden" @click="load" />
        </template>
      </q-banner>

      <q-card
        v-for="group in tools.groups"
        :key="group.id"
        flat
        class="app-surface tool-settings-card q-mb-md"
      >
        <q-card-section class="row items-center q-gutter-md">
          <q-avatar color="primary" text-color="white" :icon="group.icon" />
          <div>
            <div class="text-subtitle1 text-weight-bold">{{ group.label }}</div>
            <div class="text-body2 app-muted">Tools und Richtlinien dieser Gruppe.</div>
          </div>
        </q-card-section>
        <q-separator />

        <template v-for="(tool, toolIndex) in group.tools" :key="tool.name">
          <q-card-section class="tool-settings-item">
            <div class="row items-start q-col-gutter-md">
              <div class="col-12 col-md row items-start no-wrap q-gutter-md">
                <q-avatar color="primary" text-color="white" :icon="tool.icon" />
                <div class="col">
                  <div class="row items-center q-gutter-sm">
                    <div class="text-subtitle2 text-weight-bold">{{ tool.label }}</div>
                    <q-chip
                      v-if="tool.source?.type !== 'builtin'"
                      dense
                      outline
                      color="primary"
                      label="Integration"
                    />
                  </div>
                  <div class="text-caption app-muted">
                    {{ TOOL_DESCRIPTIONS[tool.name] || tool.name }}
                  </div>
                </div>
              </div>

              <div class="col-12 col-md-5 q-gutter-sm">
                <q-select
                  v-for="action in configurableActions(tool)"
                  :key="action.capability"
                  v-model="form.policies[action.capability]"
                  outlined
                  dense
                  emit-value
                  map-options
                  :label="actionLabel(tool, action)"
                  :options="actionPolicyOptions(action)"
                  :hint="policyHint(form.policies[action.capability])"
                />
                <q-chip
                  v-for="action in fixedActions(tool)"
                  :key="action.capability"
                  dense
                  outline
                  color="primary"
                  :label="fixedActionLabel(tool, action)"
                />
              </div>
            </div>

            <div v-if="tool.name === 'web_search'" class="tool-settings-detail q-gutter-md">
              <q-select
                v-model="form.webSearch.primaryProvider"
                outlined
                emit-value
                map-options
                label="Primärprovider"
                :behavior="$q.screen.lt.sm ? 'dialog' : 'menu'"
                :options="primaryProviderOptions"
                @update:model-value="ensureDistinctProviders"
              />
              <q-select
                v-model="form.webSearch.fallbackProvider"
                outlined
                emit-value
                map-options
                clearable
                label="Fallback-Provider"
                :behavior="$q.screen.lt.sm ? 'dialog' : 'menu'"
                :options="fallbackProviderOptions"
                hint="Wird nur bei temporären Fehlern des Primärproviders verwendet."
                persistent-hint
              />
              <q-banner rounded class="app-surface-tonal">
                <template #avatar><q-icon name="privacy_tip" color="primary" /></template>
                Suchbegriffe werden an den gewählten Dienst übertragen. Zusätzliche Provider
                werden unter Integrationen konfiguriert.
              </q-banner>
            </div>

            <div v-else-if="tool.name === 'webfetch'" class="tool-settings-detail q-gutter-md">
              <q-toggle v-model="form.enabled" color="primary" label="Global aktiv" />
              <q-banner v-if="!form.enabled" rounded class="app-surface-tonal">
                <template #avatar><q-icon name="block" color="warning" /></template>
                Webfetch ist für alle Modelle deaktiviert. Modell-Overrides bleiben gespeichert.
              </q-banner>
              <q-select
                v-model="form.defaultPolicy"
                outlined
                emit-value
                map-options
                label="Globale Standardpolicy"
                :options="WEBFETCH_DEFAULT_POLICY_OPTIONS"
                hint="Modelle mit „Globalen Standard übernehmen“ verwenden diese Policy."
              />
              <q-input
                v-model.trim="form.userAgent"
                outlined
                maxlength="512"
                label="User-Agent"
                hint="Browserkennung, die Webfetch bei HTTP-Anfragen sendet."
                :rules="[userAgentRule]"
                lazy-rules
              />
              <q-input
                v-model.number="form.maxResponseMiB"
                outlined
                type="number"
                min="1"
                max="256"
                suffix="MiB"
                label="Maximale Antwortgröße"
                hint="Gilt gemeinsam für Webseiten, PDFs, Text und Bilder."
                :rules="[responseLimitRule]"
                lazy-rules
              />
              <q-banner v-if="form.defaultPolicy === 'auto_allowed'" rounded class="app-surface-tonal">
                <template #avatar><q-icon name="warning_amber" color="warning" /></template>
                Erlaubte private Origins können ohne einzelne Rückfrage abgerufen werden.
              </q-banner>
              <div>
                <div class="text-subtitle2">Erlaubte private Origins</div>
                <div class="text-caption app-muted q-mb-sm">
                  Nur vollständige Origins ohne Pfad oder Zugangsdaten. Gefährliche Adressen bleiben blockiert.
                </div>
                <div
                  v-for="(_, index) in form.allowedPrivateOrigins"
                  :key="index"
                  class="tool-origin-row q-mb-sm"
                >
                  <q-input
                    v-model="form.allowedPrivateOrigins[index]"
                    outlined
                    dense
                    :label="`Private Origin ${index + 1}`"
                    :rules="[originRule]"
                    lazy-rules
                  >
                    <template #prepend><q-icon name="lan" /></template>
                  </q-input>
                  <q-btn
                    flat
                    round
                    dense
                    color="negative"
                    icon="delete_outline"
                    :aria-label="`Private Origin ${index + 1} entfernen`"
                    @click="form.allowedPrivateOrigins.splice(index, 1)"
                  />
                </div>
                <q-btn
                  outline
                  no-caps
                  color="primary"
                  icon="add"
                  label="Origin hinzufügen"
                  @click="form.allowedPrivateOrigins.push('')"
                />
              </div>
            </div>

            <div v-else-if="mediaToolsByName[tool.name]" class="tool-settings-detail q-gutter-md">
              <q-input
                v-model.number="form[mediaToolsByName[tool.name].field].timeoutSeconds"
                outlined
                type="number"
                min="10"
                max="3600"
                step="10"
                label="Zeitlimit"
                suffix="Sekunden"
                :rules="[timeoutRule]"
                lazy-rules
                hint="Nach dieser Dauer wird die Provider-Anfrage abgebrochen."
                persistent-hint
              />
              <q-banner rounded class="app-surface-tonal">
                <template #avatar>
                  <q-icon
                    :name="form.policies[mediaToolsByName[tool.name].capability] === 'auto'
                      ? 'warning_amber'
                      : mediaToolsByName[tool.name].warningIcon"
                    :color="form.policies[mediaToolsByName[tool.name].capability] === 'auto'
                      ? 'warning'
                      : 'primary'"
                  />
                </template>
                {{ mediaToolsByName[tool.name].warning }}
                <span v-if="form.policies[mediaToolsByName[tool.name].capability] === 'auto'">
                  Im Modus „Automatisch“ kann das Modell dies ohne einzelne Rückfrage auslösen.
                </span>
              </q-banner>
            </div>

            <q-banner
              v-else-if="tool.name === 'update_agent_identity'"
              rounded
              class="tool-settings-detail app-surface-tonal"
            >
              <template #avatar>
                <q-icon
                  :name="form.policies['settings.agent_identity.update'] === 'auto'
                    ? 'warning_amber'
                    : 'manage_accounts'"
                  :color="form.policies['settings.agent_identity.update'] === 'auto'
                    ? 'warning'
                    : 'primary'"
                />
              </template>
              Das Tool darf nur ausdrückliche Wünsche des Nutzers übernehmen. Im Modus
              „Automatisch“ wird die persistente Persona ohne einzelne Rückfrage geändert.
            </q-banner>
          </q-card-section>
          <q-separator v-if="toolIndex < group.tools.length - 1" />
        </template>
        <q-inner-loading :showing="tools.loading" />
      </q-card>
    </q-form>
  </section>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useQuasar } from 'quasar'
import { useToolSettingsStore } from 'stores/tool-settings'
import {
  DEFAULT_WEBFETCH_USER_AGENT,
  WEBFETCH_DEFAULT_POLICY_OPTIONS,
  normalizePrivateOrigin
} from 'src/utils/webfetch'

defineOptions({ name: 'ToolSettingsPage' })

const $q = useQuasar()
const tools = useToolSettingsStore()
const formRef = ref(null)
const MEDIA_POLICY_OPTIONS = [
  { label: 'Deaktiviert', value: 'disabled' },
  { label: 'Nachfragen', value: 'ask' },
  { label: 'Automatisch', value: 'auto' }
]
const mediaTools = [
  {
    name: 'generate_image',
    field: 'generateImage',
    capability: 'media.generate.image',
    warningIcon: 'cloud_upload',
    warning: 'Prompts werden an den konfigurierten Mediendienst übertragen; die Erzeugung kann länger dauern.'
  },
  {
    name: 'edit_image',
    field: 'editImage',
    capability: 'media.edit.image',
    warningIcon: 'privacy_tip',
    warning: 'Ausgewählte Bilder und der Bearbeitungsprompt werden an den konfigurierten Mediendienst übertragen.'
  },
  {
    name: 'generate_speech',
    field: 'generateSpeech',
    capability: 'media.generate.speech',
    warningIcon: 'cloud_upload',
    warning: 'Text wird an den konfigurierten Mediendienst übertragen; lange Texte erhöhen Laufzeit und Datenmenge.'
  },
  {
    name: 'generate_audio',
    field: 'generateAudio',
    capability: 'media.generate.audio',
    warningIcon: 'schedule',
    warning: 'Die Audioerzeugung kann viel Laufzeit und Rechenleistung beanspruchen und externe Datenübertragung auslösen.'
  },
  {
    name: 'transcribe_audio',
    field: 'transcribeAudio',
    capability: 'media.transcribe.audio',
    warningIcon: 'privacy_tip',
    warning: 'Audioaufnahmen können an den konfigurierten Transkriptionsdienst übertragen werden.'
  },
  {
    name: 'analyze_video',
    field: 'analyzeVideo',
    capability: 'media.analyze.video',
    warningIcon: 'privacy_tip',
    warning: 'Videobilder und Tonspuren können an die konfigurierten Analyse- und Transkriptionsmodelle übertragen werden.'
  }
]
const mediaToolsByName = Object.fromEntries(mediaTools.map((item) => [item.name, item]))
const TOOL_DESCRIPTIONS = {
  list_directory: 'Listet Dateien und Unterordner eines Verzeichnisses auf.',
  read_file: 'Liest Textdateien aus Session oder Workspace.',
  write_file: 'Erstellt oder ersetzt Textdateien.',
  create_directory: 'Legt ein neues Verzeichnis an.',
  delete_path: 'Löscht Dateien oder Verzeichnisse.',
  copy_path: 'Kopiert Dateien oder Verzeichnisse zwischen erlaubten Bereichen.',
  move_path: 'Verschiebt Dateien oder Verzeichnisse zwischen erlaubten Bereichen.',
  web_search: 'Sucht über den hostseitig gewählten Provider im Web.',
  webfetch: 'Ruft geprüfte HTTP(S)-Inhalte ab.',
  generate_image: 'Erzeugt Bilder aus einer textlichen Beschreibung.',
  edit_image: 'Bearbeitet Bildanhänge, ohne die Originale zu verändern.',
  generate_speech: 'Erzeugt gesprochene WAV-Audiodateien aus Text.',
  generate_audio: 'Erzeugt Audio oder Musik aus einem Prompt.',
  transcribe_audio: 'Transkribiert WAV-Anhänge aus der aktuellen Session.',
  analyze_video: 'Analysiert MP4- und WebM-Videos aus Session oder Workspace.',
  session_read: 'Liest Nachrichten aus der aktuellen oder einer anderen Session.',
  session_tool_result_read: 'Liest ein persistiertes Tool-Ergebnis der aktuellen Session.',
  session_browse: 'Listet letzte Sessions auf. Nutzt dieselbe Richtlinie wie die Session-Suche.',
  session_search: 'Durchsucht Titel und Inhalte vorhandener Sessions.',
  memory_retrieve: 'Liest passende persistente Memories.',
  memory_write: 'Erstellt oder aktualisiert persistente Memories.',
  update_agent_identity: 'Ändert Name oder Persona-Anweisungen des Agents.',
  calendar_list_accounts: 'Listet die konfigurierten CalDAV-Konten auf.',
  calendar_list_calendars: 'Listet Kalender eines CalDAV-Kontos auf.',
  calendar_query_events: 'Liest Termine aus einem ausgewählten Kalender.',
  calendar_create_event: 'Erstellt einen Termin in einem ausgewählten Kalender.',
  calendar_update_event: 'Ändert einen zuvor gelesenen Termin.',
  calendar_delete_event: 'Löscht einen zuvor gelesenen Termin.',
  knowledge_list_accounts: 'Listet die konfigurierten BookStack-Wissensdatenbanken auf.',
  knowledge_list_books: 'Listet die Bücher einer Wissensdatenbank samt Regalzuordnung auf.',
  knowledge_search: 'Durchsucht Bücher, Kapitel und Seiten einer Wissensdatenbank.',
  knowledge_read_book: 'Listet Kapitel, Seiten und Regalzuordnung eines ausgewählten Buchs auf.',
  knowledge_read_page: 'Liest den Markdown-Inhalt einer zuvor gefundenen Seite.',
  knowledge_create_page: 'Erstellt eine Markdown-Seite in einem ausgewählten Buch.',
  knowledge_update_page:
    'Ersetzt Titel oder Inhalt einer zuvor gelesenen Seite und gibt den gespeicherten Inhalt zurück.',
  knowledge_delete_page: 'Löscht eine zuvor gelesene Seite.',
  knowledge_create_book: 'Erstellt ein neues Buch und ordnet es optional Regalen zu.',
  knowledge_delete_book: 'Löscht ein Buch samt aller Kapitel und Seiten.',
  knowledge_create_chapter: 'Erstellt ein Kapitel in einem ausgewählten Buch.',
  knowledge_delete_chapter: 'Löscht ein zuvor gelesenes Kapitel samt Seiten.',
  knowledge_list_shelves: 'Listet die Regale einer Wissensdatenbank auf.',
  knowledge_read_shelf: 'Zeigt ein Regal samt zugeordneter Bücher an.',
  knowledge_create_shelf: 'Erstellt ein neues Regal in einer Wissensdatenbank.',
  knowledge_update_shelf: 'Ändert Name, Beschreibung oder Bücher eines Regals; Bücher können einzeln hinzugefügt oder entfernt werden.',
  knowledge_delete_shelf: 'Löscht ein Regal, ohne die enthaltenen Bücher zu löschen.',
  knowledge_list_attachments: 'Listet die Anhänge einer zuvor gelesenen Seite auf.',
  knowledge_create_attachment:
    'Hängt eine Datei oder einen Link an eine Seite: Download aus dem Internet, Datei aus Session- oder Workspace-Speicher oder reiner Verweis.',
  knowledge_delete_attachment: 'Löscht einen zuvor aufgelisteten Anhang einer Seite.'
}
const form = reactive({
  policies: {},
  webSearch: {
    primaryProvider: 'no_key',
    fallbackProvider: null,
    providerOptions: []
  },
  enabled: true,
  userAgent: DEFAULT_WEBFETCH_USER_AGENT,
  maxResponseMiB: 64,
  defaultPolicy: 'auto_public',
  allowedPrivateOrigins: [],
  generateImage: { policy: 'disabled', timeoutSeconds: 300 },
  editImage: { policy: 'ask', timeoutSeconds: 300 },
  generateSpeech: { policy: 'disabled', timeoutSeconds: 300 },
  generateAudio: { policy: 'disabled', timeoutSeconds: 300 },
  transcribeAudio: { policy: 'disabled', timeoutSeconds: 300 },
  analyzeVideo: { policy: 'ask', timeoutSeconds: 1800 }
})

function assignForm(settings) {
  form.policies = { ...settings.policies }
  form.webSearch = {
    ...settings.webSearch,
    providerOptions: [...settings.webSearch.providerOptions]
  }
  Object.assign(form, settings.webFetch, {
    allowedPrivateOrigins: [...settings.webFetch.allowedPrivateOrigins]
  })
  for (const { field, capability } of mediaTools) {
    form[field] = { ...settings[field] }
    form.policies[capability] ??= settings[field].policy
  }
  form.policies['settings.agent_identity.update'] ??= settings.updateAgentIdentity.policy
}

const primaryProviderOptions = computed(() => form.webSearch.providerOptions.map((provider) => ({
  label: provider.integrationId ? `${provider.name} · SearXNG` : provider.name,
  value: provider.value
})))
const fallbackProviderOptions = computed(() => [
  { label: 'Kein Fallback', value: null },
  ...primaryProviderOptions.value.filter((provider) => provider.value !== form.webSearch.primaryProvider)
])

function ensureDistinctProviders() {
  if (form.webSearch.fallbackProvider === form.webSearch.primaryProvider) {
    form.webSearch.fallbackProvider = null
  }
}

function configurableActions(tool) {
  if (tool.name === 'session_browse') return []
  return (tool.actions || []).filter((action) => action.configurable)
}

function fixedActions(tool) {
  return (tool.actions || []).filter((action) => !action.configurable)
}

function actionPolicyOptions(action) {
  const allowed = action.allowedPolicies || MEDIA_POLICY_OPTIONS.map(({ value }) => value)
  return MEDIA_POLICY_OPTIONS.filter(({ value }) => allowed.includes(value))
}

function actionLabel(tool, action) {
  return configurableActions(tool).length > 1
    ? `${action.label}: Richtlinie`
    : 'Ausführungsrichtlinie'
}

function fixedActionLabel(tool, action) {
  if (tool.name === 'webfetch') return 'Spezialrichtlinie'
  return `Hostgesteuert · ${policyLabel(action.policy)}`
}

function policyLabel(policy) {
  return {
    disabled: 'Deaktiviert',
    ask: 'Nachfragen',
    auto: 'Automatisch'
  }[policy] || policy
}

function policyHint(policy) {
  return {
    disabled: 'Das Tool wird dem Modell nicht angeboten.',
    ask: 'Vor jeder Ausführung ist eine Freigabe erforderlich.',
    auto: 'Das Modell darf das Tool ohne einzelne Freigabe ausführen.'
  }[policy]
}

function timeoutRule(value) {
  return (Number.isInteger(value) && value >= 10 && value <= 3600)
    || 'Das Zeitlimit muss zwischen 10 und 3600 Sekunden liegen.'
}

function responseLimitRule(value) {
  return (Number.isInteger(value) && value >= 1 && value <= 256)
    || 'Die Antwortgröße muss zwischen 1 und 256 MiB liegen.'
}

function userAgentRule(value) {
  const valid = typeof value === 'string'
    && value.length >= 1
    && value.length <= 512
    && [...value].every((character) => {
      const code = character.charCodeAt(0)
      return code >= 32 && code <= 126
    })
  return valid || 'Der User-Agent muss aus 1 bis 512 sichtbaren ASCII-Zeichen bestehen.'
}

function originRule(value) {
  const result = normalizePrivateOrigin(value)
  return result.valid || result.message
}

async function save() {
  if (!tools.loaded || !await formRef.value?.validate()) return
  const allowedPrivateOrigins = [...new Set(
    form.allowedPrivateOrigins.map((origin) => normalizePrivateOrigin(origin).value)
  )]
  const saved = await tools.saveSettings({
    policies: { ...form.policies },
    webSearch: {
      primaryProvider: form.webSearch.primaryProvider,
      fallbackProvider: form.webSearch.fallbackProvider
    },
    webFetch: {
      enabled: form.enabled,
      userAgent: form.userAgent,
      maxResponseMiB: form.maxResponseMiB,
      defaultPolicy: form.defaultPolicy,
      allowedPrivateOrigins
    },
    generateImage: {
      ...form.generateImage,
      policy: form.policies['media.generate.image']
    },
    editImage: {
      ...form.editImage,
      policy: form.policies['media.edit.image']
    },
    generateSpeech: {
      ...form.generateSpeech,
      policy: form.policies['media.generate.speech']
    },
    generateAudio: {
      ...form.generateAudio,
      policy: form.policies['media.generate.audio']
    },
    transcribeAudio: {
      ...form.transcribeAudio,
      policy: form.policies['media.transcribe.audio']
    },
    analyzeVideo: {
      ...form.analyzeVideo,
      policy: form.policies['media.analyze.video']
    },
    updateAgentIdentity: {
      policy: form.policies['settings.agent_identity.update']
    }
  })
  assignForm(saved)
  $q.notify({ type: 'positive', message: 'Tool-Einstellungen gespeichert' })
}

async function load() {
  try {
    assignForm(await tools.fetchSettings())
  } catch {
    // The central Axios interceptor displays the error message.
  }
}

onMounted(load)
</script>
