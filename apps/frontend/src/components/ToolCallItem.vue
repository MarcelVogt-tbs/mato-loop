<template>
  <div class="tool-call" :class="`tool-call--${status.name}`">
    <q-expansion-item
      v-model="expanded"
      dense
      header-class="tool-call__header"
      expand-icon-class="tool-call__toggle"
      :duration="expansionDuration"
      :toggle-aria-label="`Technische Details für ${identity.label} ein- oder ausblenden`"
    >
      <template #header>
        <q-item-section class="tool-call__identity">
          <q-item-label lines="1" class="tool-call__line">
            <span
              class="tool-call__title"
              :class="{ 'activity-shimmer': status.name === 'running' }"
            >{{ identity.label }}</span>
            <span v-if="summary" class="tool-call__summary">{{ summary }}</span>
          </q-item-label>
        </q-item-section>

        <q-item-section
          v-if="exceptionalStatus || (!technicalOnly && fileActions.length)"
          side
          class="tool-call__state"
        >
          <div class="row items-center no-wrap q-gutter-xs">
            <div v-if="!technicalOnly && fileActions.length" class="tool-call__file-actions row items-center no-wrap">
              <div v-for="action in fileActions" :key="`${action.rootIdentity}:${action.path}`" class="row no-wrap">
                <q-btn
                  flat
                  round
                  dense
                  size="sm"
                  color="primary"
                  icon="visibility"
                  :aria-label="`${action.filename} ansehen`"
                  @click.stop="emit('open-file', action)"
                  @keydown.stop
                >
                  <q-tooltip>Datei ansehen</q-tooltip>
                </q-btn>
                <q-btn
                  flat
                  round
                  dense
                  size="sm"
                  color="primary"
                  icon="download"
                  :href="action.downloadUrl"
                  :download="action.filename"
                  :aria-label="`${action.filename} herunterladen`"
                  @click.stop
                  @keydown.stop
                >
                  <q-tooltip>Datei herunterladen</q-tooltip>
                </q-btn>
              </div>
            </div>
            <div
              v-if="exceptionalStatus"
              class="tool-call__status row items-center no-wrap q-gutter-xs"
              :class="`tool-call__status--${status.name}`"
            >
              <span class="tool-call__status-label">{{ status.label }}</span>
            </div>
          </div>
        </q-item-section>
      </template>

      <div class="tool-call__body">
        <ToolPresentation
          v-if="!approval && presentation.items.length"
          :items="presentation.items"
          embedded
        />

        <div
          v-if="argumentsDisplay.text"
          class="tool-call__section"
          :class="{ 'q-mt-md': !approval && presentation.items.length }"
        >
          <div class="tool-call__section-label">Technische Argumente</div>
          <pre class="tool-call__details">{{ showFullArguments ? argumentsDisplay.fullText : argumentsDisplay.text }}</pre>
          <q-btn
            v-if="argumentsDisplay.truncated"
            flat
            dense
            no-caps
            color="primary"
            :label="showFullArguments ? 'Weniger anzeigen' : 'Vollständig anzeigen'"
            class="q-mt-xs"
            @click="showFullArguments = !showFullArguments"
          />
        </div>

        <q-banner v-if="resultData.error" dense rounded class="tool-call__error bg-negative text-white">
          <template #avatar><q-icon name="error_outline" /></template>
          {{ resultData.errorMessage }}
        </q-banner>

        <div v-if="resultData.text" class="tool-call__section q-mt-md">
          <div class="tool-call__section-label">Ergebnis</div>
          <pre class="tool-call__details tool-call__result">{{ showFullResult ? resultData.fullText : resultData.text }}</pre>
          <q-btn
            v-if="resultData.textTruncated"
            flat
            dense
            no-caps
            color="primary"
            :label="showFullResult ? 'Weniger anzeigen' : 'Vollständig anzeigen'"
            class="q-mt-xs"
            @click="showFullResult = !showFullResult"
          />
        </div>

        <div v-if="resultData.stdout" class="tool-call__section q-mt-md">
          <div class="tool-call__section-label">Standardausgabe</div>
          <pre class="tool-call__details tool-call__result">{{ resultData.stdout }}</pre>
        </div>

        <div v-if="resultData.stderr" class="tool-call__section q-mt-md">
          <div class="tool-call__section-label">Fehlerausgabe</div>
          <pre class="tool-call__details tool-call__result">{{ resultData.stderr }}</pre>
        </div>

        <div v-if="resultData.truncated" class="text-caption app-muted q-mt-sm">
          Die Prozessausgabe wurde gekürzt.
        </div>

        <ApprovalCard v-if="!technicalOnly && approval" :approval="approval" class="tool-call__approval" />
      </div>
    </q-expansion-item>

    <MessageAttachments
      v-if="!technicalOnly && attachments.length"
      class="tool-call__outputs"
      :attachments="attachments"
      label="Ausgabe"
    />
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import ApprovalCard from 'components/ApprovalCard.vue'
import MessageAttachments from 'components/MessageAttachments.vue'
import ToolPresentation from 'components/ToolPresentation.vue'
import { toolFileActions, toolFileAttachment, toolOutputAttachments } from 'src/utils/attachments'
import { useFileMediaSettingsStore } from 'stores/file-media-settings'
import {
  parseToolResult,
  toolArgumentsDisplay,
  toolIdentity,
  toolPresentation,
  toolStatus
} from 'src/utils/tool-display'

defineOptions({ name: 'ToolCallItem' })

const props = defineProps({
  tool: { type: Object, required: true },
  result: { type: Object, default: null },
  approval: { type: Object, default: null },
  question: { type: Object, default: null },
  processOutput: { type: Object, default: null },
  active: Boolean,
  autoExpand: Boolean,
  technicalOnly: Boolean
})
const emit = defineEmits(['open-file'])
const fileMediaSettings = useFileMediaSettingsStore()

const identity = computed(() => toolIdentity(props.tool.name))
const presentation = computed(() => toolPresentation(
  props.tool,
  props.approval?.details?.displayItems
))
const summary = computed(() => presentation.value.summary)
const argumentsDisplay = computed(() => toolArgumentsDisplay(props.tool.arguments, props.tool.name))
const resultData = computed(() => ({
  ...parseToolResult(props.result?.content, props.tool.name),
  ...(props.processOutput || {})
}))
const attachments = computed(() => {
  const providedFile = toolFileAttachment(
    props.tool,
    props.result,
    fileMediaSettings.maxImagePreviewBytes
  )
  return toolOutputAttachments(props.tool, [
    ...(props.result?.attachments || []),
    ...(providedFile ? [providedFile] : [])
  ])
})
const fileActions = computed(() => toolFileActions(props.result))
const status = computed(() => toolStatus(props))
const exceptionalStatus = computed(() => (
  ['approval', 'question', 'failed', 'denied', 'interrupted'].includes(status.value.name)
))
const expansionDuration = globalThis.matchMedia?.('(prefers-reduced-motion: reduce)').matches ? 0 : 180
const expanded = ref(props.autoExpand || ['approval', 'question', 'failed', 'denied', 'interrupted'].includes(status.value.name))
const showFullArguments = ref(false)
const showFullResult = ref(false)
watch(() => status.value.name, (name) => {
  if (['approval', 'question', 'failed', 'denied', 'interrupted'].includes(name)) expanded.value = true
})
watch(() => props.autoExpand, (value) => {
  if (value) expanded.value = true
})
</script>
