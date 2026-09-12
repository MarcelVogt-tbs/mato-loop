<template>
  <q-page :style-fn="chatPageStyle" class="chat-page column no-wrap">
    <q-splitter
      v-model="splitPosition"
      class="chat-workspace-splitter col"
      :limits="splitLimits"
      :disable="!desktopControlCenterOpen"
      separator-class="bg-transparent"
      :separator-style="desktopControlCenterOpen
        ? { width: '12px', margin: '0 6px' }
        : { display: 'none' }"
    >
      <template #before>
        <div class="chat-page__pane column no-wrap full-height">
    <q-scroll-area
      id="chat-scroll-area"
      ref="scrollArea"
      class="chat-page__content col"
      :aria-busy="chat.loading ? 'true' : 'false'"
      @keydown="handleScrollKey"
      @mousedown="handleScrollAreaMouseDown"
    >
      <div ref="scrollContent" class="chat-page__scroll-content">
        <div v-if="chat.loading" class="chat-transcript q-pa-md" aria-label="Session wird geladen">
          <div v-for="width in ['42%', '86%', '64%']" :key="width" class="chat-session-skeleton">
            <q-skeleton type="text" width="88px" />
            <q-skeleton type="text" :width="width" height="24px" />
            <q-skeleton type="text" width="72%" />
          </div>
        </div>

        <div
          v-else-if="chat.loadError"
          class="chat-session-state text-center q-pa-lg"
          role="alert"
        >
          <q-icon name="error_outline" color="negative" size="40px" />
          <div class="text-h6 q-mt-sm">Session konnte nicht geladen werden</div>
          <p class="app-muted q-mt-sm">{{ sessionLoadErrorMessage }}</p>
          <q-btn
            unelevated
            no-caps
            color="primary"
            icon="refresh"
            label="Erneut versuchen"
            @click="loadRouteSession"
          />
        </div>

        <div v-else-if="!chat.current" class="chat-welcome text-center q-pa-lg">
          <AppLogo :size="72" />
          <h1 class="text-h4 text-weight-bold q-mb-sm">Willkommen bei MatoLoop</h1>
          <p class="text-body1 app-muted q-mx-auto">
            Was möchtest du tun?
          </p>
          <AgentActivityPanel
            v-if="runPresentation.showActivity"
            :history="liveWorkHistory"
            :activity="chat.activity"
            :presentation="runPresentation"
            :context-summary="chat.streamContextSummary"
            :warnings="chat.runWarnings"
            :display-mode="answerDisplay(chat.runAgentProfileId || chat.current?.activeRun?.agentProfileId)"
            :trusted-image-origins="trustedImageOrigins"
            running
            class="chat-welcome__activity text-left q-mx-auto"
            @resize-start="beginActivityResize"
            @resized="finishActivityResize"
          />
        </div>

        <q-virtual-scroll
          v-else
          :key="chat.current.id"
          ref="virtualScroll"
          class="chat-transcript q-pa-md"
          :items="displayMessages"
          scroll-target="#chat-scroll-area > .scroll"
          :virtual-scroll-item-size="96"
          @virtual-scroll="handleVirtualScroll"
        >
          <template #before>
            <div v-if="chat.olderMessagesLoading" class="row justify-center q-py-sm" role="status">
              <q-spinner color="primary" size="24px" />
              <span class="app-sr-only">Ältere Nachrichten werden geladen</span>
            </div>
            <q-banner
              v-else-if="chat.olderMessagesLoadFailed"
              dense
              rounded
              class="app-surface-tonal q-mb-md"
            >
              Ältere Nachrichten konnten nicht geladen werden.
              <template #action>
                <q-btn flat dense no-caps color="primary" label="Erneut versuchen" @click="loadOlderHistory" />
              </template>
            </q-banner>
          </template>
          <template #default="{ item: message }">
            <div
              :key="message.liveMessageId || message.id"
              :class="[
                'chat-message',
                `chat-message--${message.role}`,
                {
                  'chat-message--turn-continues': message.continuesAssistantTurn,
                  'chat-message--search-target': highlightedMessageId === message.id
                }
              ]"
              :data-message-id="message.id"
              :data-live-message-id="message.liveMessageId || undefined"
              tabindex="-1"
            >
            <div v-if="message.role === 'user' || message.showAgentLabel" class="chat-message__label">
              <span>{{ message.role === 'user' ? userMessageLabel(message) : identity.displayName }}</span>
            </div>
            <template v-if="message.kind === 'runtime_error' && message.error">
              <q-banner rounded class="chat-runtime-error app-surface-tonal" role="alert">
                <template #avatar>
                  <q-icon name="error_outline" color="negative" />
                </template>
                {{ apiErrorMessage(message.error) }}
              </q-banner>
            </template>
            <template
              v-else-if="message.content
                || message.workHistory
                || message.generationStatus === 'interrupted'"
            >
              <AgentActivityPanel
                v-if="message.workHistory"
                :history="message.workHistory"
                :subagents="childrenByMessage.get(message.id) || []"
                :activity="message.currentTurn ? chat.activity : null"
                :presentation="message.currentTurn ? runPresentation : null"
                :reasoning="message.currentTurn ? chat.streamReasoning : null"
                :context-summary="message.currentTurn ? chat.streamContextSummary : null"
                :process-outputs="message.currentTurn ? chat.processOutputs : emptyProcessOutputs"
                :warnings="message.currentTurn ? chat.runWarnings : []"
                :display-mode="answerDisplay(message.agentProfileId)"
                :trusted-image-origins="trustedImageOrigins"
                :running="chat.running && message.currentTurn"
                :class="{
                  'q-mb-sm': message.content
                    || message.attachments?.length
                    || message.generationStatus === 'interrupted'
                }"
                @open-file="openToolFile"
                @open-approval-draft="openApprovalDraft"
                @rendered="handleMessageRendered"
                @resize-start="beginActivityResize"
                @resized="finishActivityResize"
                @trust-image-origin="trustImageOrigin"
              />
              <MessageContent
                v-if="message.content && message.role === 'assistant'"
                class="chat-message__content"
                :content="message.content"
                :research-sources="message.research?.linkedSources || []"
                :streaming="message.streaming"
                :trusted-image-origins="trustedImageOrigins"
                @rendered="handleMessageRendered"
                @trust-image-origin="trustImageOrigin"
              />
              <div v-else-if="message.content" class="chat-message__content">{{ message.content }}</div>
            </template>
            <MessageAttachments
              v-if="message.attachments?.length"
              class="q-mt-sm"
              :attachments="message.attachments"
            />
            <div
              v-if="message.generationStatus === 'interrupted'"
              class="app-muted text-caption row items-center q-gutter-x-xs q-mt-sm"
              role="status"
            >
              <q-icon name="cancel" size="14px" aria-hidden="true" />
              <span>Anfrage abgebrochen</span>
            </div>
            <div
              v-if="message.createdAt
                || showMessageModel(message)
                || isMessageTrustLevel(message.trustLevel)"
              class="chat-message__meta items-center"
            >
              <q-btn
                v-if="message.role === 'user' && !String(message.id).startsWith('local-')"
                flat
                round
                dense
                size="sm"
                icon="undo"
                class="chat-message__meta-action chat-message__meta-action--undo"
                :loading="messageActionId === message.id"
                :disable="messageActionId !== null && messageActionId !== message.id"
                aria-label="Nachricht rückgängig machen"
                @click.stop="undoMessage(message)"
              >
                <q-tooltip>Nachricht rückgängig machen</q-tooltip>
              </q-btn>
              <span
                v-if="isMessageTrustLevel(message.trustLevel)"
                class="row inline items-center"
                role="img"
                :aria-label="`Datenfreigabe: ${messageTrustLabel(message.trustLevel)}`"
              >
                <q-icon
                  :name="message.trustLevel === 'private' ? 'lock' : 'public'"
                  size="1em"
                  aria-hidden="true"
                />
                <q-tooltip>{{ messageTrustLabel(message.trustLevel) }}</q-tooltip>
              </span>
              <span
                v-if="isMessageTrustLevel(message.trustLevel)
                  && (showMessageModel(message) || message.createdAt)"
                aria-hidden="true"
              >·</span>
              <span
                v-if="showMessageModel(message)"
                class="chat-message__meta-model"
                :title="message.modelName"
              >
                {{ message.modelName }}
              </span>
              <span
                v-if="message.createdAt && showMessageModel(message)"
                aria-hidden="true"
              >·</span>
              <time
                v-if="message.createdAt"
                :datetime="message.createdAt"
                :title="formatAbsoluteDateTime(message.createdAt)"
                :aria-label="`Gesendet am ${formatAbsoluteDateTime(message.createdAt)}`"
              >
                {{ formatRelativeDateTime(message.createdAt, messageTimestampNow) }}
              </time>
              <q-btn
                v-if="canRetryMessage(message)"
                flat
                round
                dense
                size="sm"
                icon="refresh"
                color="negative"
                class="chat-message__meta-action chat-message__meta-action--retry"
                :loading="messageActionId === message.id"
                :disable="messageActionId !== null && messageActionId !== message.id"
                aria-label="Fehlgeschlagene Antwort erneut versuchen"
                @click.stop="retryMessage(message)"
              >
                <q-tooltip>Erneut versuchen</q-tooltip>
              </q-btn>
            </div>
            </div>
          </template>
          <template #after>
            <q-banner v-if="subagents.childrenError" rounded class="app-surface-tonal q-mt-md" role="alert">
              {{ apiErrorMessage(subagents.childrenError) }}
              <template #action>
                <q-btn flat no-caps color="primary" label="Subagenten laden" @click="subagents.loadChildren()" />
              </template>
            </q-banner>
            <SubagentRows :children="unmatchedChildren" />
            <ApprovalCard
              v-for="approval in transcript.unmatchedApprovals"
              :key="approval.id"
              :approval="approval"
              class="q-mt-md"
              @open-approval-draft="openApprovalDraft"
            />
          </template>
        </q-virtual-scroll>
      </div>
    </q-scroll-area>

    <div v-if="chat.loading" class="chat-composer-region" aria-hidden="true">
      <q-card flat bordered class="chat-composer__card chat-composer-skeleton">
        <q-skeleton type="text" width="52%" height="28px" />
        <div class="row items-center q-mt-md q-gutter-sm">
          <q-skeleton type="rect" width="160px" height="40px" />
          <q-skeleton type="rect" width="180px" height="40px" />
          <q-space />
          <q-skeleton type="QBtn" />
        </div>
      </q-card>
    </div>

    <div
      v-else-if="!chat.loadError && !chat.current?.readOnly && (!routeSessionId || chat.current)"
      class="chat-composer-region"
    >
      <transition name="chat-scroll-jump">
        <div v-if="chat.current && !followingBottom" class="chat-scroll-to-bottom">
          <q-btn
            round
            unelevated
            icon="keyboard_arrow_down"
            class="chat-scroll-to-bottom__button"
            aria-label="Automatisches Mitscrollen aktivieren"
            @click="enableFollowMode"
          >
            <q-tooltip>Automatisches Mitscrollen aktivieren</q-tooltip>
          </q-btn>
        </div>
      </transition>

      <q-banner
        v-if="chat.outboxLoadFailed"
        dense
        rounded
        class="app-surface-tonal q-mx-md q-mt-sm"
      >
        Der lokale Nachrichtenspeicher konnte nicht geladen werden.
        <template #action>
          <q-btn flat dense no-caps label="Erneut versuchen" @click="chat.loadQueuedMessages()" />
        </template>
      </q-banner>
      <ChatQueuedMessages
        :messages="chat.activeQueuedMessages"
        :paused="chat.queuedSendPaused"
        :sending-id="chat.queueSendingEntryId"
        @resume="chat.resumeQueuedSend"
        @undo="undoQueuedMessage"
      />
      <QuestionDock
        v-if="activeQuestion"
        :key="activeQuestion.id"
        :request="activeQuestion"
        :stopping="chat.stopping"
        :show-stop="chat.running"
        @stop="chat.stop"
      />
      <ChatComposer
        v-else
        ref="composer"
        :session="chat.activeContext"
        :draft="chat.activeDraft"
        :disabled="!chat.outboxLoaded
          || chat.outboxSaving
          || chat.historyActionPending
          || chat.sessionSettingsPending > 0
          || (!chat.current && chat.activeQueuedMessages.length > 0)"
        :running="chat.running"
        :stopping="chat.stopping"
        :pending-count="sessionApprovals.length"
        :queue-mode="chat.messageQueueActive"
        :attachments="chat.activeAttachmentDraft"
        @add-attachments="addAttachmentFiles"
        @remove-attachment="chat.removeAttachmentFile"
        @send="send"
        @stop="chat.stop"
        @update="chat.updateActive"
        @update:draft="chat.setDraft"
      />
    </div>
    <span class="app-sr-only" role="status" aria-live="polite" aria-atomic="true">
      {{ announcedRunStatus }}
    </span>
        </div>
      </template>

      <template #separator>
        <div
          v-if="desktopControlCenterOpen"
          class="chat-workspace-splitter__handle flex flex-center"
          role="separator"
          aria-label="Breite des Chat-Bereichs ändern"
          aria-orientation="vertical"
          :aria-valuemin="MIN_CONTROL_CENTER_SPLIT_RATIO"
          :aria-valuemax="MAX_CONTROL_CENTER_SPLIT_RATIO"
          :aria-valuenow="controlCenter.splitRatio"
          :aria-valuetext="`${controlCenter.splitRatio} Prozent Chat, ${100 - controlCenter.splitRatio} Prozent Control Center`"
          tabindex="0"
          @keydown="resizeWorkspacePane"
        >
          <q-icon name="drag_indicator" size="18px" />
        </div>
      </template>

      <template #after>
        <aside
          v-if="desktopControlCenterOpen"
          class="chat-workspace-pane full-height"
          aria-label="Control Center"
        >
          <ControlCenterPanel />
        </aside>
      </template>
    </q-splitter>
    <SubagentInspector
      v-if="routeSessionId && routeSubagentId"
      :parent-id="routeSessionId"
      :child-id="routeSubagentId"
    />
    <q-dialog
      :model-value="Boolean(approvalDraftPreview)"
      maximized
      aria-labelledby="approval-draft-preview-title"
      @update:model-value="approvalDraftPreview = null"
    >
      <q-card v-if="approvalDraftPreview" class="column no-wrap full-height">
        <q-card-section class="row items-center no-wrap q-gutter-sm">
          <div class="col overflow-hidden">
            <h2 id="approval-draft-preview-title" class="text-subtitle1 text-weight-bold q-my-none ellipsis">{{ approvalDraftPreview.filename }}</h2>
            <div class="text-caption app-muted">Skill-Entwurf des Subagenten · Nur Lesezugriff</div>
          </div>
          <q-btn flat round icon="close" aria-label="Entwurfsvorschau schließen" @click="approvalDraftPreview = null" />
        </q-card-section>
        <q-separator />
        <q-scroll-area class="col">
          <div v-if="approvalDraftPreview.loading" class="row justify-center q-pa-lg" role="status">
            <q-spinner color="primary" size="32px" />
            <span class="app-sr-only">Entwurf wird geladen</span>
          </div>
          <q-banner v-else-if="approvalDraftPreview.error" class="app-surface-tonal q-ma-md" role="alert">
            {{ apiErrorMessage(approvalDraftPreview.error) }}
          </q-banner>
          <pre v-else class="file-viewer__text q-pa-md">{{ approvalDraftPreview.content }}</pre>
        </q-scroll-area>
      </q-card>
    </q-dialog>
  </q-page>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useQuasar } from 'quasar'
