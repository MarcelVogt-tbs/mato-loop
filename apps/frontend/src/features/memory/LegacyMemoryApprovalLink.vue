<template>
  <q-banner rounded class="app-surface-tonal q-mb-md" role="status">
    <template v-if="loading">Vorschlag wird geladen …</template>
    <template v-else-if="failed">Der Vorschlag konnte nicht geladen werden.</template>
    <template v-else-if="sessionId">Diesen Vorschlag prüfst du im zugehörigen Chat.</template>
    <template v-else>Dieser Vorschlag ist nicht mehr offen oder nicht verfügbar.</template>
    <template #action>
      <q-btn v-if="failed" flat no-caps label="Erneut laden" @click="load" />
      <q-btn v-else-if="sessionId" flat no-caps icon="chat" label="Im Chat prüfen" :to="{ name: 'chat', query: { session: sessionId } }" />
    </template>
  </q-banner>
</template>

<script setup>
import { ref, watch } from 'vue'
import { useApprovalStore } from 'stores/approvals'

const props = defineProps({ approvalId: { type: String, required: true } })
const approvals = useApprovalStore()
const loading = ref(false)
const failed = ref(false)
const sessionId = ref(null)

async function load() {
  const id = props.approvalId
  loading.value = true
  failed.value = false
  sessionId.value = null
  try {
    await approvals.fetchPending()
    if (id !== props.approvalId) return
    const approval = approvals.pending.find((item) => item.id === id && item.requestType === 'memory_change')
    sessionId.value = approval?.sessionId || null
  } catch {
    if (id === props.approvalId) failed.value = true
    // The central API interceptor displays the error.
  } finally {
    if (id === props.approvalId) loading.value = false
  }
}

watch(() => props.approvalId, load, { immediate: true })
</script>
