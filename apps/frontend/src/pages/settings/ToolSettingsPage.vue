<template>
  <section>
    <header class="settings-section-header">
      <div>
        <div class="row items-center q-gutter-sm">
          <h2 class="text-h6 text-weight-bold q-my-none">Tools</h2>
          <q-badge v-if="activeDirty" color="warning" text-color="black" label="Ungespeichert" />
        </div>
        <p class="text-body2 app-muted q-mb-none q-mt-xs">
          Richtlinien und technische Verfügbarkeit eingebauter Werkzeuge verwalten.
        </p>
      </div>
      <div class="settings-scope-actions row items-center col-auto">
        <q-btn
          class="col-auto"
          v-if="tab === 'policies' && tools.scope === 'personal'"
          outline
          no-caps
          no-wrap
          color="primary"
          icon="restart_alt"
          label="Auf Instanzstandard zurücksetzen"
          :disable="personalBlocked || activeLoading || activeSaving"
          @click="resetPersonalSettings"
        />
        <SettingsScopeToggle
          v-if="tab === 'policies'"
          :model-value="tools.scope"
          :is-admin="auth.isAdmin"
          :disable="activeLoading || activeSaving || gateLoading"
          @update:model-value="changeScope"
        />
        <q-btn
          class="col-auto"
          color="primary"
          unelevated
          no-caps
          no-wrap
          icon="save"
          label="Speichern"
          :loading="activeSaving"
          :disable="activeLoading || !activeLoaded || !activeDirty || personalBlocked"
          @click="save"
        />
      </div>
    </header>

    <q-tabs
      :model-value="tab"
      dense
      no-caps
      align="left"
      active-color="primary"
      indicator-color="primary"
      class="q-mb-lg"
      @update:model-value="changeTab"
    >
      <q-tab v-if="auth.isAdmin" name="configuration" icon="build" label="Verfügbare Tools" />
      <q-tab name="policies" icon="policy" label="Policies" />
    </q-tabs>

    <q-tab-panels :model-value="tab" animated class="bg-transparent">
      <q-tab-panel name="policies" class="q-pa-none">
        <q-card v-if="auth.isAdmin && tools.scope === 'instance'" flat class="app-surface-tonal q-mb-md">
          <q-card-section class="row items-center justify-between q-gutter-md">
            <div>
              <div class="text-subtitle2 text-weight-bold">Persönliche Tool-Einstellungen</div>
              <div class="text-caption app-muted">Erlaubt Benutzern eigene Tool-Richtlinien festzulegen.</div>
            </div>
            <q-toggle
              :model-value="tools.personalSettingsEnabled"
              color="primary"
              label="Erlaubt"
              :disable="gateLoading"
              @update:model-value="togglePersonalSettings"
            />
          </q-card-section>
        </q-card>

        <q-banner v-if="tools.scope === 'personal'" rounded class="app-surface-tonal q-mb-md">
          <template #avatar><q-icon name="person" color="primary" /></template>
          {{ personalBlocked
            ? 'Persönliche Tool-Einstellungen wurden für diese Instanz deaktiviert. Vorhandene Einstellungen bleiben gespeichert, können aber nicht bearbeitet werden.'
            : 'Persönliche Tool-Richtlinien ersetzen die Instanzvorgaben. Technische Verfügbarkeit und Grenzen werden von der Instanz übernommen.' }}
        </q-banner>

        <q-card flat class="app-surface-tonal q-mb-md">
          <q-card-section>
            <q-btn-toggle
              :model-value="tools.trust"
              spread
              no-caps
              unelevated
              toggle-color="primary"
              :options="TRUST_OPTIONS"
              :disable="tools.loading || tools.saving"
              @update:model-value="changeTrust"
            />
          </q-card-section>
        </q-card>

        <q-banner rounded class="app-surface-tonal q-mb-md">
          <template #avatar><q-icon name="info_outline" color="primary" /></template>
          Technisch deaktivierte Tools und Ressourcen können keine Aktion ausführen. Ihre Richtlinien bleiben gespeichert.
        </q-banner>

        <q-form
          ref="policyFormRef"
          :class="{ disabled: personalBlocked }"
          :inert="personalBlocked"
          @submit.prevent="savePolicies"
        >
          <q-banner v-if="tools.loadFailed" rounded class="bg-negative text-white q-mb-md">
            <template #avatar><q-icon name="error_outline" /></template>
            Die Tool-Richtlinien konnten nicht geladen werden.
            <template #action>
              <q-btn flat no-caps color="white" label="Erneut laden" @click="loadPolicies" />
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
              <q-card-section>
                <div class="row items-start q-col-gutter-md">
                  <div class="col-12 col-md row items-start no-wrap q-gutter-md">
                    <q-avatar color="primary" text-color="white" :icon="tool.icon" />
                    <div class="col">
                      <div class="row items-center q-gutter-sm">
                        <div class="text-subtitle2 text-weight-bold">{{ tool.label }}</div>
                        <q-chip
                          v-if="tool.available === false"
                          dense
                          outline
                          color="warning"
                          label="Technisch deaktiviert"
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
                      v-model="policyForm.policies[action.capability]"
                      outlined
                      dense
                      emit-value
                      map-options
                      :label="actionLabel(tool, action)"
                      :options="POLICY_OPTIONS"
                      :hint="actionHint(tool, action)"
                      :disable="actionUnavailable(tool, action)"
                      persistent-hint
                    />
                    <q-chip
                      v-for="action in fixedActions(tool)"
                      :key="action.capability"
                      dense
                      outline
                      color="primary"
                      :label="`Hostgesteuert · ${policyLabel(action.policy)}`"
                    />
                  </div>
                </div>

                <div v-if="tool.name === 'shell_run'" class="tool-settings-detail q-gutter-md">
                  <q-banner rounded class="app-surface-tonal">
                    <template #avatar><q-icon name="rule" color="primary" /></template>
                    Ein Präfix pro Zeile. Es wird tokenbasiert geprüft und gilt nur für die jeweilige
                    Klassifikation. Alle Teilbefehle müssen erlaubt sein.
                    Die Einstufung ist keine Garantie für rein lesendes Verhalten.
                  </q-banner>
                  <div>
                    <label for="shell-read-prefixes" class="text-caption">Präfixe für Befehle ohne erkannte Schreibaktionen</label>
                    <q-input
                      v-model="shellPrefixText.read"
                      for="shell-read-prefixes"
                      outlined
                      type="textarea"
                      autogrow
                      hint="Beispiel: cat"
                      :rules="[shellPrefixRule]"
                      lazy-rules
                    />
                  </div>
                  <div>
                    <label for="shell-write-prefixes" class="text-caption">Präfixe für als schreibend eingestufte Befehle</label>
                    <q-input
                      v-model="shellPrefixText.write"
                      for="shell-write-prefixes"
                      outlined
                      type="textarea"
                      autogrow
                      hint="Beispiel: git status"
                      :rules="[shellPrefixRule]"
                      lazy-rules
                    />
                  </div>
                </div>

                <div v-else-if="mediaToolsByName[tool.name]" class="tool-settings-detail q-gutter-md">
                  <q-input
                    v-model.number="policyForm[mediaToolsByName[tool.name].field].timeoutSeconds"
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
                        :name="policyForm.policies[mediaToolsByName[tool.name].capability] === 'auto'
                          ? 'warning_amber'
                          : mediaToolsByName[tool.name].warningIcon"
                        :color="policyForm.policies[mediaToolsByName[tool.name].capability] === 'auto'
                          ? 'warning'
                          : 'primary'"
                      />
                    </template>
                    {{ mediaToolsByName[tool.name].warning }}
                    <span v-if="policyForm.policies[mediaToolsByName[tool.name].capability] === 'auto'">
                      Im Modus „Automatisch“ kann das Modell dies ohne einzelne Rückfrage auslösen.
                    </span>
                  </q-banner>
                </div>
              </q-card-section>
              <q-separator v-if="toolIndex < group.tools.length - 1" />
            </template>
            <q-inner-loading :showing="tools.loading" />
          </q-card>
        </q-form>
      </q-tab-panel>

      <q-tab-panel v-if="auth.isAdmin" name="configuration" class="q-pa-none">
        <q-banner v-if="tools.configurationLoadFailed" rounded class="bg-negative text-white q-mb-md">
          <template #avatar><q-icon name="error_outline" /></template>
          Die verfügbaren Tools konnten nicht geladen werden.
          <template #action>
            <q-btn flat no-caps color="white" label="Erneut laden" @click="loadConfiguration" />
          </template>
        </q-banner>

        <q-banner rounded class="app-surface-tonal q-mb-md">
          <template #avatar><q-icon name="admin_panel_settings" color="primary" /></template>
          Diese technischen Instanzgrenzen gelten für persönliche und instanzweite Richtlinien sowie für private und öffentliche Modelle.
        </q-banner>

        <q-form ref="configurationFormRef" @submit.prevent="saveConfiguration">
          <q-card
            v-for="group in configurationForm.groups"
            :key="group.id"
            flat
            class="app-surface tool-settings-card q-mb-md"
          >
            <q-card-section class="row items-center q-gutter-md">
              <q-avatar color="primary" text-color="white" :icon="group.icon" />
              <div class="text-subtitle1 text-weight-bold">{{ group.label }}</div>
            </q-card-section>
            <q-separator />
            <template v-for="(tool, toolIndex) in group.tools" :key="tool.name">
              <q-card-section>
                <div class="row items-center q-col-gutter-md">
                  <div class="col-12 col-sm row items-center no-wrap q-gutter-md">
                    <q-avatar color="primary" text-color="white" :icon="tool.icon" />
                    <div>
                      <div class="text-subtitle2 text-weight-bold">{{ tool.label }}</div>
                      <div class="text-caption app-muted">{{ TOOL_DESCRIPTIONS[tool.name] || tool.name }}</div>
                    </div>
                  </div>
                  <div class="col-12 col-sm-auto">
                    <q-toggle v-model="tool.available" color="primary" label="Verfügbar" />
                  </div>
                </div>

                <div v-if="tool.name === 'webfetch'" class="tool-settings-detail row q-col-gutter-md">
                  <div class="col-12">
                    <q-toggle
                      v-model="configurationForm.webFetch.publicAddressesOnly"
                      color="primary"
                      label="Nur öffentliche Adressen zulassen"
                    />
                  </div>
                  <div class="col-12 col-md-8">
                    <q-input
                      v-model.trim="configurationForm.webFetch.userAgent"
                      outlined
                      maxlength="512"
                      label="User-Agent"
                      hint="Browserkennung, die Webfetch bei HTTP-Anfragen sendet."
                      :rules="[userAgentRule]"
                      lazy-rules
                    />
                  </div>
                  <div class="col-12 col-md-4">
                    <q-input
                      v-model.number="configurationForm.webFetch.maxResponseMiB"
                      outlined
                      type="number"
                      min="1"
                      max="256"
                      suffix="MiB"
                      label="Maximale Antwortgröße"
                      hint="Gilt gemeinsam für alle unterstützten Inhalte."
                      :rules="[responseLimitRule]"
                      lazy-rules
                    />
                  </div>
                </div>
              </q-card-section>
              <q-separator v-if="toolIndex < group.tools.length - 1" />
            </template>
            <q-inner-loading :showing="tools.configurationLoading" />
          </q-card>
        </q-form>
      </q-tab-panel>
    </q-tab-panels>
  </section>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useQuasar } from 'quasar'
