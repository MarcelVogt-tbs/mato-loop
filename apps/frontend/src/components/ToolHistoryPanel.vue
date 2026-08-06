<template>
  <div class="tool-history">
    <q-expansion-item
      v-model="expanded"
      dense
      class="tool-history__panel"
      header-class="tool-history__header"
      :duration="180"
    >
      <template #header>
        <q-item-section avatar class="tool-history__avatar-section">
          <q-icon :name="headerIcon" class="tool-history__icon" size="18px" />
        </q-item-section>
        <q-item-section class="tool-history__identity" :aria-label="headerAriaLabel">
          <div class="tool-history__identity-content row items-center no-wrap q-gutter-sm">
            <span class="tool-history__label text-weight-medium ellipsis">
              {{ headerTool ? toolIdentity(headerTool.tool.name).label : items.length }}
            </span>
            <span
              v-if="problemItems.length"
              class="tool-history__problem-count row items-center no-wrap q-gutter-xs"
              role="img"
              :aria-label="problemLabel"
            >
              <q-icon name="error" color="negative" size="18px" />
              <span>{{ problemItems.length }}</span>
            </span>
            <q-spinner v-if="aggregateStatus.name === 'running'" color="primary" size="18px" />
            <q-icon
              v-else-if="['approval', 'pending'].includes(aggregateStatus.name)"
              :name="aggregateStatus.icon"
              :color="aggregateStatus.color"
              size="18px"
            />
          </div>
        </q-item-section>
      </template>

      <div class="tool-history__calls">
        <ToolCallItem
          v-for="item in items"
          :key="item.key"
          v-bind="item"
          technical-only
          @open-file="emit('open-file', $event)"
        />
      </div>
    </q-expansion-item>

    <ApprovalCard
      v-for="item in approvalItems"
      :key="`approval-${item.approval.id}`"
      :approval="item.approval"
      class="tool-history__attention"
    />

    <div v-if="outputs.length" class="tool-history__outputs">
      <div class="tool-call__section-label">Ausgaben</div>
      <div v-for="output in outputs" :key="output.key" class="tool-history__output">
        <MessageAttachments
          v-if="output.attachments.length"
          :attachments="output.attachments"
        />
        <div v-if="output.fileActions.length" class="tool-history__file-outputs">
          <div
            v-for="action in output.fileActions"
            :key="`${action.rootIdentity}:${action.path}`"
            class="tool-history__file-output app-surface"
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
import { computed, ref } from 'vue'
import ApprovalCard from 'components/ApprovalCard.vue'
import MessageAttachments from 'components/MessageAttachments.vue'
import ToolCallItem from 'components/ToolCallItem.vue'
import {
  supportedAttachments,
  toolFileActions,
  toolFileAttachment
} from 'src/utils/attachments'
import { toolIdentity, toolStatus } from 'src/utils/tool-display'

defineOptions({ name: 'ToolHistoryPanel' })

const props = defineProps({
  history: { type: Object, required: true }
})
const emit = defineEmits(['open-file'])
const expanded = ref(false)
const items = computed(() => props.history?.items || [])
const states = computed(() => items.value.map((item) => ({ item, status: toolStatus(item) })))
const activeItem = computed(() => items.value.find((item) => item.active) || null)
const approvalItems = computed(() => items.value.filter((item) => item.approval))
const problemItems = computed(() => states.value.filter(({ status }) => (
  ['failed', 'denied', 'interrupted'].includes(status.name)
)))
const headerTool = computed(() => activeItem.value || (items.value.length === 1 ? items.value[0] : null))
const headerIcon = computed(() => headerTool.value
  ? toolIdentity(headerTool.value.tool.name).icon
  : 'history'
)
const aggregateStatus = computed(() => {
  if (activeItem.value) return { name: 'running', label: 'Wird ausgeführt', icon: null, color: 'primary' }
  if (approvalItems.value.length) {
    return { name: 'approval', label: 'Freigabe erforderlich', icon: 'pending_actions', color: 'warning' }
  }
  if (problemItems.value.length) {
    return {
      name: 'warning',
      label: `${problemItems.value.length} nicht erfolgreich`,
      icon: 'error',
      color: 'negative'
    }
  }
  if (states.value.length && states.value.every(({ status }) => status.name === 'completed')) {
    return { name: 'completed', label: 'Erledigt', icon: 'check_circle', color: 'positive' }
  }
  return { name: 'pending', label: 'Ausstehend', icon: 'schedule', color: 'grey-7' }
})
const headerAriaLabel = computed(() => {
  const subject = headerTool.value
    ? toolIdentity(headerTool.value.tool.name).label
    : `${items.value.length} Tool-Aufrufe`
  const problems = problemItems.value.length && aggregateStatus.value.name !== 'warning'
    ? `, ${problemLabel.value}`
    : ''
  return `${subject}: ${aggregateStatus.value.label}${problems}`
})
const problemLabel = computed(() => (
  `${problemItems.value.length} ${problemItems.value.length === 1 ? 'Tool-Aufruf war' : 'Tool-Aufrufe waren'} nicht erfolgreich.`
))
const outputs = computed(() => items.value.flatMap((item) => {
  const providedFile = toolFileAttachment(item.tool, item.result)
  const attachments = supportedAttachments([
    ...(item.result?.attachments || []),
    ...(providedFile ? [providedFile] : [])
  ])
  const fileActions = toolFileActions(item.result)
  return attachments.length || fileActions.length
    ? [{ key: `output-${item.key}`, attachments, fileActions }]
    : []
}))
</script>
