<template>
  <div class="agent-activity">
    <div class="agent-activity__visible">
      <span class="agent-activity__label activity-shimmer">{{ label }}</span>
      <span v-if="summary" class="agent-activity__summary">{{ summary }}</span>
      <span class="agent-activity__duration">{{ duration }}</span>
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { agentActivityLabel, formatActivityDuration } from 'src/utils/agent-activity'

defineOptions({ name: 'AgentActivity' })

const props = defineProps({
  activity: { type: Object, required: true },
  label: { type: String, default: '' },
  summary: { type: String, default: '' }
})

const now = ref(Date.now())
const label = computed(() => props.label || agentActivityLabel(props.activity))
const duration = computed(() => formatActivityDuration(props.activity.startedAt, now.value))
let timer = null

onMounted(() => {
  timer = setInterval(() => {
    now.value = Date.now()
  }, 1000)
})

onBeforeUnmount(() => clearInterval(timer))
</script>
