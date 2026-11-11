<template>
  <section class="q-gutter-y-md" aria-labelledby="assistant-settings-title">
    <header class="settings-section-header">
      <h2 id="assistant-settings-title" class="text-h6 text-weight-bold q-my-none">Assistent</h2>
      <q-toggle v-if="settings?.initialized" :model-value="settings.enabled" label="Assistent aktiv" :disable="busy || !settings.automationId || !!settings.errorCode" @update:model-value="setEnabled" />
    </header>
    <q-spinner v-if="loading" color="primary" aria-label="Assistent wird geladen" />
    <q-banner v-if="loadFailed" rounded class="bg-negative text-white">
      Der Assistent konnte nicht geladen werden.
      <template #action><q-btn flat no-caps label="Erneut laden" @click="load" /></template>
    </q-banner>
    <template v-if="settings">
      <p class="text-body2 app-muted" aria-live="polite">{{ status }}</p>
      <div class="row q-gutter-sm">
        <q-btn color="primary" unelevated no-caps icon="auto_awesome" :label="settings.initialized ? 'Einrichtung anpassen' : 'Einrichten'" :loading="starting" :disable="busy" @click="startWizard" />
        <q-btn v-if="settings.sessionId" outline no-caps icon="chat" label="Session öffnen" :to="{ name: 'chat', query: { session: settings.sessionId } }" />
        <q-btn v-if="settings.automationId" flat no-caps color="primary" label="Automation bearbeiten" :to="{ name: 'settings-automation-detail', params: { automationId: settings.automationId } }" />
        <q-btn flat no-caps color="primary" label="Skill bearbeiten" :to="{ name: 'settings-skill-edit', params: { skillId: '00000000-0000-4000-8000-000000000103' } }" />
      </div>
    </template>
  </section>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useQuasar } from 'quasar'
import { useRouter } from 'vue-router'
import { api } from 'src/boot/axios'
import { useAutomationStore } from 'src/features/automations/store'
import { scheduleDraft, schedulePayload } from 'src/features/automations/schedules'
import { useChatStore } from 'src/features/chat/store'

const $q = useQuasar(), router = useRouter(), chat = useChatStore(), automations = useAutomationStore()
const settings = ref(null), loading = ref(false), loadFailed = ref(false), saving = ref(false), starting = ref(false)
const busy = computed(() => loading.value || saving.value || starting.value)
const status = computed(() => settings.value?.errorCode ? 'Einrichtung prüfen' : !settings.value?.initialized ? 'Nicht eingerichtet' : settings.value.enabled ? 'Aktiv' : 'Pausiert')

async function load() {
  loading.value = true
  loadFailed.value = false
  try { settings.value = (await api.get('/assistant')).data }
  catch { loadFailed.value = true }
  finally { loading.value = false }
}

async function setEnabled(enabled) {
  if (busy.value || !settings.value?.initialized || !settings.value.automationId || settings.value.errorCode) return
  saving.value = true
  try {
    const saved = await automations.fetchAutomation(settings.value.automationId)
    const updated = await automations.updateAutomation(saved.id, {
      name: saved.name, task: saved.task, enabled, modelId: saved.modelId ?? null,
      schedules: saved.schedules.map(schedule => schedulePayload(scheduleDraft(schedule))),
      triggers: saved.triggers || [], expectedRevision: saved.revision
    })
    settings.value = { ...settings.value, enabled: updated.effectiveState?.enabled ?? updated.enabled }
    $q.notify({ type: 'positive', message: enabled ? 'Assistent aktiviert' : 'Assistent pausiert' })
  } catch { /* The central Axios interceptor displays the error; keep the saved state. */ }
  finally { saving.value = false }
}

async function startWizard() {
  if (busy.value) return
  starting.value = true
  try {
    settings.value = (await api.post('/assistant/setup')).data
    const sessionId = settings.value.sessionId
    if (!sessionId) return
    await chat.loadSession(sessionId)
    if (chat.current?.id !== sessionId || chat.current.readOnly || chat.current.archivedAt) return
    await chat.send('/assistant-init', { preserveDraft: true, activeFile: null })
    await router.push({ name: 'chat', query: { session: sessionId } })
  } catch { /* The central Axios interceptor displays the error. */ }
  finally { starting.value = false }
}

onMounted(load)
</script>
