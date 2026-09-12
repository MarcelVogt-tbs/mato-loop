<template>
  <q-expansion-item
    v-if="entry.type === 'context' && !contextHasAttention"
    dense
    class="work-history__context"
    header-class="work-history__context-header"
    expand-icon-class="work-history__context-toggle"
    :duration="expansionDuration"
    :toggle-aria-label="`${contextLabel} ein- oder ausblenden`"
    @before-show="emit('resize-start')"
    @before-hide="emit('resize-start')"
    @after-show="emit('resized')"
    @after-hide="emit('resized')"
  >
    <template #header>
      <q-item-section>
        <q-item-label lines="1" class="work-history__activity-line">
          <span
            class="work-history__activity-title"
            :class="{ 'activity-shimmer': contextActive }"
          >
            {{ contextLabel }}
          </span>
          <span class="work-history__activity-summary">{{ contextSummary }}</span>
        </q-item-label>
      </q-item-section>
    </template>
    <div class="work-history__context-items">
      <AgentActivityEntry
        v-for="item in entry.items"
        :key="item.key"
        :entry="item"
        :process-outputs="processOutputs"
        :trusted-image-origins="trustedImageOrigins"
        :read-only="readOnly"
        :show-supplementary="showSupplementary"
        @open-approval-draft="emit('open-approval-draft', $event)"
        @open-file="emit('open-file', $event)"
        @rendered="emit('rendered')"
        @resize-start="emit('resize-start')"
        @resized="emit('resized')"
        @trust-image-origin="emit('trust-image-origin', $event)"
      />
    </div>
  </q-expansion-item>
  <template v-else-if="entry.type === 'context'">
    <AgentActivityEntry
      v-for="item in entry.items"
      :key="item.key"
      :entry="item"
      :process-outputs="processOutputs"
      :trusted-image-origins="trustedImageOrigins"
      :read-only="readOnly"
      :show-supplementary="showSupplementary"
      @open-approval-draft="emit('open-approval-draft', $event)"
      @open-file="emit('open-file', $event)"
      @rendered="emit('rendered')"
      @resize-start="emit('resize-start')"
      @resized="emit('resized')"
      @trust-image-origin="emit('trust-image-origin', $event)"
    />
  </template>
  <q-expansion-item
    v-else-if="entry.type === 'reasoning' || entry.type === 'context-summary'"
    dense
    :default-opened="autoExpand"
    class="work-history__reasoning"
    header-class="work-history__reasoning-header"
    expand-icon-class="work-history__reasoning-toggle"
    :duration="expansionDuration"
    :toggle-aria-label="`${entry.presentation.title} ein- oder ausblenden`"
    @before-show="emit('resize-start')"
    @before-hide="emit('resize-start')"
    @after-show="emit('resized')"
    @after-hide="emit('resized')"
  >
    <template #header>
      <q-item-section>
        <q-item-label lines="1" class="work-history__activity-line">
          <q-icon v-if="entry.warning" name="warning_amber" color="warning" size="18px" />
          <span
            class="work-history__activity-title"
            :class="{ 'activity-shimmer': entry.live, 'text-warning': entry.warning }"
          >{{ entry.presentation.title }}</span>
          <span
            v-if="entry.presentation.summary"
            class="work-history__activity-summary"
            :class="{ 'text-warning': entry.warning }"
          >
            {{ entry.presentation.summary }}
          </span>
        </q-item-label>
      </q-item-section>
      <q-item-section v-if="entry.presentation.duration" side>
        <span class="work-history__reasoning-duration">{{ entry.presentation.duration }}</span>
      </q-item-section>
    </template>
    <div class="work-history__reasoning-detail" :class="{ 'text-warning': entry.warning }">
      <p v-if="entry.type === 'context-summary'" class="work-history__reasoning-block">
        {{ entry.text }}
      </p>
      <template v-else>
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
      </template>
    </div>
  </q-expansion-item>
  <q-item v-else-if="entry.type === 'warning'" dense class="text-warning" role="status">
    <q-item-section avatar>
      <q-icon name="warning_amber" size="18px" />
    </q-item-section>
    <q-item-section>
      <q-item-label class="text-weight-medium">{{ entry.presentation.title }}</q-item-label>
      <q-item-label caption class="text-warning">{{ entry.presentation.summary }}</q-item-label>
    </q-item-section>
  </q-item>
  <div v-else-if="entry.type === 'commentary'" class="q-py-sm">
    <MessageContent
      :content="entry.message.content"
      :research-sources="entry.message.research?.linkedSources || []"
      :streaming="entry.message.streaming"
      :trusted-image-origins="trustedImageOrigins"
      @rendered="emit('rendered')"
      @trust-image-origin="emit('trust-image-origin', $event)"
    />
    <MessageAttachments
      v-if="entry.message.attachments?.length"
      class="q-mt-sm"
      :attachments="entry.message.attachments"
      @open-file="emit('open-file', $event)"
    />
  </div>
  <div v-else-if="entry.type === 'tool'">
    <ToolCallItem
      v-if="!supplementaryOnly"
      class="work-history__tool"
      :tool="entry.tool"
      :result="entry.result"
      :approval="entry.approval"
      :question="entry.question"
      :process-output="entry.processOutput || processOutputs[entry.tool.id]"
      :active="entry.active"
      :auto-expand="autoExpand"
      technical-only
      @open-file="emit('open-file', $event)"
      @resize-start="emit('resize-start')"
      @resized="emit('resized')"
    />
    <template v-if="showSupplementary || supplementaryOnly">
      <SubagentRows :children="entry.subagents || []" />
      <ApprovalCard
        v-if="entry.approval && !readOnly"
        :approval="entry.approval"
        class="work-history__attention"
        @open-approval-draft="emit('open-approval-draft', $event)"
      />
      <div
        v-if="entry.output?.attachments.length || entry.output?.fileActions.length"
        class="work-history__outputs"
      >
        <div class="tool-call__section-label">Ausgaben</div>
        <MessageAttachments
          v-if="entry.output.attachments.length"
          :attachments="entry.output.attachments"
          @open-file="emit('open-file', $event)"
        />
        <div v-if="entry.output.fileActions.length" class="work-history__file-outputs">
          <div
            v-for="action in entry.output.fileActions"
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
    </template>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import ApprovalCard from 'components/ApprovalCard.vue'
