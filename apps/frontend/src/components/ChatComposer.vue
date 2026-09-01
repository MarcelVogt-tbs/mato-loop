<template>
  <div class="chat-composer q-pa-md">
    <q-card flat class="app-surface chat-composer__card">
      <q-form @submit.prevent="submit">
        <q-input
          ref="input"
          :model-value="draft"
          borderless
          autogrow
          autofocus
          type="textarea"
          rows="1"
          class="chat-composer__input"
          :placeholder="inputPlaceholder"
          :disable="inputDisabled"
          @update:model-value="$emit('update:draft', $event)"
          @keydown.enter.exact.prevent="submit"
          @paste="handlePaste"
        />
        <ComposerAttachments
          :attachments="attachments"
          :disabled="inputDisabled"
          class="q-mb-sm"
          @remove="$emit('remove-attachment', $event)"
        />
        <div v-if="recording" class="composer-recording app-surface-tonal q-mb-sm">
          <div class="row items-center q-gutter-sm">
            <q-icon name="fiber_manual_record" color="negative" class="composer-recording__pulse" />
            <span class="text-weight-medium">Diktat läuft</span>
            <span class="text-caption app-muted">{{ recordingDuration }}</span>
            <q-space />
            <q-btn
              flat
              dense
              no-caps
              color="negative"
              icon="close"
              label="Abbrechen"
              type="button"
              @click="cancelDictation"
            />
            <q-btn
              unelevated
              dense
              no-caps
              color="primary"
              icon="stop"
              label="Stoppen und transkribieren"
              type="button"
              @click="finishDictation"
            />
          </div>
          <div class="text-caption app-muted q-mt-xs">
            Die Aufnahme wird als PCM-WAV transkribiert. Nach fünf Minuten stoppt sie automatisch.
          </div>
        </div>
        <q-banner v-else-if="starting" dense rounded class="app-surface-tonal q-mb-sm">
          <template #avatar><q-spinner color="primary" size="22px" /></template>
          Mikrofon wird gestartet ...
        </q-banner>
        <q-banner v-else-if="transcribing" dense rounded class="app-surface-tonal q-mb-sm">
          <template #avatar><q-spinner color="primary" size="22px" /></template>
          Diktat wird transkribiert ...
        </q-banner>
        <q-banner
          v-else-if="currentDictation?.status === 'failed'"
          dense
          rounded
          class="composer-dictation-failed app-surface-tonal q-mb-sm"
        >
          <template #avatar><q-icon name="record_voice_over" color="warning" /></template>
          <div class="text-weight-medium">Diktat ist noch nicht transkribiert</div>
          <div class="text-caption app-muted">{{ currentDictation.message }}</div>
          <template #action>
            <div class="composer-dictation-actions row q-gutter-xs">
              <q-btn flat dense no-caps icon="refresh" label="Erneut versuchen" @click="retryDictation" />
              <q-btn flat dense no-caps icon="attach_file" label="Als Anhang" @click="attachDictation" />
              <q-btn flat dense no-caps icon="download" label="Herunterladen" @click="downloadDictation" />
              <q-btn flat dense no-caps color="negative" icon="delete_outline" label="Verwerfen" @click="discardDictation" />
            </div>
          </template>
        </q-banner>
        <div class="chat-composer__footer row items-center q-gutter-xs">
          <SessionSettingsFields
            ref="settingsFields"
            :session="session"
            :disabled="interactionDisabled"
            :fields="['agent', 'model', 'reasoning']"
            compact
            @update="$emit('update', $event)"
          />
          <q-chip v-if="pendingCount" dense square color="warning" text-color="dark" icon="pending_actions">
            {{ pendingCount }} offen
          </q-chip>
          <q-space />
          <input
            ref="attachmentInput"
            class="composer-file-input"
            type="file"
            multiple
            tabindex="-1"
            @change="selectAttachmentFiles"
          >
          <div class="chat-composer__actions row items-center">
            <q-btn
              flat
              round
              dense
              type="button"
              icon="tune"
              class="composer-session-settings"
              aria-label="Sessioneinstellungen öffnen"
              :disable="interactionDisabled"
            >
              <q-icon
                v-if="sessionSettingsWarning"
                name="warning_amber"
                color="warning"
                size="15px"
                class="composer-session-settings__warning"
              />
              <q-tooltip>{{ sessionSettingsWarning || 'Sessioneinstellungen' }}</q-tooltip>
              <q-popup-proxy
                v-model="settingsOpen"
                :breakpoint="600"
                :maximized="$q.screen.lt.sm"
                anchor="top right"
                self="bottom right"
                :offset="[0, 8]"
              >
                <q-card class="chat-session-settings-panel column no-wrap">
                  <q-toolbar class="chat-session-settings-panel__toolbar">
                    <q-icon name="tune" size="20px" class="q-mr-sm" />
                    <q-toolbar-title class="text-subtitle2 text-weight-medium">
                      Sessioneinstellungen
                    </q-toolbar-title>
                    <q-btn
                      flat
                      round
                      :dense="$q.screen.gt.xs"
                      icon="close"
                      aria-label="Sessioneinstellungen schließen"
                      v-close-popup
                    />
                  </q-toolbar>

                  <q-separator />

                  <q-card-section class="chat-session-settings-panel__content scroll">
                    <SessionSettingsFields
                      :session="session"
                      :disabled="interactionDisabled"
                      :fields="['computer', 'memory', 'webSearch']"
                      class="chat-session-settings-panel__fields"
                      @update="$emit('update', $event)"
                    />
                  </q-card-section>
                </q-card>
              </q-popup-proxy>
            </q-btn>
            <q-btn
              flat
              round
              dense
              icon="attach_file"
              type="button"
              aria-label="Dateien anhängen"
              :disable="inputDisabled || starting || recording || transcribing"
              @click="attachmentInput?.click()"
            >
              <q-tooltip>Dateien anhängen</q-tooltip>
            </q-btn>
            <q-btn
              flat
              round
              dense
              icon="mic"
              type="button"
              aria-label="Diktat starten"
              :loading="starting || transcribing"
              :disable="inputDisabled || starting || recording || transcribing || !micAvailable"
              @click="startDictation"
            >
              <q-tooltip>{{ micUnavailableReason || 'Diktat starten' }}</q-tooltip>
            </q-btn>
            <q-btn
              v-if="running"
              round
              unelevated
              color="negative"
              icon="stop"
              aria-label="Agent-Anfrage stoppen"
              type="button"
              :loading="stopping"
              :disable="stopping"
              @click="$emit('stop')"
            />
            <q-btn
              v-if="!running || draftHasContent"
              round
              unelevated
              :color="queueMode ? 'secondary' : 'primary'"
              :icon="queueMode ? 'schedule_send' : 'arrow_upward'"
              :aria-label="queueMode ? 'Nachricht vormerken' : 'Nachricht senden'"
              type="submit"
              :disable="sendDisabled"
            >
              <q-tooltip v-if="queueMode">
                Wird automatisch gesendet, sobald der Chat bereit ist
              </q-tooltip>
            </q-btn>
          </div>
        </div>

      </q-form>
    </q-card>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useQuasar } from 'quasar'
