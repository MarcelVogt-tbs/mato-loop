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
          @keydown="handleComposerKeydown"
          @keydown.enter.exact.prevent="submit"
          @paste="handlePaste"
        />
        <ComposerAttachments
          :attachments="attachments"
          :disabled="inputDisabled"
          class="q-mb-sm"
          @remove="$emit('remove-attachment', $event)"
        />
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
            :agent-profile-id="chat.activeAgentProfileId"
            :agent-selectable="!inputDisabled && (running || queueMode)"
            :fields="['agent', 'model', 'reasoning']"
            compact
            @update="$emit('update', $event)"
            @agent-popup-hide="input?.focus()"
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
            <div
              v-if="recording"
              class="composer-recording-status row items-center text-caption text-negative"
              aria-hidden="true"
            >
              <q-icon name="fiber_manual_record" size="12px" class="composer-recording__pulse" />
              <span>{{ recordingDuration }}</span>
            </div>
            <q-btn
              flat
              round
              dense
              :icon="recording ? 'stop' : 'mic'"
              :color="recording ? 'negative' : undefined"
              type="button"
              :aria-label="recording ? 'Sprachnachricht beenden' : 'Sprachnachricht aufnehmen'"
              :loading="starting || finalizing"
              :disable="inputDisabled || starting || finalizing"
              @click="toggleVoiceRecording"
            >
              <q-tooltip>
                {{ recording
                  ? 'Aufnahme beenden und anhängen'
                  : transcriptionUnavailableReason || 'Sprachnachricht aufnehmen, Transkription beim Senden' }}
              </q-tooltip>
            </q-btn>
            <q-btn
              v-if="running"
              round
              unelevated
              :color="canSendDuringRun ? sendMode === 'queue' ? 'secondary' : 'primary' : 'negative'"
              :icon="canSendDuringRun ? sendMode === 'queue' ? 'schedule_send' : 'arrow_upward' : 'stop'"
              :aria-label="canSendDuringRun
                ? voiceActivity ? voiceSendLabel : sendMode === 'queue' ? 'Nachricht vormerken' : 'Nachricht jetzt senden'
                : 'Agent-Anfrage stoppen'"
              type="button"
              :loading="sendingRecording || starting || finalizing || (!canSendDuringRun && stopping)"
              :disable="stopping || (voiceActivity && !canFinishAndSend)"
              @click="canSendDuringRun ? submit() : $emit('stop')"
            />
            <q-btn
              v-if="canSendDuringRun && (!voiceActivity || canFinishAndSend)"
              flat
              round
              dense
              icon="arrow_drop_down"
              aria-label="Versandart auswählen"
              type="button"
              :disable="stopping"
            >
              <q-menu anchor="top right" self="bottom right" auto-close @hide="input?.focus()">
                <q-list>
                  <q-item clickable @click="sendMode = 'interrupt'">
                    <q-item-section>Jetzt senden</q-item-section>
                    <q-item-section side><q-icon v-if="sendMode === 'interrupt'" name="check" /></q-item-section>
                  </q-item>
                  <q-item clickable @click="sendMode = 'queue'">
                    <q-item-section>Nach dem Lauf senden</q-item-section>
                    <q-item-section side><q-icon v-if="sendMode === 'queue'" name="check" /></q-item-section>
                  </q-item>
                </q-list>
              </q-menu>
            </q-btn>
            <q-btn
              v-if="!running"
              round
              unelevated
              :color="queueMode ? 'secondary' : 'primary'"
              :icon="queueMode ? 'schedule_send' : 'arrow_upward'"
              :aria-label="voiceActivity ? voiceSendLabel : queueMode ? 'Nachricht vormerken' : 'Nachricht senden'"
              :type="voiceActivity ? 'button' : 'submit'"
              :loading="sendingRecording || starting || finalizing"
              :disable="voiceActivity ? !canFinishAndSend : sendDisabled"
              @click="voiceActivity && submit()"
            />
          </div>
        </div>

      </q-form>
    </q-card>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useQuasar } from 'quasar'
