<template>
  <div
    v-if="$slots.header || entries.length || running || todos.length || unmatchedSubagents.length"
    class="work-history"
    :class="{
      'work-history--preview': showPreview,
      'work-history--with-agent-header': $slots.header
    }"
  >
    <div v-if="$slots.header" class="work-history__agent-header">
      <slot name="header" />
    </div>
    <div v-if="$slots.header" class="work-history__header-actions row items-center no-wrap">
      <TurnDuration :history="running ? activity : history" :running="running" />
      <q-btn
        flat round dense
        size="sm"
        class="work-history__toggle q-ml-sm"
        icon="history"
        :color="historyExpanded ? 'primary' : undefined"
        :aria-expanded="historyExpanded"
        :aria-label="historyExpanded ? 'Verlauf ausblenden' : 'Verlauf anzeigen'"
        @click="toggleHistory"
      >
        <q-tooltip>{{ historyExpanded ? 'Verlauf ausblenden' : 'Verlauf anzeigen' }}</q-tooltip>
      </q-btn>
    </div>
    <div v-if="!$slots.header" class="work-history__meta-section row items-center no-wrap" :class="showPreview ? 'self-start' : 'self-center'">
      <div class="work-history__meta row items-center no-wrap q-gutter-xs">
        <TurnDuration :history="running ? activity : history" :running="running" />
      </div>
      <q-btn
        flat round dense
        size="sm"
        class="work-history__toggle q-ml-sm"
        icon="history"
        :color="historyExpanded ? 'primary' : undefined"
        :aria-expanded="historyExpanded"
        :aria-label="historyExpanded ? 'Verlauf ausblenden' : 'Verlauf anzeigen'"
        @click="toggleHistory"
      >
        <q-tooltip>{{ historyExpanded ? 'Verlauf ausblenden' : 'Verlauf anzeigen' }}</q-tooltip>
      </q-btn>
    </div>

    <div v-if="displayEntries.length" class="work-history__timeline" role="region" aria-label="Aktivitäten der Antworterstellung">
      <div class="work-history__entries">
        <AgentActivityEntry
          v-for="entry in displayEntries"
          :key="entry.key"
          :entry="entry"
          :expanded-details="expandedDetails"
          :trusted-image-origins="trustedImageOrigins"
          :read-only="readOnly"
          :show-output="historyExpanded"
          show-supplementary
          @open-approval-draft="emit('open-approval-draft', $event)"
          @open-file="emit('open-file', $event)"
          @rendered="emit('rendered')"
          @resize-start="emit('resize-start')"
          @resized="emit('resized')"
          @toggle-details="toggleDetails"
          @trust-image-origin="emit('trust-image-origin', $event)"
        />
      </div>
    </div>
    <div v-if="!historyExpanded && outputEntries.length" class="work-history__persistent-outputs">
      <AgentActivityEntry
        v-for="entry in outputEntries"
        :key="entry.key"
        :entry="entry"
        :expanded-details="expandedDetails"
        :read-only="readOnly"
        output-only
        show-output
        @open-file="emit('open-file', $event)"
      />
    </div>
    <SessionTodoStrip
      v-if="todos.length"
      class="work-history__todos"
      :todos="todos"
    />
    <SubagentRows class="work-history__children" :children="unmatchedSubagents" />
  </div>
</template>

<script setup>
import { computed, nextTick, ref } from 'vue'
import AgentActivityEntry from 'src/features/chat/components/AgentActivityEntry.vue'
import SessionTodoStrip from 'src/features/chat/components/SessionTodoStrip.vue'
import SubagentRows from 'src/features/subagents/SubagentRows.vue'
import TurnDuration from 'src/features/chat/components/TurnDuration.vue'
import {
  deduplicateFileOutputAttachments,
  toolOutputAttachments,
  toolFileActions,
  toolFileAttachment
} from 'src/utils/attachments'
import { agentRunPresentation, runWarningEntries } from 'src/utils/agent-activity'
import { groupWorkHistoryEntries } from 'src/utils/chat-transcript'
import { reasoningBlocks, reasoningHistoryPresentation } from 'src/utils/reasoning-display'
import { useInstanceLimitsStore } from 'stores/instance-limits'

defineOptions({ name: 'AgentActivityPanel' })