import ComposerAttachments from 'components/ComposerAttachments.vue'
import SessionSettingsFields from 'components/SessionSettingsFields.vue'
import { useAgentIdentityStore } from 'stores/agent-identity'
import { useChatStore } from 'stores/chat'
import { useInstanceLimitsStore } from 'stores/instance-limits'
import { useProviderStore } from 'stores/providers'
import { useToolSettingsStore } from 'stores/tool-settings'
import { api } from 'src/boot/axios'
import { recorderErrorMessage, useAudioRecorder } from 'src/composables/audio-recorder'
import { clipboardAttachmentFiles } from 'src/utils/attachment-drafts'
import { formatAttachmentDuration } from 'src/utils/attachments'

defineOptions({ name: 'ChatComposer' })

const props = defineProps({
  session: { type: Object, default: null },
  draft: { type: String, default: '' },
  disabled: Boolean,
  running: Boolean,
  stopping: Boolean,
  pendingCount: { type: Number, default: 0 },
  queueMode: Boolean,
  attachments: { type: Array, default: () => [] }
})
const emit = defineEmits(['add-attachments', 'remove-attachment', 'send', 'stop', 'update', 'update:draft'])
const $q = useQuasar()
const identity = useAgentIdentityStore()
const chat = useChatStore()
const instanceLimits = useInstanceLimitsStore()
const providers = useProviderStore()
const toolSettings = useToolSettingsStore()
const input = ref(null)
const attachmentInput = ref(null)
const settingsFields = ref(null)
const settingsOpen = ref(false)
let transcriptionRequest = null
const {
  starting,
  recording,
  durationMs: recordingDurationMs,
  limitReached,
  autoStoppedFile,
  startRecording,
  stopRecording,
  cancelRecording,
  clearAutoStoppedFile
} = useAudioRecorder()
const dictationKey = computed(() => props.session?.id || null)
const currentDictation = computed(() => chat.getAudioDictation(dictationKey.value))
const transcribing = computed(() => currentDictation.value?.status === 'transcribing')
const recordingDuration = computed(() => formatAttachmentDuration(recordingDurationMs.value) || '0:00')
const effectiveModelId = computed(() => props.session?.modelId || providers.defaultModelId)
const selectedModelConfig = computed(() => providers.models.find((model) => (
  model.id === effectiveModelId.value
)))
const modelSupportsTools = computed(() => selectedModelConfig.value?.supportsTools !== false)
const transcriptionModelAvailable = computed(() => (
  Boolean(providers.audioTranscriptionModelId)
  && providers.capabilityOptions('audio_transcription')
    .some((option) => option.value === providers.audioTranscriptionModelId)
))
const micUnavailableReason = computed(() => {
  if (toolSettings.loading || !toolSettings.loaded && !toolSettings.loadFailed) {
    return 'Die Diktateinstellungen werden geladen.'
  }
  if (toolSettings.loadFailed) return 'Die Diktateinstellungen konnten nicht geladen werden.'
  if (!toolSettings.transcribeAudioAvailable) {
    return 'Audiotranskription ist in den Tool-Einstellungen deaktiviert.'
  }
  if (!transcriptionModelAvailable.value) {
    return 'Es ist kein verfügbares Standardmodell für Audiotranskription eingerichtet.'
  }
  return ''
})
const micAvailable = computed(() => !micUnavailableReason.value)
const interactionDisabled = computed(() => (
  !props.session || props.disabled || props.running || props.pendingCount > 0
))
const inputDisabled = computed(() => !props.session || props.disabled)
const draftHasContent = computed(() => Boolean(props.draft.trim() || props.attachments.length))
const inputPlaceholder = computed(() => `Eine Nachricht an ${identity.displayName} senden`)
const sendDisabled = computed(() => (
  inputDisabled.value
  || (props.running && !props.queueMode)
  || starting.value
  || recording.value
  || transcribing.value
  || !modelSupportsTools.value
  || (!props.draft.trim() && !props.attachments.length)
))
const sessionSettingsWarning = computed(() => settingsFields.value?.warning || '')

