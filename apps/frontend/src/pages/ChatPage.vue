<template>
  <q-page :style-fn="chatPageStyle" class="chat-page column no-wrap">
    <q-scroll-area
      ref="scrollArea"
      class="chat-page__content col"
      :aria-busy="chat.loading ? 'true' : 'false'"
    >
      <div ref="scrollContent">
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
          <AgentActivity
            v-if="runPresentation.showActivity"
            :activity="chat.activity"
            class="chat-welcome__activity text-left q-mx-auto"
          />
        </div>

        <div v-else class="chat-transcript q-pa-md">
          <div
            v-for="message in transcript.messages"
            :key="message.id"
            :class="['chat-message', `chat-message--${message.role}`]"
          >
            <template v-if="message.kind === 'runtime_error' && message.error">
              <div class="chat-message__label">{{ identity.displayName }}</div>
              <q-banner rounded class="chat-runtime-error app-surface-tonal" role="alert">
                <template #avatar>
                  <q-icon name="error_outline" color="negative" />
                </template>
                {{ apiErrorMessage(message.error) }}
              </q-banner>
            </template>
            <template
              v-else-if="message.content || message.reasoning || message.generationStatus === 'interrupted'"
            >
              <div class="chat-message__label">
                <span>{{ message.role === 'user' ? 'Du' : identity.displayName }}</span>
              </div>
              <ReasoningPanel
                v-if="message.reasoning"
                :reasoning="message.reasoning"
                :expanded="auth.preferences.thinkingExpanded"
                class="q-mb-md"
                @update:expanded="updateThinkingExpanded"
              />
              <MessageContent
                v-if="message.content && message.role === 'assistant'"
                class="chat-message__content"
                :content="message.content"
                :research-sources="message.research?.linkedSources || []"
                :trusted-image-origins="trustedImageOrigins"
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
              v-if="message.toolCalls.length"
              class="tool-timeline"
              :class="{ 'q-mt-md': message.content }"
              aria-label="Tool-Aktivitäten"
            >
              <ToolCallItem
                v-for="item in message.toolCalls"
                :key="item.tool.id"
                v-bind="item"
              />
            </div>
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
                || (message.role === 'assistant' && message.modelName)
                || isMessageTrustLevel(message.trustLevel)"
              class="chat-message__meta items-center"
            >
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
                  && ((message.role === 'assistant' && message.modelName) || message.createdAt)"
                aria-hidden="true"
              >·</span>
              <span
                v-if="message.role === 'assistant' && message.modelName"
                class="chat-message__meta-model"
                :title="message.modelName"
              >
                {{ message.modelName }}
              </span>
              <span
                v-if="message.createdAt && message.role === 'assistant' && message.modelName"
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
          <div
            v-if="chat.streamContent || chat.streamReasoning"
            class="chat-message chat-message--assistant"
          >
            <div class="chat-message__label">
              <span>{{ identity.displayName }}</span>
            </div>
            <ReasoningPanel
              v-if="chat.streamReasoning"
              :reasoning="chat.streamReasoning"
              :activity="chat.activity"
              :running="chat.running"
              :expanded="auth.preferences.thinkingExpanded"
              class="q-mb-md"
              @update:expanded="updateThinkingExpanded"
            />
            <MessageContent
              v-if="chat.streamContent"
              class="chat-message__content"
              :content="chat.streamContent"
              :trusted-image-origins="trustedImageOrigins"
              streaming
              @trust-image-origin="trustImageOrigin"
            />
            <div v-if="chat.streamModelName" class="chat-message__meta items-center">
              <span class="chat-message__meta-model" :title="chat.streamModelName">
                {{ chat.streamModelName }}
              </span>
            </div>
          </div>
          <ApprovalCard
            v-for="approval in transcript.unmatchedApprovals"
            :key="approval.id"
            :approval="approval"
            class="q-mt-md"
          />
          <AgentActivity v-if="runPresentation.showActivity" :activity="chat.activity" />
        </div>
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

    <div v-else-if="!chat.loadError" class="chat-composer-region">
      <transition name="chat-scroll-jump">
        <div v-if="chat.current && !followingBottom" class="chat-scroll-to-bottom">
          <q-btn
            round
            unelevated
            icon="keyboard_arrow_down"
            class="chat-scroll-to-bottom__button"
            aria-label="Zum neuesten Inhalt springen"
            @click="scrollToBottom"
          >
            <q-tooltip>Zum neuesten Inhalt springen</q-tooltip>
          </q-btn>
        </div>
      </transition>

      <ChatComposer
        ref="composer"
        :session="chat.activeContext"
        :draft="chat.activeDraft"
        :disabled="chat.historyActionPending"
        :running="chat.running"
        :stopping="chat.stopping"
        :pending-count="sessionApprovals.length"
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
  </q-page>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useQuasar } from 'quasar'
