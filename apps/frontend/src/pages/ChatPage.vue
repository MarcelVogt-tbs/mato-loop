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
            v-if="chat.running"
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
              <div class="chat-message__label">MatoLoop</div>
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
                <span>{{ message.role === 'user' ? 'Du' : 'MatoLoop' }}</span>
                <span
                  v-if="message.role === 'assistant' && message.modelName"
                  class="chat-message__model"
                >
                  · {{ message.modelName }}
                </span>
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
          </div>
          <div
            v-if="chat.streamContent || chat.streamReasoning"
            class="chat-message chat-message--assistant"
          >
            <div class="chat-message__label">
              <span>MatoLoop</span>
              <span v-if="chat.streamModelName" class="chat-message__model">
                · {{ chat.streamModelName }}
              </span>
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
          </div>
          <ApprovalCard
            v-for="approval in transcript.unmatchedApprovals"
            :key="approval.id"
            :approval="approval"
            class="q-mt-md"
          />
          <AgentActivity v-if="chat.running && !chat.streamReasoning" :activity="chat.activity" />
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
import { useApprovalStore } from 'stores/approvals'
import { useChatStore } from 'stores/chat'
import { useAuthStore } from 'stores/auth'
import { useIntegrationStore } from 'stores/integrations'
import { useProviderStore } from 'stores/providers'
import { useToolSettingsStore } from 'stores/tool-settings'
import { chatBottomDistance, scrollChatToBottom } from 'src/utils/chat-scroll'
import { buildChatTranscript } from 'src/utils/chat-transcript'
import { apiErrorMessage } from 'src/utils/api-errors'

defineOptions({ name: 'ChatPage' })

const route = useRoute()
const router = useRouter()
const $q = useQuasar()
const chat = useChatStore()
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
const visualViewportHeight = ref(null)
const bottomThreshold = 64
const routeSessionId = computed(() => (
  typeof route.query.session === 'string' ? route.query.session : null
))
let resizeObserver = null
let scrollFrame = null
let scrollTarget = null
let userScrollActive = false
let userScrollTimer = null
let touchActive = false
const sessionApprovals = computed(() => chat.current ? approvals.forSession(chat.current.id) : [])
const transcript = computed(() => buildChatTranscript(
  chat.current?.messages,
  sessionApprovals.value,
  chat.running
))
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
      providers.selectionOptions(null)
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

function updateVisualViewport() {
  const height = window.visualViewport?.height || window.innerHeight
  if (Number.isFinite(height) && height > 0) visualViewportHeight.value = height
  scheduleFollowScroll()
}

function chatPageStyle(offset, height) {
  const viewportHeight = visualViewportHeight.value || height
  return {
    minHeight: '0px',
    height: `${Math.max(0, viewportHeight - offset)}px`
  }
}

watch(routeSessionId, () => void loadRouteSession(), { immediate: true })
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
  updateVisualViewport()
  window.visualViewport?.addEventListener('resize', updateVisualViewport)
  window.visualViewport?.addEventListener('scroll', updateVisualViewport)
  window.addEventListener('resize', updateVisualViewport)
  void loadBackgroundData()
})
onBeforeUnmount(() => {
  resizeObserver?.disconnect()
  scrollTarget?.removeEventListener('scroll', updateFollowState)
  scrollTarget?.removeEventListener('touchstart', handleTouchStart)
  scrollTarget?.removeEventListener('touchend', handleTouchEnd)
  scrollTarget?.removeEventListener('touchcancel', handleTouchEnd)
  scrollTarget?.removeEventListener('wheel', handleWheel)
  window.visualViewport?.removeEventListener('resize', updateVisualViewport)
  window.visualViewport?.removeEventListener('scroll', updateVisualViewport)
  window.removeEventListener('resize', updateVisualViewport)
  clearTimeout(userScrollTimer)
  cancelFollowScroll()
})
</script>
