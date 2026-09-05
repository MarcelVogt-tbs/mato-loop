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
            <span class="text-weight-medium">Aufnahme läuft</span>
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
              @click="cancelVoiceRecording"
            />
            <q-btn
              unelevated
              dense
              no-caps
              color="primary"
              icon="stop"
              label="Als Sprachnachricht anhängen"
              type="button"
              @click="finishVoiceRecording"
            />
          </div>
          <div class="text-caption app-muted q-mt-xs">
            Die Aufnahme bleibt als Audio erhalten und wird beim Senden transkribiert.
            Nach fünf Minuten stoppt sie automatisch.
          </div>
        </div>
        <q-banner v-else-if="starting" dense rounded class="app-surface-tonal q-mb-sm">
          <template #avatar><q-spinner color="primary" size="22px" /></template>
          Mikrofon wird gestartet ...
        </q-banner>
        <q-banner v-else-if="finalizing" dense rounded class="app-surface-tonal q-mb-sm">
          <template #avatar><q-spinner color="primary" size="22px" /></template>
          Aufnahme wird vorbereitet ...
        </q-banner>
        <q-banner
          v-if="currentDictation?.files?.length"
          dense
          rounded
          class="composer-dictation-failed app-surface-tonal q-mb-sm"
        >
          <template #avatar><q-icon name="record_voice_over" color="warning" /></template>
          <div class="text-weight-medium">Noch nicht angehängte Aufnahmen</div>
          <div class="text-caption app-muted">Die Aufnahmen bleiben bis zum Anhängen oder Verwerfen hier erhalten.</div>
          <div v-for="file in currentDictation.files" :key="file.name" class="q-mt-sm">
            <div class="text-caption ellipsis">{{ file.name }}</div>
            <div class="composer-dictation-actions row q-gutter-xs">
              <q-btn flat dense no-caps icon="attach_file" label="Anhängen" :disable="inputDisabled" @click="attachRecording(file, audioScope)" />
              <q-btn flat dense no-caps icon="download" label="Herunterladen" @click="downloadRecording(file)" />
              <q-btn flat dense no-caps color="negative" icon="delete_outline" label="Verwerfen" @click="discardRecording(file)" />
            </div>
          </div>
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
              :disable="inputDisabled || starting || recording || finalizing"
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
              aria-label="Sprachnachricht aufnehmen"
              :loading="starting || finalizing"
              :disable="inputDisabled || starting || recording || finalizing"
              @click="startVoiceRecording"
            >
              <q-tooltip>{{ transcriptionUnavailableReason || 'Sprachnachricht aufnehmen, Transkription beim Senden' }}</q-tooltip>
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
import { useProviderStore } from 'stores/providers'
import { useToolSettingsStore } from 'stores/tool-settings'
import { useConfirm } from 'src/composables/confirm'
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
const providers = useProviderStore()
const toolSettings = useToolSettingsStore()
const input = ref(null)
const attachmentInput = ref(null)
const settingsFields = ref(null)
const settingsOpen = ref(false)
const { confirm } = useConfirm()
let recordingScope = null
let finishingRequest = null
let unmounted = false
const {
  starting,
  recording,
  finalizing,
  durationMs: recordingDurationMs,
  startRecording,
  stopRecording,
  cancelRecording
} = useAudioRecorder({
  onAutoStopped(file) {
    retainRecording(file, recordingScope)
    if (!unmounted) {
      $q.notify({ type: 'info', message: 'Die maximale Aufnahmedauer ist erreicht. Die Aufnahme wurde vorbereitet.' })
    }
  }
})
const audioScope = computed(() => ({
  sessionId: props.session?.id || null,
  projectId: chat.sessionsProjectId,
  ownerUserId: chat.draftOwnerUserId
}))
const currentDictation = computed(() => chat.getAudioDictation(audioScope.value.sessionId, audioScope.value.projectId))
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
const transcriptionUnavailableReason = computed(() => {
  if (toolSettings.loading || !toolSettings.loaded && !toolSettings.loadFailed) {
    return 'Sprachnachricht aufnehmen; Transkriptionseinstellungen werden geladen.'
  }
  if (toolSettings.loadFailed) return 'Sprachnachricht aufnehmen; automatische Transkription derzeit nicht verfügbar.'
  if (!toolSettings.transcribeAudioAvailable) {
    return 'Sprachnachricht aufnehmen; automatische Transkription ist deaktiviert.'
  }
  if (!transcriptionModelAvailable.value) {
    return 'Sprachnachricht aufnehmen; kein Transkriptionsmodell verfügbar.'
  }
  return ''
})
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
  || finalizing.value
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

