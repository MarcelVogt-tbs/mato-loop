<template>
  <section>
    <header class="settings-section-header">
      <div>
        <h2 class="text-h6 text-weight-bold q-my-none">Tools</h2>
        <p class="text-body2 app-muted q-mb-none q-mt-xs">
          {{ admin ? 'Globale Tool-Defaults und Vorgaben der eingebauten Profile verwalten.' : 'Tool-Rechte der eigenen Agentenprofile anpassen.' }}
        </p>
      </div>
      <q-btn
        v-if="canEdit"
        color="primary" unelevated no-caps icon="save" label="Speichern"
        :loading="saving"
        :disable="loading || !dirty"
        @click="save"
      />
    </header>

    <q-tabs v-if="admin" :model-value="section" dense no-caps align="left" active-color="primary" indicator-color="primary" class="q-mb-lg" @update:model-value="selectSection">
      <q-tab name="general" icon="settings" label="Einstellungen" />
      <q-tab name="management" icon="build" label="Verwaltung" />
    </q-tabs>

    <AppSelect dense
      v-if="showManagement"
      :model-value="selectedId" outlined emit-value map-options label="Agentenprofil"
      class="q-mb-md" :options="profileOptions" :disable="loading || saving"
      @update:model-value="selectProfile"
    />
    <q-banner v-if="!canEdit && !loading" rounded class="app-surface-tonal">
      <template #avatar><q-icon name="admin_panel_settings" color="primary" /></template>
      {{ !allowed ? 'Persönliche Tool-Rechte wurden für diese Instanz deaktiviert.' : 'Erstelle zuerst ein persönliches Agentenprofil.' }}
    </q-banner>

    <q-banner v-else-if="loadFailed" rounded class="bg-negative text-white q-mb-md">
      <template #avatar><q-icon name="error_outline" /></template>
      Die Tool-Einstellungen konnten nicht geladen werden.
      <template #action><q-btn flat no-caps color="white" label="Erneut laden" @click="load" /></template>
    </q-banner>

    <q-form v-else-if="canEdit" ref="formRef" @submit.prevent="save">
      <PersonalSettingsGate v-if="admin && section === 'general'" v-model="form.personalSettingsEnabled" title="Tool-Rechte für persönliche Agentenprofile anpassen erlauben" description="Benutzer können die Tool-Rechte ihrer eigenen Profile frei anpassen." :disabled="saving || loading" />
      <template v-if="showManagement">
      <q-banner v-if="!selectedId" rounded class="app-surface-tonal q-mb-md">
        Berechtigungen sind Defaults. Ein technisch nicht verfügbares Tool kann durch Overrides nicht aktiviert werden.
      </q-banner>
      <ProfileToolOverrides v-if="selectedId" :key="selectedId" v-model="form.permissions" :settings="form" :groups-by-trust="permissionGroups" :model-trust-enabled="auth.modelTrustEnabled" :disabled="loading || saving || loadFailed">
        <template #actions>
          <q-btn v-if="admin" flat round dense icon="delete_outline" color="negative" aria-label="Alle Overrides löschen" :disable="loading || saving || loadFailed || !hasOverrides" @click="resetPermissions">
            <q-tooltip>Alle Overrides löschen</q-tooltip>
          </q-btn>
        </template>
      </ProfileToolOverrides>
      <PermissionFields v-else v-model="form.permissions" :groups-by-trust="permissionGroups" :model-trust-enabled="auth.modelTrustEnabled">
        <template #before-action="{ action }">
          <q-card-section v-if="(admin && !selectedId && (action.toolName === 'webfetch' || mediaByName[action.toolName])) || action.capability === 'shell.read'">
            <div v-if="action.toolName === 'webfetch'" class="row q-col-gutter-md">
              <div class="col-12">
                <div class="row items-center q-col-gutter-sm">
                  <div class="col-12 col-sm-6">
                    <q-toggle dense v-model="form.webFetch.publicAddressesOnly" color="primary" label="Nur öffentliche Adressen zulassen" />
                  </div>
                  <div class="col-12 col-sm-6 row justify-end">
                    <q-btn flat no-caps dense icon="edit" label="JavaScript-Konfiguration" :disable="loading || saving" @click="editJavascript" />
                  </div>
                </div>
              </div>
              <q-input dense v-model.trim="form.webFetch.userAgent" class="col-12 col-md-8" outlined maxlength="512" label="User-Agent" :rules="[userAgentRule]" />
              <q-input dense v-model.number="form.webFetch.maxResponseMiB" class="col-12 col-md-4" outlined type="number" min="1" max="256" suffix="MiB" label="Maximale Antwortgröße" :rules="[responseLimitRule]" />
            </div>

            <q-input dense
              v-if="mediaByName[action.toolName]"
              v-model.number="form.timeouts[mediaByName[action.toolName]].timeoutSeconds"
              outlined type="number" min="10" max="3600" step="10"
              label="Zeitlimit" suffix="Sekunden" :rules="[timeoutRule]"
            />
            <div v-if="action.capability === 'shell.read'" class="row justify-end">
              <q-btn flat no-caps dense icon="edit" label="Shell-Präfixe konfigurieren" :disable="loading || saving" @click="editShellPrefixes" />
            </div>
          </q-card-section>
        </template>
      </PermissionFields>
      <q-card v-for="group in technicalGroups" :key="group.id" flat class="app-surface q-mb-md">
        <q-card-section class="row items-center q-gutter-md">
          <q-avatar color="primary" text-color="white" :icon="group.icon" />
          <div class="text-subtitle1 text-weight-bold">{{ group.label }}</div>
        </q-card-section>
        <q-separator />
        <template v-for="(tool, index) in group.tools" :key="tool.name">
          <q-card-section>
            <div class="row items-center q-col-gutter-md">
              <div class="col-12 col-sm row items-center no-wrap q-gutter-md">
                <q-avatar color="primary" text-color="white" :icon="tool.icon" />
                <div>
                  <div class="settings-list-name text-subtitle2 text-weight-bold">{{ tool.label }}</div>
                  <div class="text-caption app-muted">{{ descriptions[tool.name] || tool.name }}</div>
                </div>
              </div>
              <div class="col-12 col-sm-auto row justify-end">
                <q-toggle dense v-model="tool.available" color="primary" label="Verfügbar" :aria-label="`${tool.label}: verfügbar`" />
              </div>
            </div>
          </q-card-section>
          <q-separator v-if="index < group.tools.length - 1" />
        </template>
      </q-card>
      </template>
      <q-inner-loading :showing="loading" />
    </q-form>
    <q-dialog v-model="shellDialogOpen" :maximized="$q.screen.lt.sm" aria-labelledby="shell-prefixes-title">
      <q-card class="app-surface column no-wrap full-width">
        <q-card-section class="row items-center">
          <div id="shell-prefixes-title" class="text-h6 col">Shell-Präfixe</div>
          <q-btn v-close-popup flat round dense icon="close" aria-label="Dialog schließen" />
        </q-card-section>
        <q-card-section class="col scroll">
          <ShellPrefixFields v-model="shellDraft" />
          <div class="text-caption app-muted q-mt-md">Mit „Übernehmen“ wird das Formular aktualisiert. Anschließend die Einstellungen auf der Seite speichern.</div>
        </q-card-section>
        <q-card-actions align="right">
          <q-btn v-close-popup flat no-caps label="Abbrechen" />
          <q-btn unelevated no-caps color="primary" label="Übernehmen" @click="applyShellPrefixes" />
        </q-card-actions>
      </q-card>
    </q-dialog>
    <q-dialog v-model="javascriptDialogOpen" :maximized="$q.screen.lt.sm" aria-labelledby="javascript-title">
      <q-card class="app-surface column no-wrap full-width">
        <q-card-section class="row items-center">
          <div id="javascript-title" class="text-h6 col">JavaScript für ausgewählte Websites</div>
          <q-btn v-close-popup flat round dense icon="close" aria-label="Dialog schließen" />
        </q-card-section>
        <q-card-section class="col scroll">
          <q-toggle v-model="javascriptDraft.enabled" color="primary" label="JavaScript aktivieren" />
          <q-input v-if="javascriptDraft.enabled" v-model="javascriptDraft.hosts" class="q-mt-md" outlined type="textarea" autogrow label="Websites mit automatischem JavaScript" hint="Ein DNS-Hostname pro Zeile, ohne URL, IP-Adresse, Port oder Platzhalter. Leer: standardmäßig ohne JavaScript. Explizite Toolargumente haben Vorrang." />
          <div v-else class="text-body2 app-muted q-mt-md">JavaScript ist für Webabrufe vollständig gesperrt, auch bei ausdrücklicher Anforderung durch das Modell. Die gespeicherte Hostliste bleibt erhalten.</div>
          <div class="text-caption app-muted q-mt-md">Mit „Übernehmen“ wird das Formular aktualisiert. Anschließend die Einstellungen auf der Seite speichern.</div>
        </q-card-section>
        <q-card-actions align="right">
          <q-btn v-close-popup flat no-caps label="Abbrechen" />
          <q-btn unelevated no-caps color="primary" label="Übernehmen" @click="applyJavascript" />
        </q-card-actions>
      </q-card>
    </q-dialog>
  </section>
