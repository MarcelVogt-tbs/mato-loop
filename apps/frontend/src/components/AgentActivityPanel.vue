<template>
  <div class="work-history">
    <div
      v-if="displayMode === 'timeline'"
      class="work-history__timeline"
      role="region"
      aria-label="Aktivitäten der Antworterstellung"
    >
      <div class="work-history__entries">
        <AgentActivityEntry
          v-for="entry in entries"
          :key="entry.key"
          :entry="entry"
          :process-outputs="processOutputs"
          :trusted-image-origins="trustedImageOrigins"
          :read-only="readOnly"
          show-supplementary
          @open-approval-draft="emit('open-approval-draft', $event)"
          @open-file="emit('open-file', $event)"
          @rendered="emit('rendered')"
          @resize-start="emit('resize-start')"
          @resized="emit('resized')"
          @trust-image-origin="emit('trust-image-origin', $event)"
        />
        <q-item v-if="running && !liveHistory.current && presentation?.label" dense>
          <q-item-section>
            <q-item-label class="activity-shimmer">{{ presentation.label }}</q-item-label>
            <q-item-label v-if="presentation.summary" caption>{{ presentation.summary }}</q-item-label>
          </q-item-section>
        </q-item>
      </div>
    </div>

    <template v-else>
      <AgentActivityEntry
        v-if="running && liveHistory.current"
        :key="`live-${liveHistory.current.key}`"
        :entry="liveHistory.current"
        :process-outputs="processOutputs"
        :trusted-image-origins="trustedImageOrigins"
        :read-only="readOnly"
        auto-expand
        @open-file="emit('open-file', $event)"
        @rendered="emit('rendered')"
        @resize-start="emit('resize-start')"
        @resized="emit('resized')"
        @trust-image-origin="emit('trust-image-origin', $event)"
      />
      <q-item v-else-if="running && presentation?.label" dense>
        <q-item-section>
          <q-item-label class="activity-shimmer">{{ presentation.label }}</q-item-label>
          <q-item-label v-if="presentation.summary" caption>{{ presentation.summary }}</q-item-label>
        </q-item-section>
      </q-item>

      <q-expansion-item
        v-if="compactEntries.length || totalDuration"
        v-model="historyExpanded"
        dense
        class="work-history__panel"
        header-class="work-history__header"
        expand-icon-class="work-history__toggle"
        :duration="expansionDuration"
        :toggle-aria-label="`${compactHeaderTitle} ein- oder ausblenden`"
        @before-show="emit('resize-start')"
        @before-hide="emit('resize-start')"
        @after-show="emit('resized')"
        @after-hide="emit('resized')"
      >
        <template #header>
          <q-item-section class="work-history__identity" :aria-label="headerAriaLabel">
            <div class="work-history__title text-weight-medium">
              <span>{{ compactHeaderTitle }}</span>
              <span v-if="toolUsage.length" class="work-history__tool-icons" aria-hidden="true">
                <span v-for="tool in toolUsage" :key="tool.name" class="work-history__tool-usage">
                  <span class="work-history__tool-icon flex flex-center" :class="{ 'work-history__tool-icon--active': tool.active }">
                    <q-icon :name="tool.icon" size="15px" />
                  </span>
                  <span class="work-history__tool-count">×{{ tool.count }}</span>
                  <q-tooltip>{{ tool.label }} · {{ tool.count }} {{ tool.count === 1 ? 'Aufruf' : 'Aufrufe' }}</q-tooltip>
                </span>
              </span>
            </div>
          </q-item-section>
          <q-item-section side class="work-history__meta-section">
            <div class="work-history__meta row items-center no-wrap q-gutter-xs">
              <span v-if="totalDuration">{{ totalDuration }}<q-tooltip>Antwortdauer ohne Wartezeit auf dich</q-tooltip></span>
              <span v-if="errorItems.length" class="work-history__message-count work-history__message-count--error row items-center no-wrap" role="img" :aria-label="errorLabel">
                <q-icon name="error" size="17px" /><span>{{ errorItems.length }}</span>
              </span>
              <span v-if="warningItems.length" class="work-history__message-count work-history__message-count--warning row items-center no-wrap" role="img" :aria-label="warningLabel">
                <q-icon name="warning" size="17px" /><span>{{ warningItems.length }}</span>
              </span>
            </div>
          </q-item-section>
        </template>
        <div v-if="historyExpanded" class="work-history__timeline" role="region" aria-label="Aktivitäten der Antworterstellung">
          <div class="work-history__entries">
            <AgentActivityEntry
              v-for="entry in compactEntries"
              :key="entry.key"
              :entry="entry"
              :process-outputs="processOutputs"
              :trusted-image-origins="trustedImageOrigins"
              :read-only="readOnly"
              @open-file="emit('open-file', $event)"
              @rendered="emit('rendered')"
              @resize-start="emit('resize-start')"
              @resized="emit('resized')"
              @trust-image-origin="emit('trust-image-origin', $event)"
            />
          </div>
        </div>
      </q-expansion-item>
    </template>

    <template v-if="displayMode !== 'timeline'">
      <AgentActivityEntry
        v-for="item in supplementaryItems"
        :key="`supplementary-${item.key}`"
        :entry="item"
        :read-only="readOnly"
        supplementary-only
        @open-approval-draft="emit('open-approval-draft', $event)"
        @open-file="emit('open-file', $event)"
      />
    </template>
    <SubagentRows :children="unmatchedSubagents" />
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import AgentActivityEntry from 'components/AgentActivityEntry.vue'
import SubagentRows from 'components/SubagentRows.vue'
import {
  deduplicateFileOutputAttachments,
  toolOutputAttachments,
  toolFileActions,
  toolFileAttachment
} from 'src/utils/attachments'
import {
  activityDurationMs,
  formatActivityDuration,
  runWarningEntries
} from 'src/utils/agent-activity'
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
  contextSummary: { type: Object, default: null },
  warnings: { type: Array, default: () => [] },
  processOutputs: { type: Object, default: () => ({}) },
  subagents: { type: Array, default: () => [] },
  displayMode: { type: String, default: 'compact' },
  trustedImageOrigins: { type: Array, default: () => [] },
  readOnly: Boolean,
  running: Boolean
})
const emit = defineEmits(['open-file', 'open-approval-draft', 'rendered', 'resize-start', 'resized', 'trust-image-origin'])
const instanceLimits = useInstanceLimitsStore()
const now = ref(Date.now())
const historyExpanded = ref(false)
let timer = null
const expansionDuration = globalThis.matchMedia?.('(prefers-reduced-motion: reduce)').matches ? 0 : 180
const preparedEntries = computed(() => [
  ...(props.history?.entries || []),
  ...runWarningEntries(props.warnings, 'live-warning', true),
  ...(props.reasoning
    ? [{ key: 'live-reasoning', type: 'reasoning', reasoning: props.reasoning, live: true }]
    : []),
  ...(props.contextSummary?.content
    ? [{
        key: 'live-context-summary',
        type: 'context-summary',
        text: props.contextSummary.content,
        warning: props.contextSummary.isFallback,
        current: true,
        live: true,
        presentation: {
          title: 'Gesprächskontext wird verdichtet',
          summary: props.contextSummary.isFallback ? 'Vereinfachte Ersatzfassung' : null,
          duration: null
        }
      }]
    : [])
].map((entry) => entry.type === 'reasoning'
  ? {
      ...entry,
      blocks: reasoningBlocks(entry.reasoning),
      presentation: reasoningHistoryPresentation(entry.reasoning, entry.live)
    }
  : entry))