import { onBeforeRouteLeave } from 'vue-router'
import SettingsScopeToggle from 'components/SettingsScopeToggle.vue'
import { useConfirm } from 'src/composables/confirm'
import { useAuthStore } from 'stores/auth'
import { useToolSettingsStore } from 'stores/tool-settings'

defineOptions({ name: 'ToolSettingsPage' })

const $q = useQuasar()
const { confirm } = useConfirm()
const tools = useToolSettingsStore()
const auth = useAuthStore()
const policyFormRef = ref(null)
const configurationFormRef = ref(null)
const gateLoading = ref(false)
const tab = ref('policies')
const savedPolicyPayload = ref('')
const savedConfigurationPayload = ref('')
const clone = (value) => JSON.parse(JSON.stringify(value))
const POLICY_OPTIONS = [
  { label: 'Deaktiviert', value: 'disabled' },
  { label: 'Nachfragen', value: 'ask' },
  { label: 'Automatisch', value: 'auto' }
]
const TRUST_OPTIONS = [
  { label: 'Private Modelle', value: 'private', icon: 'lock' },
  { label: 'Öffentliche Modelle', value: 'public', icon: 'public' }
]
const mediaTools = [
  { name: 'generate_image', field: 'generateImage', capability: 'media.generate.image', warningIcon: 'cloud_upload', warning: 'Prompts werden an den konfigurierten Mediendienst übertragen; die Erzeugung kann länger dauern.' },
  { name: 'edit_image', field: 'editImage', capability: 'media.edit.image', warningIcon: 'privacy_tip', warning: 'Ausgewählte Bilder und der Bearbeitungsprompt werden an den konfigurierten Mediendienst übertragen.' },
  { name: 'generate_speech', field: 'generateSpeech', capability: 'media.generate.speech', warningIcon: 'cloud_upload', warning: 'Text wird an den konfigurierten Mediendienst übertragen; lange Texte erhöhen Laufzeit und Datenmenge.' },
  { name: 'generate_audio', field: 'generateAudio', capability: 'media.generate.audio', warningIcon: 'schedule', warning: 'Die Audioerzeugung kann viel Laufzeit und Rechenleistung beanspruchen und externe Datenübertragung auslösen.' },
  { name: 'transcribe_audio', field: 'transcribeAudio', capability: 'media.transcribe.audio', warningIcon: 'privacy_tip', warning: 'Audioaufnahmen können an den konfigurierten Transkriptionsdienst übertragen werden.' },
  { name: 'analyze_video', field: 'analyzeVideo', capability: 'media.analyze.video', warningIcon: 'privacy_tip', warning: 'Videobilder und Tonspuren können an die konfigurierten Analyse- und Transkriptionsmodelle übertragen werden.' }
]
const mediaToolsByName = Object.fromEntries(mediaTools.map((item) => [item.name, item]))
const TOOL_DESCRIPTIONS = {
  get_system_info: 'Liest grundlegende Informationen zum ausgewählten Computer.',
  change_directory: 'Wechselt den persistenten aktuellen Ordner der Session.',
  shell_run: 'Führt einen Shell-Befehl im aktuellen Ordner aus.',
  read: 'Liest Dateien aus einem erlaubten Bereich.',
  glob: 'Sucht Dateien anhand eines Pfadmusters.',
  grep: 'Durchsucht Dateiinhalte nach einem Muster.',
  write_file: 'Erstellt oder ersetzt Textdateien.',
  edit_file: 'Ersetzt einen eindeutigen Textabschnitt in einer Textdatei.',
  create_directory: 'Legt ein neues Verzeichnis an.',
  delete_path: 'Löscht Dateien oder Verzeichnisse.',
  copy_path: 'Kopiert Dateien oder Verzeichnisse zwischen erlaubten Bereichen.',
  move_path: 'Verschiebt Dateien oder Verzeichnisse.',
  web_search: 'Sucht über den hostseitig gewählten Provider im Web.',
  webfetch: 'Ruft geprüfte HTTP(S)-Inhalte ab.',
  fetch_transcript: 'Ruft veröffentlichte Untertitel unterstützter Video-URLs ab.',
  generate_image: 'Erzeugt Bilder aus einer textlichen Beschreibung.',
  edit_image: 'Bearbeitet Bildanhänge, ohne die Originale zu verändern.',
  generate_speech: 'Erzeugt gesprochene WAV-Audiodateien aus Text.',
  generate_audio: 'Erzeugt Audio oder Musik aus einem Prompt.',
  transcribe_audio: 'Transkribiert WAV-Anhänge aus der aktuellen Session.',
  analyze_video: 'Analysiert MP4- und WebM-Videos.',
  session_read: 'Liest Nachrichten aus Sessions.',
  session_tool_result_read: 'Liest ein persistiertes Tool-Ergebnis.',
  session_browse: 'Listet letzte Sessions auf.',
  session_search: 'Durchsucht Titel und Inhalte vorhandener Sessions.',
  memory_retrieve: 'Liest passende persistente Memories.',
  memory_write: 'Erstellt oder aktualisiert persistente Memories.',
  update_agent_identity: 'Ändert Name oder Persona-Anweisungen des Agents.',
  update_user_profile: 'Ändert gepflegte Angaben im persönlichen Profil.',
  list_automations: 'Listet vorhandene Automationen auf.',
  get_automation: 'Liest die Details einer Automation.',
  create_automation: 'Erstellt eine neue geplante oder einmalige Automation.',
  edit_automation: 'Ändert eine vorhandene Automation.',
  run_automation: 'Startet eine Automation sofort.',
  delete_automation: 'Löscht eine Automation samt Verlauf.'
}
const policyForm = reactive({
  policies: {},
  generateImage: { policy: 'ask', timeoutSeconds: 300 },
  editImage: { policy: 'ask', timeoutSeconds: 300 },
  generateSpeech: { policy: 'ask', timeoutSeconds: 300 },
  generateAudio: { policy: 'ask', timeoutSeconds: 300 },
  transcribeAudio: { policy: 'ask', timeoutSeconds: 300 },
  analyzeVideo: { policy: 'ask', timeoutSeconds: 1800 }
})
const shellPrefixText = reactive({ read: '', write: '' })
const configurationForm = reactive({
  groups: [],
  webFetch: { userAgent: '', maxResponseMiB: 64, publicAddressesOnly: true }
})
const personalBlocked = computed(() => tab.value === 'policies'
  && tools.scope === 'personal'
  && !tools.personalSettingsEnabled)