import ComposerAttachments from 'src/features/chat/components/ComposerAttachments.vue'
import SessionSettingsFields from 'src/features/chat/components/SessionSettingsFields.vue'
import { useAgentProfileStore } from 'stores/agent-profiles'
import { useAgentIdentityStore } from 'stores/agent-identity'
import { useChatStore } from 'stores/chat'
import { useProviderStore } from 'stores/providers'
import { useToolSettingsStore } from 'stores/tool-settings'
import { useConfirm } from 'src/composables/confirm'
import { recorderErrorMessage, useAudioRecorder } from 'src/features/chat/audio-recorder'
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
const agentProfiles = useAgentProfileStore()
const chat = useChatStore()
const providers = useProviderStore()
const toolSettings = useToolSettingsStore()
const input = ref(null)
const attachmentInput = ref(null)
const settingsFields = ref(null)
const settingsOpen = ref(false)
const sendMode = ref('interrupt')
const sendingRecording = ref(false)
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
const selectedAgent = computed(() => agentProfiles.byId(
  props.session?.agentProfileId || agentProfiles.defaultAgentProfileId
))
const effectiveModelId = computed(() => props.session?.modelId || selectedAgent.value?.defaultModelId)
const selectedModelConfig = computed(() => providers.models.find((model) => (
  model.id === effectiveModelId.value
)))
const modelSupportsTools = computed(() => selectedModelConfig.value?.supportsTools !== false)
const transcriptionModelAvailable = computed(() => (
  Boolean(selectedAgent.value?.audioTranscriptionModelId)
  && providers.capabilityOptions('audio_transcription')
    .some((option) => option.value === selectedAgent.value.audioTranscriptionModelId)
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
const voiceActivity = computed(() => starting.value || recording.value || finalizing.value || sendingRecording.value)
const voiceSendLabel = computed(() => (
  props.running ? sendMode.value === 'queue' : props.queueMode
) ? 'Aufnahme beenden und vormerken' : 'Aufnahme beenden und senden')
const canFinishAndSend = computed(() => (
  recording.value && !inputDisabled.value && !starting.value && !finalizing.value && !sendingRecording.value
  && modelSupportsTools.value && (!props.running || props.queueMode)
))
const sendDisabled = computed(() => (
  inputDisabled.value
  || (props.running && !props.queueMode)
  || starting.value
  || recording.value
  || finalizing.value
  || sendingRecording.value
  || !modelSupportsTools.value
  || (!props.draft.trim() && !props.attachments.length)
))
const canSendDuringRun = computed(() => props.running && (voiceActivity.value || (
  props.queueMode && draftHasContent.value && !sendDisabled.value
)))
const sessionSettingsWarning = computed(() => settingsFields.value?.warning || '')

function submit() {
  if (recording.value) {
    void finishAndSendRecording()
    return
  }
  const content = props.draft.trim()
  if (sendDisabled.value) return
  emit('send', content, { interrupt: props.running && sendMode.value === 'interrupt' })
}

function handleComposerKeydown(event) {
  if (
    event.defaultPrevented || event.repeat || event.isComposing || event.altKey
    || event.ctrlKey === event.metaKey || event.code !== 'Period'
  ) return
  if (event.shiftKey) {
    if (!props.running || props.stopping) return
    sendMode.value = sendMode.value === 'queue' ? 'interrupt' : 'queue'
  } else {
    if (inputDisabled.value || agentProfiles.loading || agentProfiles.agents.length < 2) return
    const agents = agentProfiles.agents
    const current = chat.activeAgentProfileId || agentProfiles.defaultAgentProfileId
    const index = agents.findIndex((agent) => agent.id === current)
    emit('update', { agent_profile_id: agents[(index + 1) % agents.length].id })
  }
  event.preventDefault()
  event.stopPropagation()
}

async function finishAndSendRecording() {
  if (!canFinishAndSend.value || !recordingScope) return
  const scope = recordingScope
  const content = props.draft.trim()
  const interrupt = props.running && sendMode.value === 'interrupt'
  sendingRecording.value = true
  try {
    const recordedFile = await finishVoiceRecording()
    if (
      !recordedFile || unmounted || scope.ownerUserId !== chat.draftOwnerUserId
      || scope.sessionId !== (chat.current?.id || null)
      || scope.sessionId !== audioScope.value.sessionId
      || scope.projectId !== chat.sessionsProjectId
      || !chat.activeAttachmentDraft.some((attachment) => attachment.file === recordedFile && attachment.voiceMessage)
    ) return
    emit('send', content, { interrupt })
  } finally {
    sendingRecording.value = false
  }
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

function toggleVoiceRecording() {
  return recording.value ? finishVoiceRecording() : startVoiceRecording()
}

function finishVoiceRecording() {
  if (finishingRequest) return finishingRequest
  if (!recording.value) return finalizing.value ? stopRecording().catch(() => {}) : Promise.resolve()
  const scope = recordingScope
  finishingRequest = (async () => {
    try {
      const file = await stopRecording()
      if (file) retainRecording(file, scope)
      return file
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
  hasAudioActivity: () => voiceActivity.value
})
</script>