</template>

<script setup>
import AppSelect from 'src/shared/ui/AppSelect.vue'
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useQuasar } from 'quasar'
import { onBeforeRouteLeave, useRoute } from 'vue-router'
import { useConfirm } from 'src/composables/confirm'
import { useAuthStore } from 'stores/auth'
import { useToolSettingsStore } from 'stores/tool-settings'
import { useAgentProfileStore } from 'stores/agent-profiles'
import PermissionFields from './PermissionFields.vue'
import ProfileToolOverrides from './ProfileToolOverrides.vue'
import ShellPrefixFields from './ShellPrefixFields.vue'
import PersonalSettingsGate from './PersonalSettingsGate.vue'

defineOptions({ name: 'ToolSettingsPage' })
const { admin } = defineProps({ admin: { type: Boolean, default: false } })

const $q = useQuasar()
const { confirm } = useConfirm()
const auth = useAuthStore()
const agents = useAgentProfileStore()
const route = useRoute()
const tools = useToolSettingsStore()
const selectedId = ref(route.query.profileId || null)
const section = ref(route.query.profileId ? 'management' : 'general')
const showManagement = computed(() => !admin || section.value === 'management')
const permissionGroups = ref({ private: [], public: [] })
const permissionsLoading = ref(false)
let loadToken = 0
const allowed = computed(() => admin || auth.isAdmin || (agents.personalAgentsEnabled && agents.personalToolPermissionsEnabled))
const canEdit = computed(() => allowed.value && (admin || Boolean(selectedId.value)))
const profileOptions = computed(() => [
  ...(admin ? [{ label: 'Globaler Standard', value: null }] : []),
  ...agents.manageableAgents.filter((agent) => admin ? agent.scope === 'instance' && agent.protected : agent.scope === 'personal').map((agent) => ({ label: agent.name, value: agent.id }))
])
const formRef = ref(null)
const saving = ref(false)
const loadFailed = ref(false)
const saved = ref('')
const javascriptDialogOpen = ref(false)
const javascriptDraft = ref({ enabled: true, hosts: '' })
const shellDialogOpen = ref(false)
const shellDraft = ref({ shellCustom: true, shellRead: '', shellWrite: '' })
const mediaByName = {
  generate_image: 'generateImage', edit_image: 'editImage', generate_speech: 'generateSpeech',
  generate_audio: 'generateAudio', transcribe_audio: 'transcribeAudio', analyze_video: 'analyzeVideo',
  analyze_audio: 'analyzeAudio'
}
const descriptions = {
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
  transcribe_audio: 'Wandelt Audiodateien in Text um.',
  analyze_video: 'Analysiert MP4- und WebM-Videos.',
  analyze_audio: 'Analysiert Audiodateien und sucht Passagen. Ohne audiofähiges Chatmodell ist nur Transkription verfügbar.',
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
const emptyPermissions = () => ({ privatePermissions: { policies: {}, integrationPolicies: {}, resourcePolicies: {} }, publicPermissions: { policies: {}, integrationPolicies: {}, resourcePolicies: {} } })
const form = reactive({ groups: [], webFetch: {}, javascriptHostsText: '', timeouts: {}, personalSettingsEnabled: false, permissions: emptyPermissions(), shellCustom: true, shellRead: '', shellWrite: '' })
const hasOverrides = computed(() => form.shellCustom || ['privatePermissions', 'publicPermissions'].some((branch) => Object.keys(form.permissions[branch].policies).length))
const technicalGroups = computed(() => admin && !selectedId.value ? form.groups : [])
const parsePrefixes = (value) => value.split('\n').map((line) => line.trim().split(/\s+/).filter(Boolean)).filter((tokens) => tokens.length)
const payload = () => ({
  ...form.permissions,
  shellPrefixes: form.shellCustom ? { read: parsePrefixes(form.shellRead), write: parsePrefixes(form.shellWrite) } : null,
  ...(!selectedId.value ? {
    personalSettingsEnabled: form.personalSettingsEnabled,
    toolAvailability: Object.fromEntries(form.groups.flatMap((group) => group.tools.map((tool) => [tool.name, tool.available]))),
    webFetch: { ...form.webFetch, javascriptHosts: form.javascriptHostsText.split('\n').map((host) => host.trim()).filter(Boolean) },
    ...Object.fromEntries(Object.values(mediaByName).map((field) => [field, { ...form.timeouts[field] }]))
  } : {})
})
const dirty = computed(() => Boolean(saved.value) && JSON.stringify(payload()) !== saved.value)
const loading = computed(() => tools.loading || tools.configurationLoading || permissionsLoading.value || agents.loading)
const timeoutRule = (value) => Number.isInteger(value) && value >= 10 && value <= 3600 || '10 bis 3600 Sekunden eingeben.'
const responseLimitRule = (value) => Number.isInteger(value) && value >= 1 && value <= 256 || '1 bis 256 MiB eingeben.'
const userAgentRule = (value) => typeof value === 'string' && value.length >= 1 && value.length <= 512
  && [...value].every((character) => character.charCodeAt(0) >= 32 && character.charCodeAt(0) <= 126)
  || '1 bis 512 sichtbare ASCII-Zeichen eingeben.'

function editShellPrefixes() {
  shellDraft.value = { shellCustom: form.shellCustom, shellRead: form.shellRead, shellWrite: form.shellWrite }
  shellDialogOpen.value = true
}

function applyShellPrefixes() {
  Object.assign(form, shellDraft.value)
  shellDialogOpen.value = false
}

function editJavascript() {
  javascriptDraft.value = { enabled: form.webFetch.javascriptEnabled, hosts: form.javascriptHostsText }
  javascriptDialogOpen.value = true
}

function applyJavascript() {
  form.webFetch.javascriptEnabled = javascriptDraft.value.enabled
  form.javascriptHostsText = javascriptDraft.value.hosts
  javascriptDialogOpen.value = false
}

async function load() {
  if (route.query.profileId) section.value = 'management'
  loadFailed.value = false
  selectedId.value = route.query.profileId || null
  try {
    await agents.fetchAgents(admin ? 'instance' : 'personal')
    if (!profileOptions.value.some((item) => item.value === selectedId.value)) selectedId.value = profileOptions.value[0]?.value || null
    await loadPermissions()
  } catch {
    loadFailed.value = true
  }
}

async function loadPermissions() {
  if (!canEdit.value) return
  const token = ++loadToken
  permissionsLoading.value = true
  loadFailed.value = false
  try {
    const data = selectedId.value ? await tools.fetchProfilePermissions(selectedId.value, admin ? 'instance' : 'personal') : await tools.fetchConfiguration()
    if (token !== loadToken) return
    form.permissions = { privatePermissions: data.privatePermissions || emptyPermissions().privatePermissions, publicPermissions: data.publicPermissions || emptyPermissions().publicPermissions }
    permissionGroups.value = data.groupsByTrust || { private: data.privateGroups, public: data.publicGroups }
    const prefixes = data.shellPrefixes || data.inheritedShellPrefixes || { read: [], write: [] }
    form.shellCustom = data.shellPrefixes !== null
    form.shellRead = prefixes.read.map((item) => item.join(' ')).join('\n')
    form.shellWrite = prefixes.write.map((item) => item.join(' ')).join('\n')
    if (!selectedId.value) {
      form.personalSettingsEnabled = data.personalSettingsEnabled
      form.groups = tools.configurationGroups.map((group) => ({ ...group, tools: group.tools.map((tool) => ({ ...tool })) }))
      form.webFetch = { ...tools.configurationWebFetch }
      form.javascriptHostsText = form.webFetch.javascriptHosts.join('\n')
      form.timeouts = Object.fromEntries(Object.values(mediaByName).map((field) => [field, { ...tools[field] }]))
    }
    saved.value = JSON.stringify(payload())
  } catch { if (token === loadToken) loadFailed.value = true }
  finally { if (token === loadToken) permissionsLoading.value = false }
}

async function selectProfile(id) {
  if (!await confirmDiscardedChanges()) return
  selectedId.value = id
  await loadPermissions()
}

async function selectSection(value) {
  if (value === 'general' && selectedId.value) {
    if (!await confirmDiscardedChanges()) return
    selectedId.value = null
    await loadPermissions()
  }
  section.value = value
}

async function resetPermissions() {
  if (!await confirm('Alle Berechtigungs- und Einstellungs-Overrides dieses Profils löschen, einschließlich öffentlicher Werte? Danach gelten wieder die geerbten Vorgaben.', 'Overrides löschen?', { okLabel: 'Löschen', okColor: 'negative' })) return
  saving.value = true
  try {
    await tools.saveProfilePermissions(selectedId.value, 'instance', { ...emptyPermissions(), shellPrefixes: null })
    await loadPermissions()
    $q.notify({ type: 'positive', message: 'Overrides gelöscht' })
  } catch { /* Central API toast. */ }
  finally { saving.value = false }
}

async function save() {
  if (!await formRef.value?.validate()) return
  saving.value = true
  try {
    if (selectedId.value) await tools.saveProfilePermissions(selectedId.value, admin ? 'instance' : 'personal', payload())
    else await tools.saveConfiguration(payload())
    await loadPermissions()
    saved.value = JSON.stringify(payload())
    $q.notify({ type: 'positive', message: 'Tool-Einstellungen gespeichert' })
  } catch {
    // The central Axios interceptor displays the error.
  } finally {
    saving.value = false
  }
}

async function confirmDiscardedChanges() {
  if (saving.value) return false
  if (!dirty.value) return true
  return confirm(
    'Sollen die ungespeicherten Änderungen verworfen werden?',
    'Änderungen verwerfen?',
    { okLabel: 'Verwerfen' }
  )
}

onMounted(load)
watch(() => [admin, route.query.profileId], load)
onBeforeRouteLeave(confirmDiscardedChanges)
</script>