import { useRoute, useRouter } from 'vue-router'
import ApprovalCard from 'components/ApprovalCard.vue'
import AgentActivityPanel from 'components/AgentActivityPanel.vue'
import AppLogo from 'components/AppLogo.vue'
import ChatComposer from 'components/ChatComposer.vue'
import ChatQueuedMessages from 'components/ChatQueuedMessages.vue'
import ControlCenterPanel from 'components/ControlCenterPanel.vue'
import MessageContent from 'components/MessageContent.vue'
import MessageAttachments from 'components/MessageAttachments.vue'
import QuestionDock from 'components/QuestionDock.vue'
import SubagentInspector from 'components/SubagentInspector.vue'
import SubagentRows from 'components/SubagentRows.vue'
import { useSubagentStore } from 'stores/subagents'
import { useAgentIdentityStore } from 'stores/agent-identity'
import { useAgentProfileStore } from 'stores/agent-profiles'
import { useApprovalStore } from 'stores/approvals'
import { useChatStore } from 'stores/chat'
import {
  MAX_CONTROL_CENTER_SPLIT_RATIO,
  MIN_CONTROL_CENTER_SPLIT_RATIO,
  useControlCenterStore
} from 'stores/control-center'
import { useExecutionAgentStore } from 'stores/execution-agents'
import { useIntegrationStore } from 'stores/integrations'
import { useMailStore } from 'stores/mail'
import { useProviderStore } from 'stores/providers'
import { useProjectStore } from 'stores/projects'
import { useQuestionStore } from 'stores/questions'
import { useToolSettingsStore } from 'stores/tool-settings'
import { useInstanceLimitsStore } from 'stores/instance-limits'
import { useWorkspaceStore } from 'stores/workspace'
import { useConfirm } from 'src/composables/confirm'
import { agentRunPresentation } from 'src/utils/agent-activity'
import {
  chatBottomDistance,
  chatReadingAnchor,
  chatShouldFollowBottom,
  restoreChatReadingAnchor,
  scrollChatToBottom
} from 'src/utils/chat-scroll'
import {
  buildChatTranscript,
  retryAvailableForMessage,
  withLiveTranscriptMessage
} from 'src/utils/chat-transcript'
import { apiErrorMessage } from 'src/utils/api-errors'
import { formatAbsoluteDateTime, formatRelativeDateTime } from 'src/utils/date-time'