function submit() {
  const content = props.draft.trim()
  if (sendDisabled.value) return
  emit('send', content)
}

function selectAttachmentFiles(event) {
  const files = Array.from(event.target.files || [])
  if (files.length) emit('add-attachments', files)
  event.target.value = ''
}

function handlePaste(event) {
  const files = clipboardAttachmentFiles(event)
  if (!files.length) return
  event.preventDefault()
  emit('add-attachments', files)
}

async function startDictation() {
  if (!micAvailable.value) return
  try {
    await startRecording()
  } catch (error) {
    $q.notify({ type: 'negative', message: recorderErrorMessage(error) })
  }
}

async function cancelDictation() {
  await cancelRecording()
  input.value?.focus()
}

async function finishDictation() {
  const file = await stopRecording().catch(() => null)
  if (!file) {
    $q.notify({ type: 'negative', message: 'Das Diktat konnte nicht verarbeitet werden.' })
    return
  }
  await transcribeDictation(file, dictationKey.value)
}

async function transcribeDictation(file, key) {
  const controller = new AbortController()
  transcriptionRequest?.controller.abort()
  transcriptionRequest = { controller, key }
  chat.setAudioDictation({ file, status: 'transcribing', message: '' }, key)
  try {
    if (file.size > instanceLimits.maxUploadBytes) {
      chat.setAudioDictation({
        file,
        status: 'failed',
        message: `Das Diktat ist größer als ${instanceLimits.maxUploadMib} MiB und kann nicht übertragen werden.`
      }, key)
      return
    }
    const formData = new FormData()
    formData.append('audio', file, file.name)
    const { data } = await api.post('/media/transcriptions', formData, { signal: controller.signal })
    if (controller.signal.aborted || dictationKey.value !== key) return
    const transcript = typeof data?.text === 'string' ? data.text.trim() : ''
    if (transcript) {
      const separator = props.draft && !/\s$/.test(props.draft) ? ' ' : ''
      emit('update:draft', `${props.draft}${separator}${transcript}`)
      chat.setAudioDictation(null, key)
    } else {
      chat.setAudioDictation({ file, status: 'failed', message: 'Keine Sprache erkannt.' }, key)
      $q.notify({ type: 'negative', message: 'Keine Sprache erkannt.' })
    }
  } catch (error) {
    if (controller.signal.aborted) {
      if (chat.getAudioDictation(key)?.status === 'transcribing') {
        chat.setAudioDictation({
          file,
          status: 'failed',
          message: 'Die Transkription wurde beim Sessionwechsel angehalten.'
        }, key)
      }
    } else {
      chat.setAudioDictation({
        file,
        status: 'failed',
        message: 'Die Transkription ist fehlgeschlagen. Das Diktat bleibt erhalten.'
      }, key)
    }
    if (!controller.signal.aborted && !error?.config) {
      $q.notify({ type: 'negative', message: 'Das Diktat konnte nicht verarbeitet werden.' })
    }
  } finally {
    if (transcriptionRequest?.controller === controller) transcriptionRequest = null
    input.value?.focus()
  }
}

