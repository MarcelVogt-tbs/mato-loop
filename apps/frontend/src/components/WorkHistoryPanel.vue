<template>
  <div class="work-history">
    <q-expansion-item
      :model-value="panelExpanded"
      dense
      class="work-history__panel"
      header-class="work-history__header"
      :duration="180"
      @update:model-value="updateExpanded"
    >
      <template #header>
        <q-item-section class="work-history__identity" :aria-label="headerAriaLabel">
          <div class="work-history__title text-weight-medium">
            <span v-if="activityDisplay" class="work-history__current">
              <span class="work-history__status-dot" aria-hidden="true" />
              <span class="work-history__current-label">{{ activityDisplay.label }}</span>
            </span>
            <span v-else>Verlauf</span>
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
          <q-item-label
            v-if="activityDisplay?.summary"
            caption
            lines="1"
            class="work-history__summary"
          >
            <span class="work-history__resource">{{ activityDisplay.summary }}</span>
          </q-item-label>
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
        aria-label="Verlauf der Antworterstellung"
        tabindex="0"
      >
        <div class="work-history__entries">
          <template v-for="entry in entries" :key="entry.key">
            <div v-if="entry.type === 'reasoning'" class="work-history__reasoning">
              <div class="work-history__marker flex flex-center">
                <q-icon name="psychology_alt" size="16px" />
              </div>
              <div class="work-history__reasoning-main">
                <div class="work-history__entry-header row items-center q-gutter-x-sm">
                  <span class="text-weight-medium">{{ reasoningTitle(entry.reasoning) }}</span>
                  <span v-if="entryDuration(entry.reasoning)" class="work-history__entry-meta">
                    {{ entryDuration(entry.reasoning) }}
                  </span>
                  <span v-if="entry.reasoning.reasoningTokens" class="work-history__entry-meta">
                    {{ entry.reasoning.reasoningTokens }} Tok.
                  </span>
                </div>
                <div class="work-history__reasoning-text">
                  <p
                    v-for="(block, blockIndex) in entry.blocks"
                    :key="blockIndex"
                    class="work-history__reasoning-block"
                  >
                    <template v-for="(segment, segmentIndex) in block" :key="segmentIndex">
                      <strong v-if="segment.strong">{{ segment.text }}</strong>
                      <span v-else>{{ segment.text }}</span>
                    </template>
                  </p>
                </div>
              </div>
            </div>
            <div v-else-if="entry.type === 'note'" class="work-history__note">
              <div class="work-history__marker flex flex-center">
                <q-icon name="chat_bubble_outline" size="15px" />
              </div>
              <div class="work-history__note-main">
                <div class="work-history__entry-header text-weight-medium">Zwischenstand</div>
                <div class="work-history__note-text">{{ entry.text }}</div>
              </div>
            </div>
            <ToolCallItem
              v-else
              class="work-history__tool"
              :tool="entry.tool"
              :result="entry.result"
              :approval="entry.approval"
              :question="entry.question"
              :process-output="entry.processOutput || processOutputs[entry.tool.id]"
              :active="entry.active"
              technical-only
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
import ApprovalCard from 'components/ApprovalCard.vue'
import MessageAttachments from 'components/MessageAttachments.vue'
import ToolCallItem from 'components/ToolCallItem.vue'
import {
  toolOutputAttachments,
  toolFileActions,
  toolFileAttachment
} from 'src/utils/attachments'
import { agentActivityLabel, formatActivityDuration } from 'src/utils/agent-activity'
import {
  formatReasoningDuration,
  reasoningBlocks,
  reasoningStatusLabel,
  reasoningTitle
} from 'src/utils/reasoning-display'
import { toolIdentity, toolStatus, toolSummary, toolUsageSummary } from 'src/utils/tool-display'

defineOptions({ name: 'WorkHistoryPanel' })

