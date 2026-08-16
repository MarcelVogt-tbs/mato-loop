<template>
  <q-expansion-item
    v-if="entry.type === 'context' && !contextHasAttention"
    dense
    class="work-history__context"
    header-class="work-history__context-header"
    expand-icon-class="work-history__context-toggle"
    :duration="expansionDuration"
    :toggle-aria-label="`${contextLabel} ein- oder ausblenden`"
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
      <ToolCallItem
        v-for="item in entry.items"
        :key="item.key"
        :tool="item.tool"
        :result="item.result"
        :approval="item.approval"
        :question="item.question"
        :process-output="item.processOutput || processOutputs[item.tool.id]"
        :active="item.active"
        technical-only
        @open-file="emit('open-file', $event)"
      />
    </div>
  </q-expansion-item>
  <template v-else-if="entry.type === 'context'">
    <ToolCallItem
      v-for="item in entry.items"
      :key="item.key"
      :tool="item.tool"
      :result="item.result"
      :approval="item.approval"
      :question="item.question"
      :process-output="item.processOutput || processOutputs[item.tool.id]"
      :active="item.active"
      technical-only
      @open-file="emit('open-file', $event)"
    />
  </template>
  <q-expansion-item
    v-else-if="entry.type === 'reasoning'"
    dense
    :default-opened="autoExpand"
    class="work-history__reasoning"
    header-class="work-history__reasoning-header"
    expand-icon-class="work-history__reasoning-toggle"
    :duration="expansionDuration"
    :toggle-aria-label="`${entry.presentation.title} ein- oder ausblenden`"
  >
    <template #header>
      <q-item-section>
        <q-item-label lines="1" class="work-history__activity-line">
          <span
            class="work-history__activity-title"
            :class="{ 'activity-shimmer': entry.live }"
          >{{ entry.presentation.title }}</span>
          <span v-if="entry.presentation.summary" class="work-history__activity-summary">
            {{ entry.presentation.summary }}
          </span>
        </q-item-label>
      </q-item-section>
      <q-item-section v-if="entry.presentation.duration" side>
        <span class="work-history__reasoning-duration">{{ entry.presentation.duration }}</span>
      </q-item-section>
    </template>
    <div class="work-history__reasoning-detail">
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
  </q-expansion-item>
  <div v-else-if="entry.type === 'note'" class="work-history__note">
    {{ entry.text }}
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
    :auto-expand="autoExpand"
    technical-only
    @open-file="emit('open-file', $event)"
  />
</template>

<script setup>
import { computed } from 'vue'
import ToolCallItem from 'components/ToolCallItem.vue'
import { toolStatus } from 'src/utils/tool-display'

defineOptions({ name: 'AgentActivityEntry' })

const props = defineProps({
  entry: { type: Object, required: true },
  processOutputs: { type: Object, default: () => ({}) },
  autoExpand: Boolean
})
const emit = defineEmits(['open-file'])
const expansionDuration = globalThis.matchMedia?.('(prefers-reduced-motion: reduce)').matches ? 0 : 180
const contextActive = computed(() => props.entry.items?.some((item) => item.active))
const contextHasAttention = computed(() => props.entry.items?.some((item) => (
  ['approval', 'question', 'failed', 'denied', 'interrupted'].includes(toolStatus(item).name)
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
