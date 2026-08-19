<template>
  <div ref="panel" class="work-history">
    <q-expansion-item
      v-model="historyExpanded"
      dense
      class="work-history__panel"
      header-class="work-history__header"
      expand-icon-class="work-history__toggle"
      :duration="expansionDuration"
      :toggle-aria-label="`${headerTitle} ein- oder ausblenden`"
      @after-show="emit('expanded', panel)"
    >
      <template #header>
        <q-item-section class="work-history__identity" :aria-label="headerAriaLabel">
          <div class="work-history__title text-weight-medium">
            <span :class="{ 'activity-shimmer': running }">{{ headerTitle }}</span>
            <span v-if="toolUsage.length" class="work-history__tool-icons" aria-hidden="true">
              <span
                v-for="tool in toolUsage"
                :key="tool.name"
                class="work-history__tool-usage"
              >
                <span
                  class="work-history__tool-icon flex flex-center"
                  :class="{ 'work-history__tool-icon--active': tool.active }"
                >
                  <q-icon :name="tool.icon" size="15px" />
                </span>
                <span class="work-history__tool-count">×{{ tool.count }}</span>
                <q-tooltip>
                  {{ tool.label }} · {{ tool.count }} {{ tool.count === 1 ? 'Aufruf' : 'Aufrufe' }}{{ tool.active ? ' · aktiv' : '' }}
                </q-tooltip>
              </span>
            </span>
          </div>
          <div v-if="headerSummary" class="work-history__summary">
            {{ headerSummary }}
          </div>
        </q-item-section>
        <q-item-section side class="work-history__meta-section">
          <div class="work-history__meta row items-center no-wrap q-gutter-xs">
            <span v-if="totalDuration">
              {{ totalDuration }}
              <q-tooltip>Gesamtdauer bis zur Antwort</q-tooltip>
            </span>
            <span
              v-if="errorItems.length"
              class="work-history__message-count work-history__message-count--error row items-center no-wrap"
              role="img"
              :aria-label="errorLabel"
            >
              <q-icon name="error" size="17px" />
              <span>{{ errorItems.length }}</span>
            </span>
            <span
              v-if="warningItems.length"
              class="work-history__message-count work-history__message-count--warning row items-center no-wrap"
              role="img"
              :aria-label="warningLabel"
            >
              <q-icon name="warning" size="17px" />
              <span>{{ warningItems.length }}</span>
            </span>
          </div>
        </q-item-section>
      </template>

    <div
      class="work-history__timeline"
      role="region"
      aria-label="Aktivitäten der Antworterstellung"
    >
      <div class="work-history__entries">
        <template v-if="running">
          <AgentActivityEntry
            v-if="liveHistory.current"
            :key="`live-${liveHistory.current.key}`"
            :entry="liveHistory.current"
            :process-outputs="processOutputs"
            auto-expand
            @open-file="emit('open-file', $event)"
          />
          <q-expansion-item
            v-if="previousEntries.length"
            v-model="previousHistoryExpanded"
            dense
            class="work-history__context"
            header-class="work-history__context-header"
            expand-icon-class="work-history__context-toggle"
            :duration="expansionDuration"
            toggle-aria-label="Bisherigen Verlauf ein- oder ausblenden"
          >
            <template #header>
              <q-item-section>
                <q-item-label lines="1" class="work-history__activity-line">
                  <span class="work-history__activity-title">Bisheriger Verlauf</span>
                </q-item-label>
              </q-item-section>
            </template>
            <div class="work-history__context-items">
              <AgentActivityEntry
                v-for="entry in previousEntries"
                :key="`previous-${entry.key}`"
                :entry="entry"
                :process-outputs="processOutputs"
                @open-file="emit('open-file', $event)"
              />
            </div>
          </q-expansion-item>
        </template>
        <template v-else>
          <AgentActivityEntry
            v-for="entry in entries"
            :key="`history-${entry.key}`"
            :entry="entry"
            :process-outputs="processOutputs"
            @open-file="emit('open-file', $event)"
          />
        </template>
      </div>
    </div>
    </q-expansion-item>

    <ApprovalCard
      v-for="item in approvalItems"
      :key="`approval-${item.approval.id}`"
      :approval="item.approval"
      class="work-history__attention"
      @open-approval-draft="emit('open-approval-draft', $event)"
    />

    <div v-if="outputs.length" class="work-history__outputs">
      <div class="tool-call__section-label">Ausgaben</div>
      <div v-for="output in outputs" :key="output.key" class="work-history__output">
        <MessageAttachments
          v-if="output.attachments.length"
          :attachments="output.attachments"
        />
        <div v-if="output.fileActions.length" class="work-history__file-outputs">
          <div
            v-for="action in output.fileActions"
            :key="`${action.rootIdentity}:${action.path}`"
            class="work-history__file-output app-surface"
          >
            <q-icon name="draft" color="primary" size="24px" />
            <span class="col ellipsis text-weight-medium">{{ action.filename }}</span>
            <q-badge
              v-if="action.intent === 'draft'"
              outline
              color="primary"
              label="Entwurf"
            />
            <q-btn
              flat
              round
              dense
              icon="visibility"
              :aria-label="`${action.filename} ansehen`"
              @click="emit('open-file', action)"
            >
              <q-tooltip>Datei ansehen</q-tooltip>
            </q-btn>
            <q-btn
              flat
              round
              dense
              icon="download"
              :href="action.downloadUrl"
              :download="action.filename"
              :aria-label="`${action.filename} herunterladen`"
            >
              <q-tooltip>Datei herunterladen</q-tooltip>
            </q-btn>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import AgentActivityEntry from 'components/AgentActivityEntry.vue'