defineOptions({ name: 'ChatPage' })

const route = useRoute()
const router = useRouter()
const $q = useQuasar()
const { confirm } = useConfirm()
const chat = useChatStore()
const subagents = useSubagentStore()
const identity = useAgentIdentityStore()
const agentProfiles = useAgentProfileStore()
const approvals = useApprovalStore()
const questions = useQuestionStore()
const executionAgents = useExecutionAgentStore()
const instanceLimits = useInstanceLimitsStore()
const integrations = useIntegrationStore()
const mail = useMailStore()
const providers = useProviderStore()
const projects = useProjectStore()
const toolSettings = useToolSettingsStore()
const controlCenter = useControlCenterStore()
const workspace = useWorkspaceStore()
const scrollArea = ref(null)
const scrollContent = ref(null)
const virtualScroll = ref(null)
const composer = ref(null)
const trustedImageOriginsBySession = ref(new Map())
const followingBottom = ref(true)
const messageTimestampNow = ref(Date.now())
const messageActionId = ref(null)
const approvalDraftPreview = ref(null)
const announcedRunStatus = ref('')
const bottomThreshold = 64
const liveWorkHistory = computed(() => ({
  entries: [],
  observedDurationMs: null,
  activeDurationMs: chat.activity.activeDurationMs,
  activeDurationUpdatedAt: chat.activity.activeDurationUpdatedAt
}))
const emptyProcessOutputs = {}
const userMessageLabel = (message) => (
  message.source?.type === 'endpoint' && message.source.provider === 'telegram'
    ? 'Du · Telegram'
    : 'Du'
)
const routeSessionId = computed(() => (
  typeof route.query.session === 'string' ? route.query.session : null
))
const routeSubagentId = computed(() => (
  typeof route.query.subagent === 'string' ? route.query.subagent : null
))
const routeProjectId = computed(() => (
  typeof route.query.project === 'string' ? route.query.project : null
))
const routeMessageId = computed(() => (
  typeof route.query.message === 'string' ? route.query.message : null
))
const highlightedMessageId = ref(null)
let resizeObserver = null
let scrollFrame = null
let scrollTarget = null
let lastScrollTop = 0
let userScrollActive = false
let userScrollTimer = null
let messageTimestampTimer = null
let runAnnouncementTimer = null
let touchActive = false
let virtualScrollRefreshFrame = null
let virtualScrollRefreshRequest = null
let virtualScrollRefreshPending = null
let searchHighlightTimer = null
let readingAnchor = null
let virtualScrollRefreshInProgress = false
let virtualScrollRefreshVersion = 0
const sessionApprovals = computed(() => chat.current ? approvals.forSession(chat.current.id) : [])
const sessionQuestions = computed(() => chat.current ? questions.forSession(chat.current.id) : [])
const activeQuestion = computed(() => sessionQuestions.value[0] || null)
const transcript = computed(() => buildChatTranscript(
  chat.current?.messages,
  sessionApprovals.value,
  {
    running: chat.running,
    runId: chat.runId || chat.current?.activeRun?.id,
    toolExecutionActive: chat.activity.phase === 'processing_tool',
    activeToolCallId: chat.activity.toolCallId,
    questions: sessionQuestions.value,
    contextSummaries: chat.current?.contextSummaries
  }
))
const hasCurrentAssistantTurn = computed(() => (
  transcript.value.messages.some((message) => message.currentTurn)
))
const childrenByMessage = computed(() => {
  const result = new Map()
  for (const message of transcript.value.messages) {
    const children = subagents.children.filter((child) => (message.workHistory?.entries || []).some((entry) => (
      entry.tool?.id === child.spawnedByToolCallId && entry.runId === child.spawnedByRunId
    )))
    if (children.length) result.set(message.id, children)
  }
  return result
})
const unmatchedChildren = computed(() => {
  const matched = new Set([...childrenByMessage.value.values()].flat().map((child) => child.id))
  return subagents.children.filter((child) => !matched.has(child.id))
})
const hasCurrentWorkHistory = computed(() => (
  transcript.value.messages.some((message) => message.currentTurn && message.workHistory)
))
const displayMessages = computed(() => {
  const messages = transcript.value.messages
  const liveHistory = chat.running && !hasCurrentWorkHistory.value
    ? liveWorkHistory.value
    : null
  const liveMessage = {
    id: `live-${chat.current?.id}`,
    role: 'assistant',
    content: chat.streamContent,
    currentTurn: true,
    modelName: chat.streamModelName,
    agentProfileId: chat.runAgentProfileId || chat.current?.activeRun?.agentProfileId || null,
    showAgentLabel: !hasCurrentAssistantTurn.value,
    streaming: Boolean(chat.streamContent),
    workHistory: liveHistory
  }
  return withLiveTranscriptMessage(messages, liveMessage)
})
const runPresentation = computed(() => agentRunPresentation({
  runId: chat.runId || chat.current?.activeRun?.id,
  running: chat.running,
  stopping: chat.stopping,
  activity: chat.activity,
  streamReasoning: chat.streamReasoning,
  streamContent: chat.streamContent,
  transcript: transcript.value
}))
const trustedImageOrigins = computed(() => (
  trustedImageOriginsBySession.value.get(chat.current?.id) || []
))
const answerDisplay = (agentProfileId) => (
  agentProfiles.byId(agentProfileId)?.answerDisplay === 'timeline' ? 'timeline' : 'compact'
)
const sessionLoadErrorMessage = computed(() => (
  apiErrorMessage(chat.loadError, 'Die Session konnte nicht geladen werden.')
))
const desktopControlCenterOpen = computed(() => controlCenter.open && $q.screen.gt.sm)
const splitLimits = computed(() => (
  desktopControlCenterOpen.value
    ? [MIN_CONTROL_CENTER_SPLIT_RATIO, MAX_CONTROL_CENTER_SPLIT_RATIO]
    : [100, 100]
))
const splitPosition = computed({
  get: () => desktopControlCenterOpen.value ? controlCenter.splitRatio : 100,
  set: (value) => {
    if (desktopControlCenterOpen.value) controlCenter.setSplitRatio(value)
  }
})

