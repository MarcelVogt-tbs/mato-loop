<template>
  <section>
    <header class="settings-section-header">
      <div>
        <h2 class="text-h6 text-weight-bold q-my-none">Tools</h2>
        <p class="text-body2 app-muted q-mb-none q-mt-xs">
          Technische Verfügbarkeit, Netzwerkgrenzen und Zeitlimits verwalten. Rechte werden in Agentenprofilen festgelegt.
        </p>
      </div>
      <q-btn
        v-if="auth.isAdmin"
        color="primary" unelevated no-caps icon="save" label="Speichern"
        :loading="saving"
        :disable="loading || !dirty"
        @click="save"
      />
    </header>

    <q-banner v-if="!auth.isAdmin" rounded class="app-surface-tonal">
      <template #avatar><q-icon name="admin_panel_settings" color="primary" /></template>
      Technische Tool-Einstellungen können nur von Administratoren geändert werden.
    </q-banner>

    <q-banner v-else-if="loadFailed" rounded class="bg-negative text-white q-mb-md">
      <template #avatar><q-icon name="error_outline" /></template>
      Die Tool-Einstellungen konnten nicht geladen werden.
      <template #action><q-btn flat no-caps color="white" label="Erneut laden" @click="load" /></template>
    </q-banner>

    <q-form v-else-if="auth.isAdmin" ref="formRef" @submit.prevent="save">
      <q-card v-for="group in form.groups" :key="group.id" flat class="app-surface q-mb-md">
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
                  <div class="text-subtitle2 text-weight-bold">{{ tool.label }}</div>
                  <div class="text-caption app-muted">{{ descriptions[tool.name] || tool.name }}</div>
                </div>
              </div>
              <q-toggle v-model="tool.available" class="col-12 col-sm-auto" color="primary" label="Verfügbar" />
            </div>

            <div v-if="tool.name === 'webfetch'" class="row q-col-gutter-md q-mt-sm">
              <q-toggle v-model="form.webFetch.publicAddressesOnly" class="col-12" color="primary" label="Nur öffentliche Adressen zulassen" />
              <q-input v-model.trim="form.webFetch.userAgent" class="col-12 col-md-8" outlined maxlength="512" label="User-Agent" :rules="[userAgentRule]" />
              <q-input v-model.number="form.webFetch.maxResponseMiB" class="col-12 col-md-4" outlined type="number" min="1" max="256" suffix="MiB" label="Maximale Antwortgröße" :rules="[responseLimitRule]" />
            </div>

            <q-input
              v-if="mediaByName[tool.name]"
              v-model.number="form.timeouts[mediaByName[tool.name]].timeoutSeconds"
              class="q-mt-md"
              outlined type="number" min="10" max="3600" step="10"
              label="Zeitlimit" suffix="Sekunden" :rules="[timeoutRule]"
            />
          </q-card-section>
          <q-separator v-if="index < group.tools.length - 1" />
        </template>
      </q-card>
      <q-inner-loading :showing="loading" />
    </q-form>
  </section>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useQuasar } from 'quasar'
import { onBeforeRouteLeave } from 'vue-router'
import { useConfirm } from 'src/composables/confirm'
import { useAuthStore } from 'stores/auth'
import { useToolSettingsStore } from 'stores/tool-settings'

defineOptions({ name: 'ToolSettingsPage' })

const $q = useQuasar()
const { confirm } = useConfirm()
const auth = useAuthStore()
const tools = useToolSettingsStore()
const formRef = ref(null)
const saving = ref(false)
const loadFailed = ref(false)
const saved = ref('')
const mediaByName = {
  generate_image: 'generateImage', edit_image: 'editImage', generate_speech: 'generateSpeech',
  generate_audio: 'generateAudio', transcribe_audio: 'transcribeAudio', analyze_video: 'analyzeVideo'
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
const form = reactive({ groups: [], webFetch: {}, timeouts: {} })
const payload = () => ({
  configuration: {
    toolAvailability: Object.fromEntries(form.groups.flatMap((group) => group.tools.map((tool) => [tool.name, tool.available]))),
    webFetch: { ...form.webFetch },
    ...Object.fromEntries(Object.values(mediaByName).map((field) => [field, { ...form.timeouts[field] }]))
  },
})
const dirty = computed(() => Boolean(saved.value) && JSON.stringify(payload()) !== saved.value)
const loading = computed(() => tools.loading || tools.configurationLoading)
const timeoutRule = (value) => Number.isInteger(value) && value >= 10 && value <= 3600 || '10 bis 3600 Sekunden eingeben.'
const responseLimitRule = (value) => Number.isInteger(value) && value >= 1 && value <= 256 || '1 bis 256 MiB eingeben.'
const userAgentRule = (value) => typeof value === 'string' && value.length >= 1 && value.length <= 512
  && [...value].every((character) => character.charCodeAt(0) >= 32 && character.charCodeAt(0) <= 126)
  || '1 bis 512 sichtbare ASCII-Zeichen eingeben.'

async function load() {
  if (!auth.isAdmin) return
  loadFailed.value = false
  try {
    await tools.fetchConfiguration()
    form.groups = tools.configurationGroups.map((group) => ({ ...group, tools: group.tools.map((tool) => ({ ...tool })) }))
    form.webFetch = { ...tools.configurationWebFetch }
    form.timeouts = Object.fromEntries(Object.values(mediaByName).map((field) => [field, { ...tools[field] }]))
    saved.value = JSON.stringify(payload())
  } catch {
    loadFailed.value = true
  }
}

async function save() {
  if (!await formRef.value?.validate()) return
  saving.value = true
  try {
    const value = payload()
    await tools.saveConfiguration(value.configuration)
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
onBeforeRouteLeave(confirmDiscardedChanges)
</script>
