<template>
  <div class="agent-activity">
    <div class="agent-activity__visible">
      <q-avatar rounded size="30px" class="agent-activity__avatar" aria-hidden="true">
        <q-spinner-dots color="primary" size="21px" class="agent-activity__spinner" />
        <q-icon name="auto_awesome" color="primary" size="17px" class="agent-activity__static-icon" />
      </q-avatar>
      <span class="agent-activity__label">{{ label }}</span>
      <span class="agent-activity__duration">{{ duration }}</span>
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { agentActivityLabel, formatActivityDuration } from 'src/utils/agent-activity'

defineOptions({ name: 'AgentActivity' })

const props = defineProps({
  activity: { type: Object, required: true }
})

const now = ref(Date.now())
const label = computed(() => agentActivityLabel(props.activity))
const duration = computed(() => formatActivityDuration(props.activity.startedAt, now.value))
let timer = null

onMounted(() => {
  timer = setInterval(() => {
    now.value = Date.now()
  }, 1000)
})

onBeforeUnmount(() => {
  clearInterval(timer)
})
</script>
