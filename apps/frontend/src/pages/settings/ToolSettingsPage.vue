<template>
  <section>
    <header class="settings-section-header">
      <div>
        <h2 class="text-h6 text-weight-bold q-my-none">Tools</h2>
        <p class="text-body2 app-muted q-mb-none q-mt-xs">
          Netzwerk- und Medienwerkzeuge des Agents global steuern.
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
      <q-card flat class="app-surface tool-settings-card">
        <q-card-section class="row items-start no-wrap q-gutter-md">
          <q-avatar color="primary" text-color="white" icon="travel_explore" />
          <div class="col">
            <div class="text-subtitle1 text-weight-bold">Websuche</div>
            <div class="text-body2 app-muted">
              Wählt den Suchanbieter des hostkontrollierten <code>web_search</code>-Tools.
            </div>
          </div>
        </q-card-section>

        <q-separator />

        <q-card-section class="q-gutter-md">
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
            Suchbegriffe werden an den gewählten Dienst übertragen. Die integrierte No-Key-Suche
            verwendet je nach Verfügbarkeit öffentliche Suchdienste; zusätzliche Provider werden
            unter Integrationen konfiguriert.
          </q-banner>
        </q-card-section>
        <q-inner-loading :showing="tools.loading" />
      </q-card>

      <q-card flat class="app-surface tool-settings-card q-mt-md">
        <q-card-section class="row items-start no-wrap q-gutter-md">
          <q-avatar color="primary" text-color="white" icon="language" />
          <div class="col">
            <div class="text-subtitle1 text-weight-bold">Webfetch</div>
            <div class="text-body2 app-muted">
              Ruft HTTP(S)-Inhalte ab. Weiterleitungen und Zieladressen werden serverseitig geprüft.
            </div>
          </div>
          <q-toggle v-model="form.enabled" color="primary" label="Global aktiv" />
        </q-card-section>

        <q-separator />

        <q-card-section class="q-gutter-md">
          <q-banner v-if="!form.enabled" rounded class="app-surface-tonal">
            <template #avatar><q-icon name="block" color="warning" /></template>
            Webfetch ist für alle Modelle deaktiviert. Modell-Overrides bleiben gespeichert, werden aber nicht wirksam.
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

          <q-banner v-if="form.defaultPolicy === 'auto_allowed'" rounded class="app-surface-tonal">
            <template #avatar><q-icon name="warning_amber" color="warning" /></template>
            Erlaubte private Origins können ohne einzelne Rückfrage abgerufen werden. Trage nur vertrauenswürdige interne Dienste ein.
          </q-banner>

          <div>
            <div class="text-subtitle2">Erlaubte private Origins</div>
            <div class="text-caption app-muted q-mb-sm">
              Nur vollständige Origins wie <code>http://127.0.0.1:8080</code>, ohne Pfad oder Zugangsdaten.
              Metadaten-, Link-Local- und andere gefährliche Adressen bleiben blockiert.
            </div>

            <div v-for="(_, index) in form.allowedPrivateOrigins" :key="index" class="tool-origin-row q-mb-sm">
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
        </q-card-section>
        <q-inner-loading :showing="tools.loading" />
      </q-card>

      <q-card flat class="app-surface tool-settings-card q-mt-md">
        <q-card-section class="row items-start no-wrap q-gutter-md">
          <q-avatar color="primary" text-color="white" icon="face" />
          <div class="col">
            <div class="text-subtitle1 text-weight-bold">Agent-Persona ändern</div>
            <div class="text-body2 app-muted">
              Erlaubt dem Agenten, einen ausdrücklichen Änderungswunsch aus dem Chat in der
              gemeinsamen Persona zu speichern.
            </div>
          </div>
        </q-card-section>
        <q-separator />
        <q-card-section class="q-gutter-md">
          <q-select
            v-model="form.updateAgentIdentity.policy"
            outlined
            emit-value
            map-options
            label="Ausführungsrichtlinie"
            :options="MEDIA_POLICY_OPTIONS"
            :hint="policyHint(form.updateAgentIdentity.policy)"
            persistent-hint
          />
          <q-banner rounded class="app-surface-tonal">
            <template #avatar>
              <q-icon
                :name="form.updateAgentIdentity.policy === 'auto' ? 'warning_amber' : 'manage_accounts'"
                :color="form.updateAgentIdentity.policy === 'auto' ? 'warning' : 'primary'"
              />
            </template>
            Das Tool darf nur ausdrückliche Wünsche des Nutzers übernehmen. Im Modus
            „Automatisch“ wird die persistente Persona dennoch ohne einzelne Rückfrage geändert.
          </q-banner>
        </q-card-section>
        <q-inner-loading :showing="tools.loading" />
      </q-card>

      <div class="media-tool-settings-grid q-mt-md">
        <q-card
          v-for="item in mediaTools"
          :key="item.field"
          flat
          class="app-surface media-tool-settings-card"
        >
          <q-card-section class="row items-start no-wrap q-gutter-md">
            <q-avatar color="primary" text-color="white" :icon="item.icon" />
            <div class="col">
              <div class="text-subtitle1 text-weight-bold">{{ item.label }}</div>
              <div class="text-body2 app-muted">{{ item.description }}</div>
            </div>
          </q-card-section>
          <q-separator />
          <q-card-section class="q-gutter-md">
            <q-select
              v-model="form[item.field].policy"
              outlined
              emit-value
              map-options
              label="Ausführungsrichtlinie"
              :options="MEDIA_POLICY_OPTIONS"
              :hint="policyHint(form[item.field].policy)"
              persistent-hint
            />
            <q-input
              v-model.number="form[item.field].timeoutSeconds"
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
                  :name="form[item.field].policy === 'auto' ? 'warning_amber' : item.warningIcon"
                  :color="form[item.field].policy === 'auto' ? 'warning' : 'primary'"
                />
              </template>
              {{ item.warning }}
              <span v-if="form[item.field].policy === 'auto'">
                Im Modus „Automatisch“ kann das Modell dies ohne einzelne Rückfrage auslösen.
              </span>
            </q-banner>
          </q-card-section>
          <q-inner-loading :showing="tools.loading" />
        </q-card>
      </div>
    </q-form>
  </section>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useQuasar } from 'quasar'
