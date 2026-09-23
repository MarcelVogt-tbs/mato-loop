<template>
  <section
    class="subagent-inspector chat-page__pane column no-wrap full-height"
    aria-labelledby="subagent-inspector-title"
  >
    <header class="row items-start no-wrap q-gutter-sm q-pa-md">
      <div class="col overflow-hidden">
        <q-breadcrumbs aria-label="Session-Navigation" class="q-mb-sm">
          <q-breadcrumbs-el label="Hauptsession" icon="arrow_back" :to="parentRoute" />
          <q-breadcrumbs-el label="Subagent" aria-current="page" />
        </q-breadcrumbs>
        <h1 id="subagent-inspector-title" class="text-h6 q-my-none ellipsis" :title="inspector.current?.title || child?.title">
          {{ inspector.current?.title || child?.title || 'Subagent' }}
        </h1>
        <div class="text-caption app-muted" role="status">
          Nur Lesezugriff · {{ inspector.reconnecting ? 'Verbindung wird wiederhergestellt' : subagentStatus(inspector.run || child?.run) }}
        </div>
      </div>
    </header>
    <q-separator />
    <q-scroll-area ref="scrollArea" class="col" @scroll="onScroll">
      <div class="chat-transcript q-pa-md">
          <div v-if="inspector.loading" class="row justify-center q-pa-lg" role="status">
            <q-spinner color="primary" size="32px" />
            <span class="app-sr-only">Subagent wird geladen</span>
          </div>
          <q-banner v-if="inspector.error" rounded class="app-surface-tonal q-mb-md" role="alert">
            {{ apiErrorMessage(inspector.error) }}
          </q-banner>
          <template v-if="inspector.current">
            <q-banner v-if="pendingApprovals.length || pendingQuestions.length || waiting" rounded class="app-surface-tonal q-mb-md" role="status">
              Dieser Subagent wartet auf deine Entscheidung in der Hauptsession.
              <div v-for="approval in pendingApprovals" :key="approval.id" class="text-body2 q-mt-xs">{{ approval.title }}</div>
              <div v-for="question in pendingQuestions" :key="question.id" class="text-body2 q-mt-xs">{{ question.questions?.[0]?.question }}</div>
            </q-banner>
            <q-btn
              v-if="inspector.current.nextMessageCursor"
              flat no-caps color="primary" label="Ältere Nachrichten laden"
              :loading="inspector.olderLoading" class="q-mb-md"
              @click="loadOlder"
            />
            <div v-if="!messages.length" class="app-muted q-py-lg">Noch keine Nachrichten vorhanden.</div>
            <article
              v-for="message in messages"
              :key="message.id"
              :class="['chat-message', `chat-message--${message.role}`, { 'chat-message--turn-continues': message.continuesAssistantTurn }]"
            >
              <div v-if="message.role === 'user' || (message.showAgentLabel && !message.workHistory)" class="chat-message__label row items-center wrap q-gutter-x-sm">
                <span>{{ message.role === 'user' ? 'Auftrag' : 'Subagent' }}</span>
              </div>
              <q-banner v-if="message.kind === 'runtime_error' && message.error" rounded class="app-surface-tonal" role="alert">
                {{ apiErrorMessage(message.error) }}
              </q-banner>
              <template v-else>
                <AgentActivityPanel
                  v-if="message.workHistory"
                  :history="message.workHistory"
                  :running="inspector.running && message.currentTurn"
                  :activity="message.currentTurn ? inspector.activity : null"
                  :presentation="message.currentTurn ? presentation : null"
                  :reasoning="message.currentTurn ? inspector.streamReasoning : null"
                  :context-summary="message.currentTurn ? inspector.streamContextSummary : null"
                  :warnings="message.currentTurn ? inspector.warnings : []"
                  :trusted-image-origins="trustedImageOrigins"
                  read-only
                  class="q-mb-sm"
                  @rendered="followBottom"
                  @trust-image-origin="trustImageOrigin"
                >
                  <template v-if="message.showAgentLabel" #header>
                    <div class="chat-message__label row items-center wrap q-gutter-x-sm">
                      <span>Subagent</span>
                      <span aria-hidden="true">·</span>
                      <span>{{ agentProfileName(message) }}</span>
                    </div>
                  </template>
                </AgentActivityPanel>
                <MessageContent
                  v-if="message.content && message.role === 'assistant'"
                  class="chat-message__content"
                  :content="message.content"
                  :research-sources="message.research?.linkedSources || []"
                  :streaming="message.streaming"
                  :trusted-image-origins="trustedImageOrigins"
                  @trust-image-origin="trustImageOrigin"
                  @rendered="followBottom"
                />
                <div v-else-if="message.content" class="chat-message__content">{{ message.content }}</div>
              </template>
              <MessageAttachments v-if="message.attachments?.length" :attachments="message.attachments" class="q-mt-sm" />
              <div v-if="message.generationStatus === 'interrupted'" class="text-caption app-muted q-mt-sm" role="status">
                Anfrage gestoppt
              </div>
              <div v-if="message.createdAt" class="chat-message__meta items-center">
                <time v-if="message.createdAt" :datetime="message.createdAt">
                  {{ formatAbsoluteDateTime(message.createdAt) }}
                </time>
              </div>
            </article>
          </template>
      </div>
    </q-scroll-area>
  </section>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import AgentActivityPanel from 'components/AgentActivityPanel.vue'