function resizeWorkspacePane(event) {
  let ratio = controlCenter.splitRatio
  if (event.key === 'ArrowLeft') ratio -= 2
  else if (event.key === 'ArrowRight') ratio += 2
  else if (event.key === 'Home') ratio = MIN_CONTROL_CENTER_SPLIT_RATIO
  else if (event.key === 'End') ratio = MAX_CONTROL_CENTER_SPLIT_RATIO
  else return
  event.preventDefault()
  controlCenter.setSplitRatio(Math.min(
    MAX_CONTROL_CENTER_SPLIT_RATIO,
    Math.max(MIN_CONTROL_CENTER_SPLIT_RATIO, ratio)
  ))
}

async function openToolFile(action) {
  const sessionId = action?.sessionId
  if (!sessionId || chat.current?.id !== sessionId) return
  const target = workspace.fileTargetFromAction(action)
  if (target) await controlCenter.openFile(target)
}

async function openApprovalDraft(approval) {
  const draft = approval?.details?.draftFile
  const sessionId = approval?.sessionId
  if (!['build_skill', 'edit_skill'].includes(approval?.actionKind) || !draft || chat.current?.id !== sessionId) return
  if (approval.payload?.subagentSessionId) {
    approvalDraftPreview.value = { filename: draft.filename, loading: true, content: '', error: null }
    const preview = approvalDraftPreview.value
    try {
      const data = await approvals.previewSkillDraft(approval)
      if (approvalDraftPreview.value !== preview) return
      if (data?.kind !== 'text' || typeof data.content !== 'string' || !draft.initialRevision) {
        preview.error = { code: 'skill.draftInvalid' }
      } else if (data.revision !== draft.initialRevision) {
        preview.error = { code: 'file.changed' }
      } else {
        preview.content = data.content
      }
    } catch (error) {
      if (approvalDraftPreview.value === preview) preview.error = error
    } finally {
      if (approvalDraftPreview.value === preview) preview.loading = false
    }
    return
  }
  const tabId = await controlCenter.openFile({
    scope: 'session',
    path: draft.path,
    name: draft.filename,
    rootIdentity: draft.rootIdentity || chat.current.projectId || sessionId,
    readOnly: false
  })
  if (tabId) workspace.startFileTabEdit(tabId)
}

