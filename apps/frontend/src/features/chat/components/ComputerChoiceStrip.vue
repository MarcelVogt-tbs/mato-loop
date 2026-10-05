<template>
  <div v-if="computers.length || agents.loadFailed">
    <q-tabs
      :model-value="session.preferredExecutionAgentId || null"
      dense no-caps inline-label outside-arrows align="left" :breakpoint="0"
      active-class="app-surface-tonal text-primary" indicator-color="transparent"
      aria-label="Computer auswählen"
      @update:model-value="() => {}"
    >
      <q-tab
        v-for="computer in computers" :key="computer.id" :name="computer.id"
        class="rounded-borders q-px-sm q-mr-xs" content-class="row no-wrap items-center"
        :disable="disabled"
        :aria-label="`${computer.alias}: ${computer.connected ? 'Online' : 'Offline'}. ${session.preferredExecutionAgentId === computer.id ? 'Computer abwählen' : 'Computer auswählen'}`"
        @click="selectComputer(computer)"
      >
        <q-icon :name="computer.connected ? 'computer' : 'desktop_access_disabled'" :color="computer.connected ? 'positive' : 'grey'" size="18px" class="q-mr-xs" />
        <span class="text-caption">{{ computer.alias }}</span>
      </q-tab>
    </q-tabs>
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
  // Handle clicks rather than QTabs model updates so an active tab can be cleared.
  const selected = props.session.preferredExecutionAgentId !== computer.id
  emit('update', {
    preferred_execution_agent_id: selected ? computer.id : null,
    current_directory: selected ? computer.homeDirectory || null : null
  })
}
</script>