const props = defineProps({
  history: { type: Object, required: true },
  activity: { type: Object, default: null },
  presentation: { type: Object, default: null },
  reasoning: { type: Object, default: null },
  contextSummary: { type: Object, default: null },
  warnings: { type: Array, default: () => [] },
  subagents: { type: Array, default: () => [] },
  todos: { type: Array, default: () => [] },
  trustedImageOrigins: { type: Array, default: () => [] },
  readOnly: Boolean,
  running: Boolean,
  expanded: { type: Boolean, default: null }
})
const emit = defineEmits(['history-expanded', 'open-file', 'open-approval-draft', 'rendered', 'resize-start', 'resized', 'trust-image-origin'])
const instanceLimits = useInstanceLimitsStore()
const localHistoryExpanded = ref(false)
const historyExpanded = computed(() => props.expanded ?? localHistoryExpanded.value)
const expandedDetails = ref(new Set())
function toggleDetails(key) {
  if (expandedDetails.value.has(key)) expandedDetails.value.delete(key)
  else expandedDetails.value.add(key)
}
function toggleHistory() {
  emit('resize-start')
  const expanded = !historyExpanded.value
  if (props.expanded === null) localHistoryExpanded.value = expanded
  emit('history-expanded', expanded)
  nextTick(() => emit('resized'))
}
const presentation = computed(() => props.presentation || agentRunPresentation({
  running: props.running, activity: props.activity,
  streamReasoning: props.reasoning, streamContextSummary: props.contextSummary,
  transcript: { messages: [{ workHistory: props.history }] }
}))
const currentBatch = computed(() => props.running && props.activity?.phase === 'processing_tool'
  && presentation.value.entry?.type === 'tool' ? presentation.value.entry.batchId : null)
const preparedEntries = computed(() => {
  const current = presentation.value.entry
  const history = (props.history?.entries || []).filter((entry) => !(entry.type === 'reasoning' && entry.live) && (
    entry.type !== 'tool' || !props.running || entry.result || entry.completed || entry.approval || entry.question
      || entry.key === current?.key || (!historyExpanded.value && currentBatch.value != null && entry.batchId === currentBatch.value)
  )).map((entry) => {
    const prepared = entry.key === current?.key ? current : { ...entry, active: false }
    return prepared.type === 'reasoning' ? {
      ...prepared,
      blocks: reasoningBlocks(prepared.reasoning),
      presentation: reasoningHistoryPresentation(prepared.reasoning)
    } : prepared
  })
  return [
    ...history,
    ...runWarningEntries(props.warnings, 'live-warning', true),
    ...(current && !history.some((entry) => entry.key === current.key) ? [current] : [])
  ]
})
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
const outputEntries = computed(() => enrichedEntries.value.filter((entry) => (
  entry.type === 'tool' && (entry.output.attachments.length || entry.output.fileActions.length)
)))
const entries = computed(() => groupWorkHistoryEntries(
  enrichedEntries.value, enrichedEntries.value.find((entry) => entry.key === presentation.value.entry?.key)
))
const currentEntries = computed(() => {
  const current = presentation.value.entry
  if (!current) return []
  const latestCommentary = current.tool?.id
    ? enrichedEntries.value.findLast((entry) => entry.type === 'commentary' && entry.toolCallIds?.length)
    : null
  return enrichedEntries.value.filter((entry) => entry.key === current.key
    || (currentBatch.value != null && entry.type === 'tool' && entry.batchId === currentBatch.value) || (
    entry.type === 'commentary' && current.tool?.id
      && (entry === latestCommentary || entry.toolCallIds?.includes(current.tool.id))
  ))
})
const showPreview = computed(() => !historyExpanded.value && currentEntries.value.length > 0)
const displayEntries = computed(() => historyExpanded.value ? entries.value
  : currentBatch.value != null ? groupWorkHistoryEntries(currentEntries.value, null, true) : currentEntries.value)
const items = computed(() => enrichedEntries.value.filter((entry) => entry.type === 'tool'))
const unmatchedSubagents = computed(() => {
  const matched = new Set(items.value.flatMap((item) => item.subagents || []).map((child) => child.id))
  return props.subagents.filter((child) => !matched.has(child.id))
})
</script>