import { useRoute, useRouter } from 'vue-router'
import ApprovalCard from 'components/ApprovalCard.vue'
import AgentActivity from 'components/AgentActivity.vue'
import AppLogo from 'components/AppLogo.vue'
import ChatComposer from 'components/ChatComposer.vue'
import MessageContent from 'components/MessageContent.vue'
import MessageAttachments from 'components/MessageAttachments.vue'
import ReasoningPanel from 'components/ReasoningPanel.vue'
import ToolCallItem from 'components/ToolCallItem.vue'
import { useAgentIdentityStore } from 'stores/agent-identity'
import { useApprovalStore } from 'stores/approvals'
import { useChatStore } from 'stores/chat'
import { useAuthStore } from 'stores/auth'
import { useIntegrationStore } from 'stores/integrations'
import { useProviderStore } from 'stores/providers'
import { useToolSettingsStore } from 'stores/tool-settings'
import { useConfirm } from 'src/composables/confirm'
import { agentRunPresentation } from 'src/utils/agent-activity'
import { chatBottomDistance, scrollChatToBottom } from 'src/utils/chat-scroll'
import { buildChatTranscript } from 'src/utils/chat-transcript'
import { apiErrorMessage } from 'src/utils/api-errors'
import { formatAbsoluteDateTime, formatRelativeDateTime } from 'src/utils/date-time'

defineOptions({ name: 'ChatPage' })

const route = useRoute()
const router = useRouter()
const $q = useQuasar()
const { confirm } = useConfirm()
const chat = useChatStore()
const identity = useAgentIdentityStore()
const auth = useAuthStore()
const approvals = useApprovalStore()
const integrations = useIntegrationStore()
const providers = useProviderStore()
const toolSettings = useToolSettingsStore()
const scrollArea = ref(null)
const scrollContent = ref(null)
const composer = ref(null)
const trustedImageOriginsBySession = ref(new Map())
const followingBottom = ref(true)
const messageTimestampNow = ref(Date.now())
const messageActionId = ref(null)
const announcedRunStatus = ref('')
const bottomThreshold = 64
const routeSessionId = computed(() => (
  typeof route.query.session === 'string' ? route.query.session : null
))
let resizeObserver = null
let scrollFrame = null
let scrollTarget = null
let userScrollActive = false
let userScrollTimer = null
let messageTimestampTimer = null
let runAnnouncementTimer = null
let touchActive = false
const sessionApprovals = computed(() => chat.current ? approvals.forSession(chat.current.id) : [])
const transcript = computed(() => buildChatTranscript(
  chat.current?.messages,
  sessionApprovals.value,
  {
    running: chat.running,
    toolExecutionActive: chat.activity.phase === 'processing_tool',
    activeToolCallId: chat.activity.toolCallId
  }
))
const runPresentation = computed(() => agentRunPresentation({
  running: chat.running,
  activity: chat.activity,
  streamReasoning: chat.streamReasoning,
  streamContent: chat.streamContent,
  transcript: transcript.value
}))
const trustedImageOrigins = computed(() => (
  trustedImageOriginsBySession.value.get(chat.current?.id) || []
))
const sessionLoadErrorMessage = computed(() => (
  apiErrorMessage(chat.loadError, 'Die Session konnte nicht geladen werden.')
))

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

function messageTrustLabel(value) {
  return value === 'private' ? 'Privat' : 'Öffentlich'
}

function canRetryMessage(message) {
  const messages = transcript.value.messages
  return message.kind === 'runtime_error'
    && messages[messages.length - 1]?.id === message.id
    && !chat.running
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
      await router.replace({ name: 'chat' })
    }
    await nextTick()
    composer.value?.focus()
  } finally {
    messageActionId.value = null
  }
}

async function retryMessage(message) {
  const accepted = await confirm(
    'Die fehlerhafte Antwort wird entfernt und mit dem ursprünglichen Modell erneut erzeugt. '
      + 'Bereits ausgeführte Tool-, Datei-, Berechtigungs- und Memory-Aktionen können erneut ausgeführt werden.',
    'Antwort erneut versuchen?',
    { okLabel: 'Erneut versuchen', okColor: 'primary' }
  )
  if (!accepted) return
  messageActionId.value = message.id
  followingBottom.value = true
  try {
    await chat.retryFailedMessage(message)
    await nextTick()
    scheduleFollowScroll()
  } finally {
    messageActionId.value = null
  }
}