const enrichedEntries = computed(() => {
  const outputs = preparedEntries.value.filter((entry) => entry.type === 'tool').map((entry) => {
    const providedFile = toolFileAttachment(
      entry.tool,
      entry.result,
      instanceLimits.maxImagePreviewBytes
    )
    return {
      key: entry.key,
      attachments: toolOutputAttachments(entry.tool, [
        ...(entry.result?.attachments || []),
        ...(providedFile ? [providedFile] : [])
      ]),
      fileActions: toolFileActions(entry.result)
    }
  })
  const fileActions = outputs.flatMap((output) => output.fileActions)
  const outputsByKey = new Map(outputs.map((output) => [output.key, {
    attachments: deduplicateFileOutputAttachments(output.attachments, fileActions),
    fileActions: output.fileActions
  }]))
  return preparedEntries.value.map((entry) => entry.type === 'tool' ? {
    ...entry,
    subagents: props.subagents.filter((child) => (
      child.spawnedByToolCallId === entry.tool?.id && child.spawnedByRunId === entry.runId
    )),
    output: outputsByKey.get(entry.key)
  } : entry)
})
const entries = computed(() => groupWorkHistoryEntries(enrichedEntries.value))
const liveHistory = computed(() => splitLiveWorkHistory(enrichedEntries.value, props.activity))
const previousEntries = computed(() => groupWorkHistoryEntries(liveHistory.value.previous))
const compactEntries = computed(() => props.running ? previousEntries.value : entries.value)
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
const supplementaryItems = computed(() => items.value.filter((item) => (
  item.subagents?.length
  || (!props.readOnly && item.approval)
  || item.output?.attachments.length
  || item.output?.fileActions.length
)))
const unmatchedSubagents = computed(() => {
  const matched = new Set(items.value.flatMap((item) => item.subagents || []).map((child) => child.id))
  return props.subagents.filter((child) => !matched.has(child.id))
})
const errorItems = computed(() => states.value.filter(({ status }) => (
  ['failed', 'denied'].includes(status.name)
)))
const warningItems = computed(() => [
  ...states.value.filter(({ status }) => status.name === 'interrupted'),
  ...entries.value.filter((entry) => entry.type === 'warning')
])
const durationState = computed(() => props.running ? props.activity : props.history)
const totalDuration = computed(() => {
  const duration = activityDurationMs(durationState.value, props.running, now.value)
  return duration === null ? null : formatActivityDuration(duration)
})
const compactHeaderTitle = computed(() => props.running ? 'Bisheriger Verlauf' : 'Verlauf')
const headerAriaLabel = computed(() => {
  const parts = [
    compactHeaderTitle.value,
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
function updateTimer(running) {
  clearInterval(timer)
  timer = running ? setInterval(() => { now.value = Date.now() }, 1000) : null
}

function syncTimer() {
  now.value = Date.now()
  updateTimer(props.running && Boolean(durationState.value?.activeDurationUpdatedAt))
}

watch([() => props.running, () => durationState.value?.activeDurationUpdatedAt], () => {
  syncTimer()
})
onMounted(syncTimer)
onBeforeUnmount(() => clearInterval(timer))
</script>
