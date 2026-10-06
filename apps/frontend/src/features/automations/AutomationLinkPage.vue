<template>
  <q-page class="q-pa-md">
    <q-banner v-if="failed" rounded class="app-surface-tonal">
      Die Automation konnte nicht geöffnet werden.
      <template #action><q-btn flat no-caps color="primary" label="Erneut versuchen" @click="open" /></template>
    </q-banner>
    <q-spinner v-else color="primary" size="24px" aria-label="Automation wird geöffnet" />
  </q-page>
</template>

<script setup>
import { ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAutomationStore } from 'stores/automations'

const route = useRoute()
const router = useRouter()
const automations = useAutomationStore()
const failed = ref(false)
let version = 0

async function open() {
  const current = ++version
  failed.value = false
  try {
    const id = route.params.automationId || [route.query.automation].flat()[0]
    const automation = id ? await automations.fetchAutomation(id) : null
    const run = automation && typeof route.query.run === 'string' ? await automations.fetchRun(automation.id, route.query.run) : null
    if (current !== version) return
    await router.replace({ name: 'chat', query: run
      ? { session: automation.executionSessionId, ...(run.messageId ? { message: run.messageId } : {}) }
      : {
          ...(automation ? { session: automation.executionSessionId, automation: automation.id } : { automation: 'new' }),
          controlCenter: 'automation'
        }
    })
  } catch {
    if (current === version) failed.value = true
  }
}

watch(() => route.fullPath, open, { immediate: true })
</script>