async function loadRouteSession() {
  const sessionId = routeSessionId.value
  const request = chat.loadSession(sessionId).catch(() => null)
  await composer.value?.cancelAudioActivity?.()
  await request
  if (chat.selectedSessionId !== sessionId || chat.loadError) return
  await nextTick()
  composer.value?.focus()
}

async function loadBackgroundData() {
  const [providerResult, integrationResult, , defaultsResult] = await Promise.allSettled([
    providers.fetchAll(),
    integrations.fetchSearch(),
    approvals.fetchPending(),
    chat.fetchDefaults(),
    toolSettings.fetchSettings()
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
  followingBottom.value = true
  const request = chat.send(content)
  await nextTick()
  scheduleFollowScroll()
  const result = await request
  if (result?.created) {
    await router.replace({ name: 'chat', query: { session: result.sessionId } })
  }
}

function addAttachmentFiles(files, done) {
  const result = chat.addAttachmentFiles(files)
  if (result.error) $q.notify({ type: 'negative', message: result.error })
  done?.(result)
}

function updateThinkingExpanded(expanded) {
  auth.setThinkingExpanded(expanded).catch(() => {})
}

function updateFollowState() {
  if (!scrollTarget) return
  const atBottom = chatBottomDistance(scrollTarget) <= bottomThreshold
  if (atBottom) followingBottom.value = true
  else if (userScrollActive) {
    followingBottom.value = false
    cancelFollowScroll()
  }
  if (userScrollActive && !touchActive) scheduleUserScrollEnd()
}

function cancelFollowScroll() {
  if (scrollFrame === null) return
  window.cancelAnimationFrame(scrollFrame)
  scrollFrame = null
}

function beginUserScroll() {
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
    scheduleFollowScroll()
  }, 180)
}

function handleTouchStart() {
  touchActive = true
  beginUserScroll()
}

function handleTouchEnd() {
  touchActive = false
  scheduleUserScrollEnd()
}

function handleWheel() {
  beginUserScroll()
  scheduleUserScrollEnd()
}

function scrollToBottom() {
  touchActive = false
  userScrollActive = false
  clearTimeout(userScrollTimer)
  userScrollTimer = null
  followingBottom.value = true
  cancelFollowScroll()
  scheduleFollowScroll()
}

function followScrollNow() {
  if (followingBottom.value && !userScrollActive && scrollTarget) {
    scrollChatToBottom(scrollTarget)
  }
}

function scheduleFollowScroll() {
  if (!followingBottom.value || userScrollActive || scrollFrame !== null) return
  scrollFrame = window.requestAnimationFrame(() => {
    scrollFrame = null
    followScrollNow()
  })
}

function observeChatSize() {
  scrollTarget = scrollArea.value?.getScrollTarget?.() || null
  if (scrollTarget) {
    scrollTarget.addEventListener('scroll', updateFollowState, { passive: true })
    scrollTarget.addEventListener('touchstart', handleTouchStart, { passive: true })
    scrollTarget.addEventListener('touchend', handleTouchEnd, { passive: true })
    scrollTarget.addEventListener('touchcancel', handleTouchEnd, { passive: true })
    scrollTarget.addEventListener('wheel', handleWheel, { passive: true })
  }
  if (typeof ResizeObserver === 'undefined') return
  resizeObserver = new ResizeObserver(followScrollNow)
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

watch(routeSessionId, () => void loadRouteSession(), { immediate: true })
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
    await nextTick()
    scheduleFollowScroll()
  }
)
onMounted(() => {
  observeChatSize()
  messageTimestampTimer = window.setInterval(() => {
    messageTimestampNow.value = Date.now()
  }, 60_000)
  void loadBackgroundData()
})
onBeforeUnmount(() => {
  resizeObserver?.disconnect()
  scrollTarget?.removeEventListener('scroll', updateFollowState)
  scrollTarget?.removeEventListener('touchstart', handleTouchStart)
  scrollTarget?.removeEventListener('touchend', handleTouchEnd)
  scrollTarget?.removeEventListener('touchcancel', handleTouchEnd)
  scrollTarget?.removeEventListener('wheel', handleWheel)
  window.clearInterval(messageTimestampTimer)
  clearTimeout(runAnnouncementTimer)
  clearTimeout(userScrollTimer)
  cancelFollowScroll()
})
</script>