const props = defineProps({
  history: { type: Object, required: true },
  activity: { type: Object, default: null },
  reasoning: { type: Object, default: null },
  processOutputs: { type: Object, default: () => ({}) },
  running: Boolean,
  expanded: Boolean
})
const emit = defineEmits(['open-file', 'open-approval-draft', 'update:expanded'])
const panelExpanded = ref(props.expanded)
const now = ref(Date.now())
let timer = null
const entries = computed(() => [
  ...(props.history?.entries || []),
  ...(props.reasoning
    ? [{ key: 'live-reasoning', type: 'reasoning', reasoning: props.reasoning }]
    : [])
].map((entry) => entry.type === 'reasoning'
  ? { ...entry, blocks: reasoningBlocks(entry.reasoning) }
  : entry))
const items = computed(() => entries.value.filter((entry) => entry.type === 'tool'))
const states = computed(() => items.value.map((item) => ({ item, status: toolStatus(item) })))
const activeItem = computed(() => items.value.find((item) => item.active) || null)
const activeToolName = computed(() => activeItem.value?.tool.name || (
  props.activity?.phase === 'processing_tool' ? props.activity.tool : null
))
const toolUsage = computed(() => toolUsageSummary(items.value).map((tool) => ({
  ...tool,
  active: tool.active || tool.name === activeToolName.value
})))
const approvalItems = computed(() => items.value.filter((item) => item.approval))
const errorItems = computed(() => states.value.filter(({ status }) => (
  ['failed', 'denied'].includes(status.name)
)))
const warningItems = computed(() => states.value.filter(({ status }) => status.name === 'interrupted'))
const reasoningStatus = computed(() => props.activity?.phase === 'waiting_model'
  ? reasoningStatusLabel(props.reasoning)
  : null)
const totalDuration = computed(() => {
  if (!props.history?.startedAt || (!props.running && !props.history?.completedAt)) return null
  const startedAt = new Date(props.history?.startedAt).getTime()
  const completedAt = props.running ? now.value : new Date(props.history?.completedAt).getTime()
  return Number.isFinite(startedAt) && Number.isFinite(completedAt)
    ? formatActivityDuration(startedAt, completedAt)
    : null
})
const activityDisplay = computed(() => {
  if (!props.running) return null
  if (activeItem.value) {
    const identity = toolIdentity(activeItem.value.tool.name)
    return { label: identity.label, summary: toolSummary(activeItem.value.tool) }
  }
  const phase = props.activity?.phase
  if (phase === 'processing_tool' && props.activity?.tool) {
    const identity = toolIdentity(props.activity.tool)
    return { label: identity.label, summary: '' }
  }
  if (reasoningStatus.value) return { label: reasoningStatus.value, summary: '' }
  return { label: agentActivityLabel(props.activity), summary: '' }
})
const headerAriaLabel = computed(() => {
  const parts = [
    activityDisplay.value ? `Aktuell: ${activityDisplay.value.label}` : 'Verlauf der Antworterstellung',
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
const entryDuration = (reasoning) => formatReasoningDuration(reasoning.observedDurationMs)
const outputs = computed(() => items.value.flatMap((item) => {
  const providedFile = toolFileAttachment(item.tool, item.result)
  const attachments = toolOutputAttachments(item.tool, [
    ...(item.result?.attachments || []),
    ...(providedFile ? [providedFile] : [])
  ])
  const fileActions = toolFileActions(item.result)
  return attachments.length || fileActions.length
    ? [{ key: `output-${item.key}`, attachments, fileActions }]
    : []
}))

function updateExpanded(value) {
  panelExpanded.value = value
  emit('update:expanded', value)
}

function updateTimer(running) {
  clearInterval(timer)
  timer = running ? setInterval(() => { now.value = Date.now() }, 1000) : null
}

watch(() => props.running, updateTimer)
onMounted(() => updateTimer(props.running))
onBeforeUnmount(() => clearInterval(timer))
</script>
