<template>
  <q-expansion-item
    v-model="expanded"
    dense
    class="tool-call"
    :class="`tool-call--${status.name}`"
    header-class="tool-call__header"
    :duration="180"
  >
    <template #header>
      <q-item-section avatar class="tool-call__avatar-section">
        <q-avatar rounded size="32px" class="tool-call__avatar">
          <q-icon :name="identity.icon" size="19px" />
        </q-avatar>
      </q-item-section>

      <q-item-section class="tool-call__identity">
        <q-item-label class="text-weight-medium">{{ identity.label }}</q-item-label>
        <q-item-label caption lines="1" class="tool-call__summary">{{ summary }}</q-item-label>
      </q-item-section>

      <q-item-section side class="tool-call__state">
        <div
          class="tool-call__status row items-center no-wrap q-gutter-xs"
          :class="`tool-call__status--${status.name}`"
        >
          <q-spinner v-if="status.name === 'running'" size="16px" />
          <q-icon v-else :name="status.icon" size="17px" />
          <span class="tool-call__status-label">{{ status.label }}</span>
          <q-icon :name="expanded ? 'expand_less' : 'expand_more'" size="20px" class="app-muted" />
        </div>
      </q-item-section>
    </template>

    <div class="tool-call__body">
      <div v-if="argumentsText" class="tool-call__section">
        <div class="tool-call__section-label">Argumente</div>
        <pre class="tool-call__details">{{ argumentsText }}</pre>
      </div>

      <q-banner v-if="resultData.error" dense rounded class="tool-call__error bg-negative text-white">
        <template #avatar><q-icon name="error_outline" /></template>
        {{ resultData.errorMessage }}
      </q-banner>

      <MessageAttachments
        v-if="attachments.length"
        class="tool-call__attachments"
        :attachments="attachments"
        label="Anhänge"
      />

      <q-expansion-item
        v-if="resultData.text"
        v-model="resultExpanded"
        dense
        dense-toggle
        icon="terminal"
        label="Ergebnis anzeigen"
        class="tool-call__result-expansion"
      >
        <pre class="tool-call__details tool-call__result">{{ resultData.text }}</pre>
      </q-expansion-item>

      <ApprovalCard v-if="approval" :approval="approval" class="tool-call__approval" />
    </div>

  </q-expansion-item>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import ApprovalCard from 'components/ApprovalCard.vue'
import MessageAttachments from 'components/MessageAttachments.vue'
import { supportedAttachments } from 'src/utils/attachments'
import {
  formatToolArguments,
  parseToolResult,
  toolIdentity,
  toolStatus,
  toolSummary
} from 'src/utils/tool-display'

defineOptions({ name: 'ToolCallItem' })

const props = defineProps({
  tool: { type: Object, required: true },
  result: { type: Object, default: null },
  approval: { type: Object, default: null },
  active: Boolean
})

const identity = computed(() => toolIdentity(props.tool.name))
const summary = computed(() => toolSummary(props.tool))
const argumentsText = computed(() => formatToolArguments(props.tool.arguments))
const resultData = computed(() => parseToolResult(props.result?.content, props.tool.name))
const attachments = computed(() => supportedAttachments(props.result?.attachments))
const status = computed(() => toolStatus(props))
const expanded = ref(attachments.value.length > 0
  || ['approval', 'failed', 'denied', 'interrupted'].includes(status.value.name))
const resultExpanded = ref(Boolean(resultData.value.error))
watch(() => status.value.name, (name) => {
  if (['approval', 'failed', 'denied', 'interrupted'].includes(name)) expanded.value = true
})
watch(() => attachments.value.length, (count) => {
  if (count) expanded.value = true
})
</script>
