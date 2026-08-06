<template>
  <div class="reasoning-panel-wrap">
    <q-expansion-item
      :model-value="expanded"
      dense
      class="reasoning-panel"
      :class="{ 'reasoning-panel--running': running }"
      header-class="reasoning-panel__header"
      :duration="180"
      @update:model-value="$emit('update:expanded', $event)"
    >
      <template #header>
        <q-item-section avatar class="reasoning-panel__avatar-section">
          <q-avatar rounded size="24px" class="reasoning-panel__avatar">
            <q-spinner-dots v-if="running" color="primary" size="17px" class="reasoning-panel__spinner" />
            <q-icon
              v-if="running"
              name="auto_awesome"
              color="primary"
              size="15px"
              class="reasoning-panel__static-icon"
            />
            <q-icon v-else name="psychology_alt" color="primary" size="16px" />
          </q-avatar>
        </q-item-section>
        <q-item-section class="reasoning-panel__identity">
          <q-item-label class="text-weight-medium">{{ title }}</q-item-label>
        </q-item-section>
        <q-item-section side>
          <div class="reasoning-panel__meta row items-center no-wrap q-gutter-xs">
            <span v-if="duration">{{ duration }}</span>
            <span v-if="reasoning.reasoningTokens">{{ reasoning.reasoningTokens }} Tok.</span>
          </div>
        </q-item-section>
      </template>
      <div class="reasoning-panel__body">
        <pre
          v-for="(entry, index) in entries"
          :key="index"
          class="reasoning-panel__content"
        >{{ entry.text }}</pre>
      </div>
    </q-expansion-item>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { formatActivityDuration } from 'src/utils/agent-activity'
import { formatReasoningDuration, reasoningTitle } from 'src/utils/reasoning-display'

defineOptions({ name: 'ReasoningPanel' })

const props = defineProps({
  reasoning: { type: Object, required: true },
  activity: { type: Object, default: null },
  running: Boolean,
  expanded: Boolean
})
defineEmits(['update:expanded'])

const now = ref(Date.now())
const liveStartedAt = ref(props.running ? props.activity?.startedAt : null)
let timer = null
const title = computed(() => reasoningTitle(props.reasoning, props.running, 'Denkprozess'))
const duration = computed(() => props.running && liveStartedAt.value
  ? formatActivityDuration(liveStartedAt.value, now.value)
  : formatReasoningDuration(props.reasoning.observedDurationMs))
const entries = computed(() => props.reasoning.entries?.length
  ? props.reasoning.entries
  : [props.reasoning]
)

onMounted(() => {
  if (props.running) timer = setInterval(() => { now.value = Date.now() }, 1000)
})
onBeforeUnmount(() => clearInterval(timer))
</script>