const policyDirty = computed(() => Boolean(savedPolicyPayload.value)
  && JSON.stringify(policyRequestPayload()) !== savedPolicyPayload.value)
const configurationDirty = computed(() => Boolean(savedConfigurationPayload.value)
  && JSON.stringify(configurationRequestPayload()) !== savedConfigurationPayload.value)
const activeDirty = computed(() => tab.value === 'policies' ? policyDirty.value : configurationDirty.value)
const activeLoading = computed(() => tab.value === 'policies' ? tools.loading : tools.configurationLoading)
const activeSaving = computed(() => tab.value === 'policies' ? tools.saving : tools.configurationSaving)
const activeLoaded = computed(() => tab.value === 'policies' ? tools.loaded : tools.configurationLoaded)

function parseShellPrefixes(value) {
  return [...new Map(
    value.split('\n')
      .map((line) => line.trim().split(/\s+/).filter(Boolean))
      .filter((prefix) => prefix.length)
      .map((prefix) => [JSON.stringify(prefix), prefix])
  ).values()]
}

function policyRequestPayload() {
  const payload = {
    policies: { ...policyForm.policies },
    shellPrefixes: {
      read: parseShellPrefixes(shellPrefixText.read),
      write: parseShellPrefixes(shellPrefixText.write)
    }
  }
  for (const { field, capability } of mediaTools) {
    payload[field] = { ...policyForm[field], policy: policyForm.policies[capability] }
  }
  payload.updateAgentIdentity = { policy: policyForm.policies['settings.agent_identity.update'] }
  return payload
}