function retryDictation() {
  if (currentDictation.value?.file) {
    void transcribeDictation(currentDictation.value.file, dictationKey.value)
  }
}

function attachDictation() {
  const entry = currentDictation.value
  if (!entry?.file) return
  chat.setAudioDictation(null, dictationKey.value)
  emit('add-attachments', [entry.file], (result) => {
    if (result?.error) chat.setAudioDictation(entry, dictationKey.value)
  })
}

function downloadDictation() {
  const file = currentDictation.value?.file
  if (!file || typeof URL?.createObjectURL !== 'function') return
  const url = URL.createObjectURL(file)
  const link = document.createElement('a')
  link.href = url
  link.download = file.name || 'diktat.wav'
  link.click()
  URL.revokeObjectURL(url)
}

function discardDictation() {
  chat.setAudioDictation(null, dictationKey.value)
  input.value?.focus()
}

async function cancelAudioActivity() {
  transcriptionRequest?.controller.abort()
  await cancelRecording()
}

watch(autoStoppedFile, (file) => {
  if (!file) return
  const key = dictationKey.value
  clearAutoStoppedFile()
  if (limitReached.value) {
    $q.notify({ type: 'info', message: 'Die maximale Aufnahmedauer ist erreicht. Das Diktat wird transkribiert.' })
  }
  void transcribeDictation(file, key)
})

watch(dictationKey, (key, previousKey) => {
  if (key === previousKey) return
  if (transcriptionRequest?.key === previousKey) transcriptionRequest.controller.abort()
  if (starting.value || recording.value) void cancelRecording()
})

onBeforeUnmount(() => {
  transcriptionRequest?.controller.abort()
})

defineExpose({
  focus: () => input.value?.focus(),
  cancelAudioActivity,
  hasAudioActivity: () => starting.value || recording.value || transcribing.value
})
</script>