function trustImageOrigin(origin) {
  const sessionId = chat.current?.id
  if (!sessionId || trustedImageOrigins.value.includes(origin)) return
  const next = new Map(trustedImageOriginsBySession.value)
  next.set(sessionId, [...trustedImageOrigins.value, origin])
  trustedImageOriginsBySession.value = next
}

function isMessageTrustLevel(value) {
  return value === 'public' || value === 'private'
}

function showMessageModel(message) {
  return message.role === 'assistant'
    && Boolean(message.hasAssistantContent || message.content)
    && Boolean(message.modelName)
}

function messageTrustLabel(value) {
  return value === 'private' ? 'Privat' : 'Öffentlich'
}

function canRetryMessage(message) {
  return retryAvailableForMessage(message, chat.current?.messages, chat.running)
}

async function undoMessage(message) {
  if (composer.value?.hasAudioActivity?.()) {
    $q.notify({
      type: 'info',
      message: 'Beende oder brich zuerst die laufende Aufnahme beziehungsweise Transkription ab.'
    })
    return
  }
  const sessionId = chat.current?.id
  const runningHint = chat.running ? ' Die laufende Anfrage wird dabei abgebrochen.' : ''
  const firstMessage = chat.current?.messages?.[0]?.id === message.id
  const hasNewSessionDraft = Boolean(
    chat.drafts.newSession.message.trim() || chat.attachmentDrafts.newSession.length
  )
  const newSessionHint = firstMessage && hasNewSessionDraft
    ? ' Der vorhandene Entwurf für eine neue Session wird ebenfalls ersetzt.'
    : ''
  const accepted = await confirm(
    `Diese Nachricht und alle späteren Chatnachrichten werden entfernt.${runningHint} `
      + `Der aktuelle Entwurf, seine Anhänge und ein vorhandenes Diktat werden ersetzt.${newSessionHint} `
      + 'Bereits ausgeführte Tool-, Datei-, Berechtigungs- und Memory-Aktionen bleiben bestehen.',
    'Nachricht rückgängig machen?',
    { okLabel: 'Rückgängig machen' }
  )
  if (!accepted) return
  messageActionId.value = message.id
  try {
    const result = await chat.undoUserMessage(message)
    if (result?.sessionDeleted && routeSessionId.value === sessionId) {
      if (routeProjectId.value) void projects.fetchAll().catch(() => {})
      await router.replace({
        name: 'chat',
        query: routeProjectId.value ? { project: routeProjectId.value } : {}
      })
    }
    await nextTick()
    composer.value?.focus()
  } finally {
    messageActionId.value = null
  }
}

async function undoQueuedMessage(id) {
  if (composer.value?.hasAudioActivity?.()) {
    $q.notify({
      type: 'info',
      message: 'Beende oder brich zuerst die laufende Aufnahme beziehungsweise Transkription ab.'
    })
    return
  }
  const hasDraft = Boolean(
    chat.activeDraft.trim()
    || chat.activeAttachmentDraft.length
    || chat.getAudioDictation(chat.current?.id || null)
  )
  if (hasDraft && !await confirm(
    'Der aktuelle Entwurf, seine Anhänge und ein vorhandenes Diktat werden ersetzt.',
    'Vorgemerkte Nachricht rückgängig machen?',
    { okLabel: 'Rückgängig machen' }
  )) return
  const restored = await chat.restoreQueuedMessage(id)
  if (!restored) return
  await nextTick()
  composer.value?.focus()
}