async function startVoiceRecording() {
  if (inputDisabled.value || starting.value || recording.value || finalizing.value) return
  recordingScope = { ...audioScope.value }
  try {
    await startRecording()
  } catch (error) {
    $q.notify({ type: 'negative', message: recorderErrorMessage(error) })
  }
}

async function cancelVoiceRecording() {
  const scope = recordingScope
  if (!await confirm('Die laufende Aufnahme wird verworfen.', 'Aufnahme abbrechen?')) return
  if (scope !== recordingScope) return
  await cancelAudioActivity()
  input.value?.focus()
}

function finishVoiceRecording() {
  if (finishingRequest) return finishingRequest
  if (!recording.value) return finalizing.value ? stopRecording().catch(() => {}) : Promise.resolve()
  const scope = recordingScope
  finishingRequest = (async () => {
    try {
      const file = await stopRecording()
      if (file) retainRecording(file, scope)
    } catch {
      $q.notify({ type: 'negative', message: 'Die Aufnahme konnte nicht verarbeitet werden.' })
    } finally {
      finishingRequest = null
      input.value?.focus()
    }
  })()
  return finishingRequest
}

function retainRecording(file, scope) {
  if (!scope || scope.discarded || scope.ownerUserId !== chat.draftOwnerUserId) return
  const files = chat.getAudioDictation(scope.sessionId, scope.projectId)?.files || []
  if (files.includes(file)) return
  chat.setAudioDictation({ files: [...files, file] }, scope.sessionId, scope.projectId)
  if (!unmounted) attachRecording(file, scope)
}

function removeRecoveryFile(file, scope) {
  const files = (chat.getAudioDictation(scope.sessionId, scope.projectId)?.files || []).filter((item) => item !== file)
  chat.setAudioDictation(files.length ? { files } : null, scope.sessionId, scope.projectId)
}

function attachRecording(file, scope) {
  emit('add-attachments', [file], (result) => {
    if (result?.added === 1 && scope.ownerUserId === chat.draftOwnerUserId) removeRecoveryFile(file, scope)
  }, { ...scope, voiceMessage: true })
}

function downloadRecording(file) {
  if (!file || typeof URL?.createObjectURL !== 'function') return
  const url = URL.createObjectURL(file)
  const link = document.createElement('a')
  link.href = url
  link.download = file.name || 'sprachnachricht.wav'
  link.click()
  URL.revokeObjectURL(url)
}

async function discardRecording(file) {
  const scope = { ...audioScope.value }
  if (!await confirm('Die Aufnahme wird unwiderruflich entfernt.', 'Aufnahme verwerfen?')) return
  if (scope.ownerUserId !== chat.draftOwnerUserId) return
  removeRecoveryFile(file, scope)
  input.value?.focus()
}

async function cancelAudioActivity() {
  if (recordingScope) recordingScope.discarded = true
  await cancelRecording()
}

async function prepareAudioNavigation() {
  if (starting.value) await cancelAudioActivity()
  else await finishVoiceRecording()
}

chat.$onAction(({ name, args, after }) => {
  if (name === 'clearAllAttachmentDrafts') void cancelAudioActivity()
  if (name === 'deleteSession' && args[0] === recordingScope?.sessionId) {
    const scope = recordingScope
    after(() => {
      scope.discarded = true
      if (scope === recordingScope) void cancelAudioActivity()
    })
  }
  if (name === 'removeProjectScope' && args[0] === recordingScope?.projectId) {
    void cancelAudioActivity()
  }
})

watch([() => props.session?.id, () => chat.sessionsProjectId, () => chat.draftOwnerUserId], () => {
  if (recordingScope?.ownerUserId !== chat.draftOwnerUserId) void cancelAudioActivity()
  else void prepareAudioNavigation()
}, { flush: 'sync' })

onBeforeUnmount(() => {
  unmounted = true
})

defineExpose({
  focus: () => input.value?.focus(),
  cancelAudioActivity,
  prepareAudioNavigation,
  hasAudioActivity: () => starting.value || recording.value || finalizing.value
})
</script>
