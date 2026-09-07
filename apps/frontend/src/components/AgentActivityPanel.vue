<template>
  <div class="work-history">
    <q-expansion-item
      v-model="historyExpanded"
      dense
      class="work-history__panel"
      header-class="work-history__header"
      expand-icon-class="work-history__toggle"
      :duration="expansionDuration"
      :toggle-aria-label="`${headerTitle} ein- oder ausblenden`"
      @before-show="emit('resize-start')"
      @before-hide="emit('resize-start')"
      @after-show="emit('resized')"
      @after-hide="emit('resized')"
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
              <q-tooltip>Antwortdauer ohne Wartezeit auf dich</q-tooltip>
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
      v-if="historyExpanded"
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
            @resize-start="emit('resize-start')"
            @resized="emit('resized')"
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
            @before-show="emit('resize-start')"
            @before-hide="emit('resize-start')"
            @after-show="emit('resized')"
            @after-hide="emit('resized')"
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
                @resize-start="emit('resize-start')"
                @resized="emit('resized')"
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
            @resize-start="emit('resize-start')"
            @resized="emit('resized')"
          />
        </template>
      </div>
    </div>
    </q-expansion-item>

    <SubagentRows :children="subagents" />

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
          @open-file="emit('open-file', $event)"
        />
        <div v-if="output.fileActions.length" class="work-history__file-outputs">
          <div
            v-for="action in output.fileActions"
            :key="`${action.rootIdentity || action.executionAgentId}:${action.path}`"
            class="work-history__file-output app-surface"
          >
            <q-icon name="draft" color="primary" size="24px" />
            <span class="col ellipsis text-weight-medium">{{ action.filename }}</span>
            <q-btn
              v-if="!readOnly"
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
  readOnly: Boolean,
  running: Boolean
})
const emit = defineEmits(['open-file', 'open-approval-draft', 'resize-start', 'resized'])
const instanceLimits = useInstanceLimitsStore()
const now = ref(Date.now())
const historyExpanded = ref(false)
const previousHistoryExpanded = ref(false)
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
const approvalItems = computed(() => props.readOnly ? [] : items.value.filter((item) => item.approval))
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
const outputs = computed(() => {
  const prepared = items.value.map((item) => {
    const providedFile = toolFileAttachment(
      item.tool,
      item.result,
      instanceLimits.maxImagePreviewBytes
    )
    return {
      key: `output-${item.key}`,
      attachments: toolOutputAttachments(item.tool, [
        ...(item.result?.attachments || []),
        ...(providedFile ? [providedFile] : [])
      ]),
      fileActions: toolFileActions(item.result)
    }
  })
  const fileActions = prepared.flatMap((output) => output.fileActions)
  return prepared.flatMap((output) => {
    const attachments = deduplicateFileOutputAttachments(output.attachments, fileActions)
    return attachments.length || output.fileActions.length
      ? [{ ...output, attachments }]
      : []
  })
})

function updateTimer(running) {
  clearInterval(timer)
  timer = running ? setInterval(() => { now.value = Date.now() }, 1000) : null
}

function syncTimer() {
  now.value = Date.now()
  updateTimer(props.running && Boolean(durationState.value?.activeDurationUpdatedAt))
}

watch([() => props.running, () => durationState.value?.activeDurationUpdatedAt], ([running], [wasRunning]) => {
  syncTimer()
  if (wasRunning && !running) {
    historyExpanded.value = false
    previousHistoryExpanded.value = false
  }
})
onMounted(syncTimer)
onBeforeUnmount(() => clearInterval(timer))
</script>