async function retryMessage(message) {
  const accepted = await confirm(
    'Die fehlerhafte Antwort wird entfernt und mit dem ursprünglichen Modell erneut erzeugt. '
      + 'Bereits ausgeführte Tool-, Datei-, Berechtigungs- und Memory-Aktionen können erneut ausgeführt werden.',
    'Antwort erneut versuchen?',
    { okLabel: 'Erneut versuchen', okColor: 'primary' }
  )
  if (!accepted) return
  const sessionId = chat.current?.id
  enableFollowMode()
  messageActionId.value = message.id
  try {
    const result = await chat.retryFailedMessage(message)
    if (!result || chat.current?.id !== sessionId) return
    await nextTick()
    scheduleFollowScroll()
  } finally {
    messageActionId.value = null
  }
}

async function loadRouteSession() {
  const sessionId = routeSessionId.value
  await composer.value?.prepareAudioNavigation?.()
  if (routeSessionId.value !== sessionId) return
  const session = await chat.loadSession(sessionId).catch(() => null)
  if (routeSessionId.value !== sessionId) return
  if (session?.readOnly && session.parentSessionId) {
    await router.replace({ name: 'chat', query: { session: session.parentSessionId, subagent: session.id } })
    return
  }
  if (chat.selectedSessionId !== sessionId || chat.loadError) return
  await reconcileRouteProject()
  const requestedApp = typeof route.query.controlCenter === 'string'
    ? route.query.controlCenter
    : null
  if (requestedApp) {
    try {
      await controlCenter.openApp(requestedApp)
    } catch {
      // The central API interceptor displays the error and restores the previous tabs.
    }
  }
  await nextTick()
  if (routeMessageId.value) await revealRouteMessage()
  else composer.value?.focus()
}

async function reconcileRouteProject() {
  if (
    !chat.current
    || chat.current.id !== routeSessionId.value
    || (chat.current.projectId || null) === routeProjectId.value
  ) return
  await router.replace({
    name: 'chat',
    query: {
      ...(chat.current.projectId ? { project: chat.current.projectId } : {}),
      session: chat.current.id,
      ...(routeSubagentId.value ? { subagent: routeSubagentId.value } : {}),
      ...(typeof route.query.controlCenter === 'string'
        ? { controlCenter: route.query.controlCenter }
        : {}),
      ...(routeMessageId.value ? { message: routeMessageId.value } : {})
    }
  })
}

async function revealRouteMessage() {
  const messageId = routeMessageId.value
  if (!messageId || chat.current?.id !== routeSessionId.value) return
  let index = transcript.value.messages.findIndex((message) => message.id === messageId)
  if (index < 0 && chat.current.nextMessageCursor) {
    try {
      await chat.loadThroughMessage(messageId)
    } catch {
      return
    }
    await nextTick()
    index = transcript.value.messages.findIndex((message) => message.id === messageId)
  }
  if (index < 0) return
  followingBottom.value = false
  cancelFollowScroll()
  highlightedMessageId.value = messageId
  await nextTick()
  virtualScroll.value?.scrollTo(index, 'center-force')
  await new Promise((resolve) => window.requestAnimationFrame(resolve))
  const target = findMessageElement(messageId)
  target?.focus({ preventScroll: true })
  rememberReadingAnchor()
  clearTimeout(searchHighlightTimer)
  searchHighlightTimer = window.setTimeout(() => {
    if (highlightedMessageId.value === messageId) highlightedMessageId.value = null
  }, 2500)
}

async function loadOlderHistory() {
  if (!chat.current?.nextMessageCursor || chat.olderMessagesLoading) return
  try {
    await chat.loadOlderMessages()
  } catch {
    // The store exposes the retry state above the transcript.
  }
}

function handleVirtualScroll({ from, direction }) {
  if (virtualScrollRefreshInProgress) {
    void completeVirtualScrollRefresh()
    return
  }
  if (!followingBottom.value) rememberReadingAnchor()
  if (
    userScrollActive
    && direction === 'decrease'
    && from <= 3
    && !followingBottom.value
    && chat.current?.nextMessageCursor
  ) void loadOlderHistory()
}

async function loadBackgroundData() {
  const [providerResult, integrationResult, , , , defaultsResult] = await Promise.allSettled([
    providers.fetchEffective(),
    integrations.fetchEffectiveSearch(),
    mail.fetchAccounts(),
    approvals.fetchPending(),
    questions.fetchPending(),
    chat.fetchDefaults(),
    toolSettings.fetchAvailability(),
    instanceLimits.fetchSettings(),
    agentProfiles.fetchAgents()
  ])
  if (
    providerResult.status === 'fulfilled'
    && providers.lastLoadComplete
    && integrationResult.status === 'fulfilled'
    && defaultsResult.status === 'fulfilled'
  ) {
    chat.reconcileNewSession(
      providers.selectionOptions(null, true)
        .filter((option) => !option.disable)
        .map((option) => option.value),
      integrations.search.effectiveEnabled
    )
  }
}

async function send(content) {
  const sessionId = chat.current?.id
  enableFollowMode()
  const request = chat.send(content)
  try {
    const result = await request
    if (!result || !sessionId || chat.current?.id !== sessionId) return
    await nextTick()
    scheduleFollowScroll()
  } finally {
    await nextTick()
    composer.value?.focus()
  }
}

async function flushQueuedMessage() {
  if (!chat.queuedSendReady) return
  const request = chat.drainOutbox()
  await nextTick()
  scheduleFollowScroll()
  await request
}

function addAttachmentFiles(files, done, options) {
  const result = chat.addAttachmentFiles(files, options)
  if (result.error) $q.notify({ type: 'negative', message: result.error })
  done?.(result)
}