function configurationRequestPayload() {
  const tools = configurationForm.groups.flatMap((group) => group.tools)
  return {
    toolAvailability: Object.fromEntries(tools.map((tool) => [tool.name, tool.available])),
    webFetch: { ...configurationForm.webFetch }
  }
}

function assignPolicyForm(settings) {
  policyForm.policies = { ...settings.policies }
  for (const { field, capability } of mediaTools) {
    policyForm[field] = { ...settings[field] }
    policyForm.policies[capability] ??= settings[field].policy
  }
  policyForm.policies['settings.agent_identity.update'] ??= settings.updateAgentIdentity.policy
  for (const classification of ['read', 'write']) {
    shellPrefixText[classification] = settings.shellPrefixes[classification]
      .map((prefix) => prefix.join(' '))
      .join('\n')
  }
  savedPolicyPayload.value = JSON.stringify(policyRequestPayload())
}

function assignConfigurationForm() {
  configurationForm.groups = clone(tools.configurationGroups)
  configurationForm.webFetch = { ...tools.configurationWebFetch }
  savedConfigurationPayload.value = JSON.stringify(configurationRequestPayload())
}

function configurableActions(tool) {
  if (tool.name === 'session_browse') return []
  return (tool.actions || []).filter((action) => action.configurable)
}

