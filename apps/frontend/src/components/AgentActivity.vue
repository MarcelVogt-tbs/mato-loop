<template>
  <div class="agent-activity">
    <div class="agent-activity__visible" aria-hidden="true">
      <q-avatar rounded size="30px" class="agent-activity__avatar">
        <q-spinner-dots color="primary" size="21px" class="agent-activity__spinner" />
        <q-icon name="auto_awesome" color="primary" size="17px" class="agent-activity__static-icon" />
      </q-avatar>
      <span class="agent-activity__label">{{ label }}</span>
      <span class="agent-activity__duration">{{ duration }}</span>
    </div>
    <span class="q-sr-only" role="status" aria-live="polite" aria-atomic="true">
      {{ announcedLabel }}
    </span>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { agentActivityLabel, formatActivityDuration } from 'src/utils/agent-activity'

defineOptions({ name: 'AgentActivity' })

const props = defineProps({
  activity: { type: Object, required: true }
})

const now = ref(Date.now())
const label = computed(() => agentActivityLabel(props.activity))
const duration = computed(() => formatActivityDuration(props.activity.startedAt, now.value))
const announcedLabel = ref(label.value)
let timer = null
let announcementTimer = null

watch(label, (value) => {
  clearTimeout(announcementTimer)
  announcementTimer = setTimeout(() => {
    announcedLabel.value = value
  }, 250)
})

onMounted(() => {
  timer = setInterval(() => {
    now.value = Date.now()
  }, 1000)
})

onBeforeUnmount(() => {
  clearInterval(timer)
  clearTimeout(announcementTimer)
})
</script>
