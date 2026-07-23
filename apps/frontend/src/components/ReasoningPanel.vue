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
          <q-avatar rounded size="30px" class="reasoning-panel__avatar">
            <q-spinner-dots v-if="running" color="primary" size="20px" class="reasoning-panel__spinner" />
            <q-icon
              v-if="running"
              name="auto_awesome"
              color="primary"
              size="17px"
              class="reasoning-panel__static-icon"
            />
            <q-icon v-else name="psychology_alt" color="primary" size="18px" />
          </q-avatar>
        </q-item-section>
        <q-item-section class="reasoning-panel__identity">
          <q-item-label class="text-weight-medium">{{ title }}</q-item-label>
        </q-item-section>
        <q-item-section side>
          <div class="reasoning-panel__meta row items-center no-wrap q-gutter-xs">
            <span v-if="duration">{{ duration }}</span>
            <span v-if="reasoning.reasoningTokens">{{ reasoning.reasoningTokens }} Tok.</span>
            <q-icon :name="expanded ? 'expand_less' : 'expand_more'" size="20px" />
          </div>
        </q-item-section>
      </template>
      <div class="reasoning-panel__body">
        <pre class="reasoning-panel__content">{{ reasoning.text }}</pre>
      </div>
    </q-expansion-item>
    <span v-if="running" class="q-sr-only" role="status" aria-live="polite" aria-atomic="true">
      {{ title }}
    </span>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { agentActivityLabel, formatActivityDuration } from 'src/utils/agent-activity'
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
let timer = null
const activityText = computed(() => props.activity?.phase === 'waiting_model'
  ? 'Modell denkt nach'
  : props.activity ? agentActivityLabel(props.activity) : '')
const title = computed(() => reasoningTitle(props.reasoning, props.running, activityText.value))
const duration = computed(() => props.running && props.activity?.startedAt
  ? formatActivityDuration(props.activity.startedAt, now.value)
  : formatReasoningDuration(props.reasoning.observedDurationMs))

onMounted(() => {
  if (props.running) timer = setInterval(() => { now.value = Date.now() }, 1000)
})
onBeforeUnmount(() => clearInterval(timer))
</script>