function fixedActions(tool) {
  return (tool.actions || []).filter((action) => !action.configurable)
}

function actionLabel(tool, action) {
  return configurableActions(tool).length > 1 ? `${action.label}: Richtlinie` : 'Ausführungsrichtlinie'
}

function actionUnavailable(tool, action) {
  return tool.available === false || action.available === false
}

function actionHint(tool, action) {
  if (tool.available === false) return 'Das Tool wurde unter „Verfügbare Tools“ deaktiviert. Die Richtlinie bleibt gespeichert.'
  if (action.available === false) return 'Diese feste Ressource wurde technisch deaktiviert. Die Richtlinie bleibt gespeichert.'
  return policyHint(policyForm.policies[action.capability])
}

function policyLabel(policy) {
  return { disabled: 'Deaktiviert', ask: 'Nachfragen', auto: 'Automatisch' }[policy] || policy
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

function shellPrefixRule(value) {
  const prefixes = parseShellPrefixes(value)
  return (prefixes.length <= 100 && prefixes.every((prefix) => (
    prefix.length <= 8
    && prefix.every((token) => token.length <= 256 && /^[\x21-\x7e]+$/.test(token))
  ))) || 'Maximal 100 Präfixe mit je 1 bis 8 sichtbaren Tokens eingeben.'
}

async function save() {
  if (tab.value === 'policies') await savePolicies()
  else await saveConfiguration()
}

async function savePolicies() {
  if (!tools.loaded || !policyDirty.value || !await policyFormRef.value?.validate()) return
  const saved = await tools.saveSettings(policyRequestPayload())
  if (!saved) return
  assignPolicyForm(saved)
  $q.notify({ type: 'positive', message: 'Tool-Richtlinien gespeichert' })
}

async function saveConfiguration() {
  if (!tools.configurationLoaded
    || !configurationDirty.value
    || !await configurationFormRef.value?.validate()) return
  const saved = await tools.saveConfiguration(configurationRequestPayload())
  if (!saved) return
  assignConfigurationForm()
  $q.notify({ type: 'positive', message: 'Verfügbare Tools gespeichert' })
}

async function loadPolicies() {
  try {
    const settings = await tools.fetchSettings()
    if (settings) assignPolicyForm(settings)
  } catch {
    // The central Axios interceptor displays the error message.
  }
}

async function loadConfiguration() {
  try {
    const configuration = await tools.fetchConfiguration()
    if (configuration) assignConfigurationForm()
  } catch {
    // The central Axios interceptor displays the error message.
  }
}

async function changeScope(scope) {
  if (scope === tools.scope) {
    if (!savedPolicyPayload.value) await loadPolicies()
    return
  }
  if (!await confirmDiscardedChanges()) return
  savedPolicyPayload.value = ''
  tools.selectScope(scope)
  await loadPolicies()
}

async function changeTrust(trust) {
  if (trust === tools.trust || !await confirmDiscardedChanges()) return
  savedPolicyPayload.value = ''
  tools.selectTrust(trust)
  await loadPolicies()
}

async function changeTab(next) {
  if (next === tab.value || !await confirmDiscardedChanges()) return
  tab.value = next
  if (next === 'policies') assignPolicyForm(tools)
  else if (tools.configurationLoaded) assignConfigurationForm()
  else await loadConfiguration()
}

async function togglePersonalSettings(enabled) {
  gateLoading.value = true
  try {
    await tools.setPersonalSettingsEnabled(enabled)
  } finally {
    gateLoading.value = false
  }
}

async function resetPersonalSettings() {
  const accepted = await confirm(
    'Deine persönlichen Tool-Einstellungen löschen und wieder die Instanzvorgaben verwenden?',
    'Tool-Richtlinien zurücksetzen?',
    { okLabel: 'Zurücksetzen' }
  )
  if (!accepted) return
  const settings = await tools.resetPersonalSettings()
  if (!settings) return
  assignPolicyForm(settings)
  $q.notify({ type: 'positive', message: 'Tool-Richtlinien zurückgesetzt' })
}

async function confirmDiscardedChanges() {
  if (activeSaving.value) return false
  if (!activeDirty.value) return true
  return confirm(
    'Sollen die ungespeicherten Änderungen verworfen werden?',
    'Änderungen verwerfen?',
    { okLabel: 'Verwerfen' }
  )
}

onMounted(() => changeScope('personal'))
onBeforeRouteLeave(confirmDiscardedChanges)
</script>