import MessageContent from 'components/MessageContent.vue'
import MessageAttachments from 'components/MessageAttachments.vue'
import { useSubagentStore, subagentStatus } from 'stores/subagents'
import { useApprovalStore } from 'stores/approvals'
import { useQuestionStore } from 'stores/questions'
import { useAgentProfileStore } from 'stores/agent-profiles'
import { buildChatTranscript, withLiveTranscriptMessage } from 'src/utils/chat-transcript'
import { agentRunPresentation } from 'src/utils/agent-activity'
import { apiErrorMessage } from 'src/utils/api-errors'
import { formatAbsoluteDateTime } from 'src/utils/date-time'

const props = defineProps({ parentId: { type: String, required: true }, childId: { type: String, required: true } })
const route = useRoute()
const inspector = useSubagentStore()
const approvals = useApprovalStore()
const questions = useQuestionStore()
const agentProfiles = useAgentProfileStore()
const scrollArea = ref(null)
const returnFocus = document.activeElement?.closest('[tabindex]:not([tabindex^="-"])') || document.activeElement
const following = ref(true)
const trustedImageOrigins = ref([])
const parentRoute = computed(() => {
  const query = { ...route.query, session: props.parentId }
  delete query.subagent
  return { name: 'chat', query }
})
const child = computed(() => inspector.children.find((item) => item.id === props.childId))
const pendingApprovals = computed(() => approvals.forSession(props.parentId).filter((item) => item.payload?.subagentSessionId === props.childId))
const pendingQuestions = computed(() => questions.forSession(props.parentId).filter((item) => item.payload?.subagentSessionId === props.childId))
const waiting = computed(() => ['waiting_approval', 'waiting_input'].includes(inspector.run?.status))
const transcript = computed(() => buildChatTranscript(inspector.current?.messages, pendingApprovals.value, {
  running: inspector.running,
  runId: inspector.run?.id,
  toolExecutionActive: inspector.activity.phase === 'processing_tool',
  activeToolCallId: inspector.activity.toolCallId,
  completedToolCallId: inspector.completedToolCallId,
  questions: pendingQuestions.value,
  contextSummaries: inspector.current?.contextSummaries
}))
const presentation = computed(() => agentRunPresentation({
  runId: inspector.run?.id,
  running: inspector.running, activity: inspector.activity, streamContent: inspector.streamContent,
  streamReasoning: inspector.streamReasoning, streamContextSummary: inspector.streamContextSummary,
  transcript: transcript.value
}))
const messages = computed(() => withLiveTranscriptMessage(transcript.value.messages, {
  id: `live-${props.childId}`, role: 'assistant', content: inspector.streamContent,
  research: { linkedSources: inspector.streamResearchSources },
  agentProfileId: inspector.run?.agentProfileId || null,
  streaming: Boolean(inspector.streamContent), currentTurn: true,
  showAgentLabel: !transcript.value.messages.some((message) => message.currentTurn),
  workHistory: inspector.running && !transcript.value.messages.some((message) => message.currentTurn && message.workHistory)
    ? { entries: [] } : null
}))
const agentProfileName = (message) => agentProfiles.byId(message.agentProfileId)?.name || 'Allgemein'

function trustImageOrigin(origin) {
  if (!trustedImageOrigins.value.includes(origin)) trustedImageOrigins.value.push(origin)
}
function onScroll(info) {
  following.value = info.verticalSize - info.verticalContainerSize - info.verticalPosition < 64
}
async function followBottom() {
  await nextTick()
  if (following.value && !inspector.olderLoading) {
    const target = scrollArea.value?.getScrollTarget()
    if (target) scrollArea.value.setScrollPosition('vertical', target.scrollHeight)
  }
}
async function loadOlder() {
  following.value = false
  const target = scrollArea.value?.getScrollTarget()
  const height = target?.scrollHeight || 0
  const position = target?.scrollTop || 0
  await inspector.loadOlder()
  await nextTick()
  if (target) scrollArea.value?.setScrollPosition('vertical', position + target.scrollHeight - height)
}
watch([() => props.parentId, () => props.childId], ([parent, id]) => {
  following.value = true
  trustedImageOrigins.value = []
  void inspector.open(parent, id)
}, { immediate: true })
watch(messages, followBottom, { flush: 'post' })
onBeforeUnmount(() => {
  inspector.close()
  void nextTick(() => {
    if (!route.query.subagent && route.query.session === props.parentId && returnFocus?.isConnected) {
      returnFocus.focus({ preventScroll: true })
    }
  })
})
</script>
