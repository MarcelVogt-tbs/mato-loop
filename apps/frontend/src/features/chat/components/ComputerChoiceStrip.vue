<template>
  <div v-if="computers.length || agents.loadFailed">
    <div class="row items-center q-gutter-xs" role="group" aria-label="Computer auswählen">
      <q-btn
        v-for="computer in computers" :key="computer.id"
        rounded dense unelevated no-caps no-wrap size="sm"
        class="col-auto q-px-sm text-weight-regular"
        :class="session.preferredExecutionAgentId === computer.id ? 'app-surface-tonal text-primary' : 'computer-choice app-muted'"
        :disable="disabled"
        :aria-pressed="session.preferredExecutionAgentId === computer.id"
        :aria-label="`${computer.alias}: ${computer.connected ? 'Online' : 'Offline'}. ${session.preferredExecutionAgentId === computer.id ? 'Computer abwählen' : 'Computer auswählen'}`"
        @click="selectComputer(computer)"
      >
        <q-icon :name="computer.connected ? 'computer' : 'desktop_access_disabled'" :color="computer.connected ? 'positive' : undefined" size="16px" class="q-mr-xs" />
        <span class="text-caption">{{ computer.alias }}</span>
      </q-btn>
    </div>
    <div v-if="agents.loadFailed" class="text-caption text-warning q-mt-xs" role="status">Computerstatus konnte nicht aktualisiert werden.</div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useExecutionAgentStore } from 'stores/execution-agents'

const props = defineProps({ session: { type: Object, required: true }, disabled: Boolean })
const emit = defineEmits(['update'])
const agents = useExecutionAgentStore()
const computers = computed(() => {
  const items = [...agents.items]
  const selectedId = props.session.preferredExecutionAgentId
  if (agents.loaded && selectedId && !agents.byId(selectedId)) {
    items.push({ id: selectedId, alias: 'Nicht verfügbarer Computer', connected: false })
  }
  return items.sort((left, right) => Number(Boolean(right.connected)) - Number(Boolean(left.connected)))
})

function selectComputer(computer) {
  if (props.disabled) return
  const selected = props.session.preferredExecutionAgentId !== computer.id
  emit('update', {
    preferred_execution_agent_id: selected ? computer.id : null,
    current_directory: selected ? computer.homeDirectory || null : null
  })
}
</script>

<style scoped>
.computer-choice {
  background: var(--app-surface);
}
</style>