function scheduleVirtualScrollRefresh(preservePosition = true) {
  if (!virtualScroll.value || transcript.value.messages.length === 0) return
  const request = {
    anchor: preservePosition && !followingBottom.value
      ? readingAnchor || captureReadingAnchor()
      : null,
    revealing: false,
    version: ++virtualScrollRefreshVersion
  }
  if (userScrollActive || virtualScrollRefreshInProgress) {
    virtualScrollRefreshPending = request
    return
  }
  startVirtualScrollRefresh(request)
}

function startVirtualScrollRefresh(request) {
  virtualScrollRefreshRequest = request
  virtualScrollRefreshInProgress = true
  virtualScrollRefreshFrame = window.requestAnimationFrame(() => {
    virtualScrollRefreshFrame = null
    if (virtualScrollRefreshRequest !== request) return
    virtualScroll.value?.refresh(-1)
  })
}

async function completeVirtualScrollRefresh() {
  const request = virtualScrollRefreshRequest
  if (!request) return
  await nextTick()
  await new Promise((resolve) => window.requestAnimationFrame(resolve))
  if (request !== virtualScrollRefreshRequest) return
  if (
    request.anchor
    && !userScrollActive
    && !followingBottom.value
    && request.version === virtualScrollRefreshVersion
  ) {
    const element = findMessageElement(request.anchor.id)
    if (!element && !request.revealing) {
      const index = transcript.value.messages.findIndex((message) => message.id === request.anchor.id)
      if (index >= 0) {
        request.revealing = true
        virtualScroll.value?.scrollTo(index, 'start-force')
        return
      }
    }
    restoreChatReadingAnchor(scrollTarget, element, request.anchor)
    readingAnchor = {
      ...request.anchor,
      id: element?.dataset.messageId || request.anchor.id
    }
  }
  finishVirtualScrollRefresh(request)
}

function finishVirtualScrollRefresh(request) {
  if (request !== virtualScrollRefreshRequest) return
  virtualScrollRefreshRequest = null
  virtualScrollRefreshInProgress = false
  if (userScrollActive || !virtualScrollRefreshPending) return
  const pending = virtualScrollRefreshPending
  virtualScrollRefreshPending = null
  startVirtualScrollRefresh(pending)
}

function handleMessageRendered() {
  if (followingBottom.value) {
    scheduleVirtualScrollRefresh(false)
    scheduleFollowScroll()
    return
  }
  scheduleVirtualScrollRefresh()
}

function renderedMessageElements() {
  return [...(scrollContent.value?.querySelectorAll('[data-message-id]') || [])]
}

function findMessageElement(messageId) {
  return renderedMessageElements().find((element) => (
    element.dataset.messageId === messageId || element.dataset.liveMessageId === messageId
  ))
}

function captureReadingAnchor() {
  return chatReadingAnchor(scrollTarget, renderedMessageElements())
}

function rememberReadingAnchor() {
  if (followingBottom.value || virtualScrollRefreshInProgress) return
  readingAnchor = captureReadingAnchor() || readingAnchor
}

function clearReadingAnchor() {
  readingAnchor = null
  virtualScrollRefreshVersion += 1
  if (virtualScrollRefreshRequest) virtualScrollRefreshRequest.anchor = null
  if (virtualScrollRefreshPending) virtualScrollRefreshPending.anchor = null
}

function beginActivityResize() {
  if (!chat.current) return
  followingBottom.value = false
  clearReadingAnchor()
  cancelFollowScroll()
}

function finishActivityResize() {
  scheduleVirtualScrollRefresh(false)
}

function updateFollowState() {
  if (!scrollTarget) return
  const scrollTop = scrollTarget.scrollTop
  const movedUp = scrollTop < lastScrollTop
  lastScrollTop = scrollTop
  if (virtualScrollRefreshInProgress) return
  const atBottom = chatBottomDistance(scrollTarget) <= bottomThreshold
  const shouldFollow = chatShouldFollowBottom(
    followingBottom.value,
    userScrollActive && movedUp,
    userScrollActive && atBottom
  )
  if (shouldFollow && !followingBottom.value) {
    followingBottom.value = true
    clearReadingAnchor()
  }
  else if (!shouldFollow && followingBottom.value) {
    followingBottom.value = false
    cancelFollowScroll()
  }
  if (!followingBottom.value) rememberReadingAnchor()
  if (userScrollActive && !touchActive) scheduleUserScrollEnd()
}

function cancelFollowScroll() {
  if (scrollFrame === null) return
  window.cancelAnimationFrame(scrollFrame)
  scrollFrame = null
}

function beginUserScroll() {
  if (virtualScrollRefreshInProgress) clearReadingAnchor()
  userScrollActive = true
  clearTimeout(userScrollTimer)
  userScrollTimer = null
  cancelFollowScroll()
}

function scheduleUserScrollEnd() {
  clearTimeout(userScrollTimer)
  userScrollTimer = setTimeout(() => {
    userScrollTimer = null
    userScrollActive = false
    if (!virtualScrollRefreshInProgress && virtualScrollRefreshPending) {
      const pending = virtualScrollRefreshPending
      virtualScrollRefreshPending = null
      startVirtualScrollRefresh(pending)
    }
    scheduleFollowScroll()
  }, 180)
}

function handleTouchStart() {
  touchActive = true
}

function handleTouchMove() {
  if (!userScrollActive) beginUserScroll()
}

function handleTouchEnd() {
  touchActive = false
  if (userScrollActive) scheduleUserScrollEnd()
}

function handleWheel(event) {
  if (!event.deltaY) return
  beginUserScroll()
  scheduleUserScrollEnd()
}

function handleScrollKey(event) {
  if (
    event.defaultPrevented
    || event.altKey
    || event.ctrlKey
    || event.metaKey
    || !['ArrowUp', 'ArrowDown', 'PageUp', 'PageDown', 'Home', 'End', ' '].includes(event.key)
  ) return
  beginUserScroll()
  scheduleUserScrollEnd()
}