import ApprovalCard from 'components/ApprovalCard.vue'
import MessageAttachments from 'components/MessageAttachments.vue'
import {
  toolOutputAttachments,
  toolFileActions,
  toolFileAttachment
} from 'src/utils/attachments'
import { formatActivityDuration } from 'src/utils/agent-activity'
import { groupWorkHistoryEntries, splitLiveWorkHistory } from 'src/utils/chat-transcript'
import {
  reasoningBlocks,
  reasoningHistoryPresentation
} from 'src/utils/reasoning-display'
import { toolStatus, toolUsageSummary } from 'src/utils/tool-display'
import { useInstanceLimitsStore } from 'stores/instance-limits'

defineOptions({ name: 'AgentActivityPanel' })

const props = defineProps({
  history: { type: Object, required: true },
  activity: { type: Object, default: null },
  presentation: { type: Object, default: null },
  reasoning: { type: Object, default: null },
  processOutputs: { type: Object, default: () => ({}) },
  running: Boolean
})
const emit = defineEmits(['open-file', 'open-approval-draft', 'expanded'])
const instanceLimits = useInstanceLimitsStore()
const panel = ref(null)
const now = ref(Date.now())
const historyExpanded = ref(false)
const previousHistoryExpanded = ref(false)
let timer = null
const expansionDuration = globalThis.matchMedia?.('(prefers-reduced-motion: reduce)').matches ? 0 : 180
const preparedEntries = computed(() => [
  ...(props.history?.entries || []),
  ...(props.reasoning
    ? [{ key: 'live-reasoning', type: 'reasoning', reasoning: props.reasoning, live: true }]
    : [])
].map((entry) => entry.type === 'reasoning'
  ? {
      ...entry,
      blocks: reasoningBlocks(entry.reasoning),
      presentation: reasoningHistoryPresentation(entry.reasoning, entry.live)
    }
  : entry))
const entries = computed(() => groupWorkHistoryEntries(preparedEntries.value))
const liveHistory = computed(() => splitLiveWorkHistory(preparedEntries.value, props.activity))
const previousEntries = computed(() => groupWorkHistoryEntries(liveHistory.value.previous))
const items = computed(() => entries.value.flatMap((entry) => (
  entry.type === 'context' ? entry.items : entry.type === 'tool' ? [entry] : []
)))
const states = computed(() => items.value.map((item) => ({ item, status: toolStatus(item) })))
const activeToolName = computed(() => props.running && props.activity?.phase === 'processing_tool'
  ? props.activity.tool
  : null)
const toolUsage = computed(() => toolUsageSummary(items.value).map((tool) => ({
  ...tool,
  active: tool.active || tool.name === activeToolName.value
})))
const approvalItems = computed(() => items.value.filter((item) => item.approval))
const errorItems = computed(() => states.value.filter(({ status }) => (
  ['failed', 'denied'].includes(status.name)
)))
const warningItems = computed(() => states.value.filter(({ status }) => status.name === 'interrupted'))
const totalDuration = computed(() => {
  if (!props.history?.startedAt || (!props.running && !props.history?.completedAt)) return null
  const startedAt = new Date(props.history?.startedAt).getTime()
  const completedAt = props.running ? now.value : new Date(props.history?.completedAt).getTime()
  return Number.isFinite(startedAt) && Number.isFinite(completedAt)
    ? formatActivityDuration(startedAt, completedAt)
    : null
})
const headerTitle = computed(() => props.running
  ? props.presentation?.label || 'Agent arbeitet'
  : 'Verlauf')
const headerSummary = computed(() => props.running ? props.presentation?.summary || '' : '')
const headerAriaLabel = computed(() => {
  const parts = [
    props.running ? `Aktuell: ${headerTitle.value}` : 'Verlauf der Antworterstellung',
    ...(headerSummary.value ? [headerSummary.value] : []),
    ...toolUsage.value.map((tool) => `${tool.label}: ${tool.count} ${tool.count === 1 ? 'Aufruf' : 'Aufrufe'}`),
    ...(totalDuration.value ? [`Gesamtdauer: ${totalDuration.value}`] : []),
    ...(errorItems.value.length ? [errorLabel.value] : []),
    ...(warningItems.value.length ? [warningLabel.value] : [])
  ]
  return parts.join(', ')
})
const errorLabel = computed(() => (
  `${errorItems.value.length} Fehler`
))
const warningLabel = computed(() => (
  `${warningItems.value.length} ${warningItems.value.length === 1 ? 'Warnung' : 'Warnungen'}`
))
const outputs = computed(() => items.value.flatMap((item) => {
  const providedFile = toolFileAttachment(
    item.tool,
    item.result,
    instanceLimits.maxImagePreviewBytes
  )
  const attachments = toolOutputAttachments(item.tool, [
    ...(item.result?.attachments || []),
    ...(providedFile ? [providedFile] : [])
  ])
  const fileActions = toolFileActions(item.result)
  return attachments.length || fileActions.length
    ? [{ key: `output-${item.key}`, attachments, fileActions }]
    : []
}))

function updateTimer(running) {
  clearInterval(timer)
  timer = running ? setInterval(() => { now.value = Date.now() }, 1000) : null
}

watch(() => props.running, (running, wasRunning) => {
  updateTimer(running)
  if (wasRunning && !running) {
    historyExpanded.value = false
    previousHistoryExpanded.value = false
  }
})
onMounted(() => updateTimer(props.running))
onBeforeUnmount(() => clearInterval(timer))
</script>