import MessageAttachments from 'components/MessageAttachments.vue'
import MessageContent from 'components/MessageContent.vue'
import SubagentRows from 'components/SubagentRows.vue'
import ToolCallItem from 'components/ToolCallItem.vue'
import { toolStatus } from 'src/utils/tool-display'

defineOptions({ name: 'AgentActivityEntry' })

const props = defineProps({
  entry: { type: Object, required: true },
  processOutputs: { type: Object, default: () => ({}) },
  trustedImageOrigins: { type: Array, default: () => [] },
  readOnly: Boolean,
  showSupplementary: Boolean,
  supplementaryOnly: Boolean,
  autoExpand: Boolean
})
const emit = defineEmits(['open-approval-draft', 'open-file', 'rendered', 'resize-start', 'resized', 'trust-image-origin'])
const expansionDuration = globalThis.matchMedia?.('(prefers-reduced-motion: reduce)').matches ? 0 : 180
const contextActive = computed(() => props.entry.items?.some((item) => item.active))
const contextHasAttention = computed(() => props.entry.items?.some((item) => (
  ['approval', 'question', 'failed', 'denied', 'interrupted'].includes(toolStatus(item).name)
  || item.subagents?.length
  || item.output?.attachments.length
  || item.output?.fileActions.length
)))
const contextLabel = computed(() => {
  if (contextActive.value) return 'Kontext wird gesammelt'
  if (props.entry.items?.some((item) => toolStatus(item).name === 'pending')) return 'Kontext ausstehend'
  return 'Kontext gesammelt'
})
const contextSummary = computed(() => {
  const reads = props.entry.items?.filter((item) => ['read', 'read_file'].includes(item.tool.name)).length
  const searches = props.entry.items?.filter((item) => ['glob', 'grep'].includes(item.tool.name)).length
  const lists = props.entry.items?.filter((item) => item.tool.name === 'list_directory').length
  return [
    reads ? `${reads} ${reads === 1 ? 'Lesevorgang' : 'Lesevorgänge'}` : '',
    searches ? `${searches} ${searches === 1 ? 'Suche' : 'Suchen'}` : '',
    lists ? `${lists} ${lists === 1 ? 'Auflistung' : 'Auflistungen'}` : ''
  ].filter(Boolean).join(' · ')
})
</script>
