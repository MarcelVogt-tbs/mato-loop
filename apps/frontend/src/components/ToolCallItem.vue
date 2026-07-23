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

      <div v-if="attachments.length" class="tool-call__attachments">
        <div class="tool-call__section-label">Bildanhänge</div>
        <div class="attachment-grid">
          <button
            v-for="attachment in attachments"
            :key="attachment.id"
            type="button"
            class="attachment-preview app-surface"
            :disabled="!attachment.safeUrl || loadErrors[attachment.id]"
            :aria-label="`${attachment.filename || 'Bild'} vergrößern`"
            @click="openAttachment(attachment)"
          >
            <img
              v-if="attachment.safeUrl && !loadErrors[attachment.id]"
              :src="attachment.safeUrl"
              :alt="attachment.filename || 'Vom Tool erzeugtes Bild'"
              loading="lazy"
              decoding="async"
              @error="loadErrors[attachment.id] = true"
            >
            <div v-else class="attachment-preview__error">
              <q-icon name="broken_image" size="28px" />
              <span>Bild konnte nicht geladen werden.</span>
            </div>
            <span class="attachment-preview__meta">
              <strong>{{ attachment.filename || 'Bild' }}</strong>
              <small>{{ attachmentMetadata(attachment) }}</small>
            </span>
          </button>
        </div>
      </div>

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

    <q-dialog v-model="attachmentDialog" :maximized="$q.screen.lt.sm">
      <q-card v-if="selectedAttachment" class="attachment-dialog app-surface">
        <q-card-section class="row items-start no-wrap q-gutter-md">
          <div class="col">
            <div class="text-h6 ellipsis">{{ selectedAttachment.filename || 'Bildvorschau' }}</div>
            <div class="text-caption app-muted">{{ attachmentMetadata(selectedAttachment) }}</div>
          </div>
          <q-btn flat round dense icon="close" aria-label="Bildvorschau schließen" v-close-popup />
        </q-card-section>
        <q-separator />
        <q-card-section class="attachment-dialog__body">
          <img
            :src="selectedAttachment.safeUrl"
            :alt="selectedAttachment.filename || 'Vom Tool erzeugtes Bild in Großansicht'"
            @error="closeFailedAttachment"
          >
        </q-card-section>
      </q-card>
    </q-dialog>
  </q-expansion-item>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { useQuasar } from 'quasar'
import ApprovalCard from 'components/ApprovalCard.vue'
import { attachmentMetadata, imageAttachments } from 'src/utils/attachments'
import {
  formatToolArguments,
  parseToolResult,
  toolIdentity,
  toolStatus,
  toolSummary
} from 'src/utils/tool-display'

defineOptions({ name: 'ToolCallItem' })

const $q = useQuasar()

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
const attachments = computed(() => imageAttachments(props.result?.attachments))
const status = computed(() => toolStatus(props))
const expanded = ref(attachments.value.length > 0
  || ['approval', 'failed', 'denied', 'interrupted'].includes(status.value.name))
const resultExpanded = ref(Boolean(resultData.value.error))
const loadErrors = reactive({})
const attachmentDialog = ref(false)
const selectedAttachment = ref(null)

function openAttachment(attachment) {
  if (!attachment.safeUrl || loadErrors[attachment.id]) return
  selectedAttachment.value = attachment
  attachmentDialog.value = true
}

function closeFailedAttachment() {
  if (selectedAttachment.value) loadErrors[selectedAttachment.value.id] = true
  attachmentDialog.value = false
}

watch(() => status.value.name, (name) => {
  if (['approval', 'failed', 'denied', 'interrupted'].includes(name)) expanded.value = true
})
watch(() => attachments.value.length, (count) => {
  if (count) expanded.value = true
})
</script>