function handleScrollAreaMouseDown(event) {
  if (event.button !== 0 || !scrollTarget || scrollTarget.contains(event.target)) return
  beginUserScroll()
  window.removeEventListener('mouseup', handleScrollAreaMouseUp)
  window.addEventListener('mouseup', handleScrollAreaMouseUp, { once: true })
}

function handleScrollAreaMouseUp() {
  scheduleUserScrollEnd()
}

function enableFollowMode() {
  touchActive = false
  userScrollActive = false
  clearTimeout(userScrollTimer)
  userScrollTimer = null
  followingBottom.value = true
  clearReadingAnchor()
  cancelFollowScroll()
  scheduleBottomScroll()
}

function followScrollNow() {
  if (followingBottom.value && !userScrollActive && scrollTarget) {
    scrollChatToBottom(scrollTarget)
  }
}

function scheduleFollowScroll() {
  if (!followingBottom.value) return
  scheduleBottomScroll()
}

function scheduleBottomScroll() {
  if (userScrollActive || scrollFrame !== null) return
  scrollFrame = window.requestAnimationFrame(() => {
    scrollFrame = null
    if (scrollTarget) scrollChatToBottom(scrollTarget)
  })
}

function observeChatSize() {
  scrollTarget = scrollArea.value?.getScrollTarget?.() || null
  if (scrollTarget) {
    lastScrollTop = scrollTarget.scrollTop
    scrollTarget.addEventListener('scroll', updateFollowState, { passive: true })
    scrollTarget.addEventListener('touchstart', handleTouchStart, { passive: true })
    scrollTarget.addEventListener('touchmove', handleTouchMove, { passive: true })
    scrollTarget.addEventListener('touchend', handleTouchEnd, { passive: true })
    scrollTarget.addEventListener('touchcancel', handleTouchEnd, { passive: true })
    scrollTarget.addEventListener('wheel', handleWheel, { passive: true })
  }
  if (typeof ResizeObserver === 'undefined') return
  resizeObserver = new ResizeObserver(() => scheduleFollowScroll())
  if (scrollContent.value) resizeObserver.observe(scrollContent.value)
  if (scrollTarget) resizeObserver.observe(scrollTarget)
}

function chatPageStyle(offset, height) {
  return {
    minHeight: '0px',
    height: `calc(var(--app-visual-viewport-height, ${height}px) - ${offset}px)`,
    transform: 'translateY(var(--app-visual-viewport-offset-top, 0px))'
  }
}

watch(routeSessionId, (id) => {
  approvalDraftPreview.value = null
  void subagents.loadChildren(id)
  void loadRouteSession()
}, { immediate: true })
watch(routeProjectId, () => void reconcileRouteProject())
watch(routeMessageId, () => void revealRouteMessage())
watch(
  () => transcript.value.messages,
  () => {
    if (!followingBottom.value) scheduleVirtualScrollRefresh()
  },
  { flush: 'pre' }
)
watch(
  () => Boolean(chat.streamContent),
  (streaming) => {
    if (!followingBottom.value || userScrollActive) return
    cancelFollowScroll()
    if (streaming) {
      followScrollNow()
      return
    }
    scheduleFollowScroll()
  },
  { flush: 'post' }
)
watch(
  () => runPresentation.value.announcement,
  (value) => {
    clearTimeout(runAnnouncementTimer)
    if (!value) {
      announcedRunStatus.value = ''
      return
    }
    runAnnouncementTimer = window.setTimeout(() => {
      announcedRunStatus.value = value
    }, 250)
  },
  { immediate: true }
)
watch(
  () => chat.current?.id,
  async (sessionId, previousSessionId) => {
    if (!sessionId || sessionId === previousSessionId) return
    followingBottom.value = true
    clearReadingAnchor()
    cancelFollowScroll()
    if (routeMessageId.value) {
      followingBottom.value = false
      return
    }
    await nextTick()
    scheduleFollowScroll()
  }
)
watch(
  [activeQuestion, () => chat.running],
  async ([question, running], [previousQuestion, wasRunning]) => {
    if (question || running || (!previousQuestion && !wasRunning)) return
    await nextTick()
    composer.value?.focus()
  }
)
watch(
  () => chat.queuedSendReady,
  (ready) => {
    if (ready) void flushQueuedMessage()
  },
  { immediate: true }
)
watch(
  () => chat.createdSessionResult,
  async (result) => {
    if (!result) return
    chat.createdSessionResult = null
    if (routeProjectId.value) void projects.fetchAll().catch(() => {})
    await router.replace({
      name: 'chat',
      query: {
        ...(routeProjectId.value ? { project: routeProjectId.value } : {}),
        session: result.sessionId
      }
    })
  }
)
onMounted(() => {
  observeChatSize()
  messageTimestampTimer = window.setInterval(() => {
    messageTimestampNow.value = Date.now()
  }, 60_000)
  void loadBackgroundData()
  executionAgents.startPolling()
})
onBeforeUnmount(() => {
  approvalDraftPreview.value = null
  void subagents.loadChildren(null)
  virtualScrollRefreshVersion += 1
  resizeObserver?.disconnect()
  scrollTarget?.removeEventListener('scroll', updateFollowState)
  scrollTarget?.removeEventListener('touchstart', handleTouchStart)
  scrollTarget?.removeEventListener('touchmove', handleTouchMove)
  scrollTarget?.removeEventListener('touchend', handleTouchEnd)
  scrollTarget?.removeEventListener('touchcancel', handleTouchEnd)
  scrollTarget?.removeEventListener('wheel', handleWheel)
  window.clearInterval(messageTimestampTimer)
  executionAgents.stopPolling()
  clearTimeout(runAnnouncementTimer)
  clearTimeout(userScrollTimer)
  clearTimeout(searchHighlightTimer)
  window.removeEventListener('mouseup', handleScrollAreaMouseUp)
  if (virtualScrollRefreshFrame !== null) window.cancelAnimationFrame(virtualScrollRefreshFrame)
  cancelFollowScroll()
})
</script>
