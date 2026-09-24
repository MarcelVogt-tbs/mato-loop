<template>
  <span
    v-if="duration"
    class="row inline items-center no-wrap text-weight-regular"
    role="timer"
    :aria-label="`Antwortdauer ohne Wartezeit auf dich: ${duration}`"
  >
    <span>{{ duration }}</span>
    <q-tooltip>Antwortdauer ohne Wartezeit auf dich</q-tooltip>
  </span>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { activityDurationMs, formatActivityDuration } from 'src/utils/agent-activity'

defineOptions({ name: 'TurnDuration' })

const props = defineProps({
  history: { type: Object, default: null },
  running: Boolean
})
const now = ref(Date.now())
let timer = null
const duration = computed(() => {
  const milliseconds = activityDurationMs(props.history, props.running, now.value)
  return milliseconds === null ? null : formatActivityDuration(milliseconds)
})

function syncTimer() {
  now.value = Date.now()
  clearInterval(timer)
  timer = props.running && props.history?.activeDurationUpdatedAt
    ? setInterval(() => { now.value = Date.now() }, 1000)
    : null
}

watch([() => props.running, () => props.history?.activeDurationUpdatedAt], syncTimer)
onMounted(syncTimer)
onBeforeUnmount(() => clearInterval(timer))
</script>