import { useToolSettingsStore } from 'stores/tool-settings'
import { WEBFETCH_DEFAULT_POLICY_OPTIONS, normalizePrivateOrigin } from 'src/utils/webfetch'

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
    field: 'generateImage',
    label: 'Bilder erzeugen',
    icon: 'image',
    warningIcon: 'cloud_upload',
    description: 'Erzeugt Bilder aus einer textlichen Beschreibung.',
    warning: 'Prompts werden an den konfigurierten Mediendienst übertragen; die Erzeugung kann länger dauern.'
  },
  {
    field: 'generateSpeech',
    label: 'Sprache erzeugen',
    icon: 'record_voice_over',
    warningIcon: 'cloud_upload',
    description: 'Erzeugt gesprochene WAV-Audiodateien aus Text.',
    warning: 'Text wird an den konfigurierten Mediendienst übertragen; lange Texte erhöhen Laufzeit und Datenmenge.'
  },
  {
    field: 'generateAudio',
    label: 'Audio erzeugen',
    icon: 'graphic_eq',
    warningIcon: 'schedule',
    description: 'Erzeugt Audio oder Musik aus einem Prompt.',
    warning: 'Die Audioerzeugung kann viel Laufzeit und Rechenleistung beanspruchen und externe Datenübertragung auslösen.'
  },
  {
    field: 'transcribeAudio',
    label: 'Audio transkribieren',
    icon: 'mic',
    warningIcon: 'privacy_tip',
    description: 'Transkribiert WAV-Anhänge und ermöglicht das Diktat im Chat.',
    warning: 'Audioaufnahmen können an den konfigurierten Transkriptionsdienst übertragen werden.'
  }
]
const form = reactive({
  webSearch: {
    primaryProvider: 'no_key',
    fallbackProvider: null,
    providerOptions: []
  },
  enabled: true,
  defaultPolicy: 'ask',
  allowedPrivateOrigins: [],
  generateImage: { policy: 'disabled', timeoutSeconds: 300 },
  generateSpeech: { policy: 'disabled', timeoutSeconds: 300 },
  generateAudio: { policy: 'disabled', timeoutSeconds: 300 },
  transcribeAudio: { policy: 'disabled', timeoutSeconds: 300 },
  updateAgentIdentity: { policy: 'ask' }
})

function assignForm(settings) {
  form.webSearch = {
    ...settings.webSearch,
    providerOptions: [...settings.webSearch.providerOptions]
  }
  Object.assign(form, settings.webFetch, {
    allowedPrivateOrigins: [...settings.webFetch.allowedPrivateOrigins]
  })
  for (const { field } of mediaTools) form[field] = { ...settings[field] }
  form.updateAgentIdentity = { ...settings.updateAgentIdentity }
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
    webSearch: {
      primaryProvider: form.webSearch.primaryProvider,
      fallbackProvider: form.webSearch.fallbackProvider
    },
    webFetch: {
      enabled: form.enabled,
      defaultPolicy: form.defaultPolicy,
      allowedPrivateOrigins
    },
    generateImage: { ...form.generateImage },
    generateSpeech: { ...form.generateSpeech },
    generateAudio: { ...form.generateAudio },
    transcribeAudio: { ...form.transcribeAudio },
    updateAgentIdentity: { ...form.updateAgentIdentity }
  })
  assignForm(saved)
  $q.notify({ type: 'positive', message: 'Tool-Einstellungen gespeichert' })
}

async function load() {
  try {
    assignForm(await tools.fetchSettings())
  } catch {
    // Der zentrale Axios-Interceptor zeigt die Fehlermeldung an.
  }
}

onMounted(load)
</script>
