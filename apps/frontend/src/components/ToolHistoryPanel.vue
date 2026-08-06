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
        <q-item-section class="tool-history__identity" :aria-label="headerAriaLabel">
          <div class="tool-history__identity-content row items-center no-wrap">
            <div v-if="activityDisplay" class="tool-history__activity row items-center no-wrap">
              <q-icon
                :name="activityDisplay.icon"
                class="tool-history__icon tool-history__activity-icon"
                size="18px"
              />
              <span class="tool-history__activity-label text-weight-medium ellipsis">
                {{ activityDisplay.label }}
              </span>
            </div>
            <div class="tool-history__summary row items-center no-wrap">
              <q-icon name="build" class="tool-history__icon" size="18px" />
              <span class="text-weight-medium">{{ items.length }}</span>
            </div>
            <span
              v-if="errorItems.length"
              class="tool-history__message-count tool-history__message-count--error row items-center no-wrap"
              role="img"
              :aria-label="errorLabel"
            >
              <q-icon name="error" color="negative" size="18px" />
              <span>{{ errorItems.length }}</span>
            </span>
            <span
              v-if="warningItems.length"
              class="tool-history__message-count tool-history__message-count--warning row items-center no-wrap"
              role="img"
              :aria-label="warningLabel"
            >
              <q-icon name="warning" color="warning" size="18px" />
              <span>{{ warningItems.length }}</span>
            </span>
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
import { agentActivityLabel } from 'src/utils/agent-activity'
import { toolIdentity, toolStatus } from 'src/utils/tool-display'

defineOptions({ name: 'ToolHistoryPanel' })

const props = defineProps({
  history: { type: Object, required: true },
  activity: { type: Object, default: null },
  running: Boolean
})
const emit = defineEmits(['open-file'])
const expanded = ref(false)
const items = computed(() => props.history?.items || [])
const states = computed(() => items.value.map((item) => ({ item, status: toolStatus(item) })))
const activeItem = computed(() => items.value.find((item) => item.active) || null)
const approvalItems = computed(() => items.value.filter((item) => item.approval))
const errorItems = computed(() => states.value.filter(({ status }) => (
  ['failed', 'denied'].includes(status.name)
)))
const warningItems = computed(() => states.value.filter(({ status }) => status.name === 'interrupted'))
const currentTurn = computed(() => items.value.some((item) => item.current))
const activityDisplay = computed(() => {
  if (!props.running || !currentTurn.value) return null
  if (activeItem.value) {
    const identity = toolIdentity(activeItem.value.tool.name)
    return { icon: identity.icon, label: identity.label }
  }
  const phase = props.activity?.phase
  if (!phase || ['streaming', 'finalizing'].includes(phase)) return null
  if (phase === 'processing_tool' && props.activity?.tool) {
    const identity = toolIdentity(props.activity.tool)
    return { icon: identity.icon, label: identity.label }
  }
  const icons = {
    preparing_tools: 'pending_actions',
    waiting_approval: 'pending_actions'
  }
  return icons[phase] ? { icon: icons[phase], label: agentActivityLabel(props.activity) } : null
})
const headerAriaLabel = computed(() => {
  const parts = [
    ...(activityDisplay.value ? [`Aktuell: ${activityDisplay.value.label}`] : []),
    `${items.value.length} ${items.value.length === 1 ? 'Tool-Aufruf' : 'Tool-Aufrufe'}`,
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
