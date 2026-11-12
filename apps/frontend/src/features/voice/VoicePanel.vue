<template>
  <q-banner v-if="voice.active" rounded class="app-surface-tonal q-mb-sm" aria-label="Sprachmodus">
    <div class="row items-center q-gutter-sm" role="status" aria-live="polite">
      <q-spinner v-if="voice.phase === 'starting'" color="primary" />
      <q-icon v-else :name="voice.muted ? 'mic_off' : 'record_voice_over'" color="primary" />
      <span class="text-weight-medium">{{ phaseLabel }}</span>
    </div>
    <div class="text-caption app-muted q-mt-xs">Audio wird direkt an den Provider übertragen und nicht textlich pseudonymisiert. MatoLoop speichert nur den Textverlauf. Beim Provider können Kosten entstehen.</div>
    <div v-if="voice.wakeLockActive !== null" class="text-caption q-mt-xs" role="status">
      {{ voice.wakeLockActive ? 'Bildschirm bleibt während des Gesprächs an.' : 'Bildschirm kann sich sperren. Prüfe bei Bedarf die automatische Sperre und den Stromsparmodus deines Geräts.' }}
    </div>
    <div class="row q-gutter-xs q-mt-sm">
      <q-btn flat dense no-caps :icon="voice.muted ? 'mic' : 'mic_off'" :label="voice.muted ? 'Mikrofon an' : 'Stummschalten'" :disable="voice.phase === 'starting'" @click="voice.toggleMute()" />
      <q-btn flat dense no-caps icon="volume_off" label="Sprache stoppen" :disable="voice.phase === 'starting'" @click="voice.interrupt()" />
      <q-btn flat dense no-caps icon="keyboard" label="Zum Textchat" @click="voice.end()" />
      <q-btn flat dense no-caps icon="call_end" color="negative" label="Gespräch beenden" @click="voice.end()" />
    </div>
    <div class="text-caption q-mt-sm">Aufgaben, Freigaben und Rückfragen bleiben hier im Chat. Aufgaben laufen nach Gesprächsende weiter.</div>
  </q-banner>
  <q-list v-if="tasks.length" dense bordered class="rounded-borders q-mb-sm">
    <q-item v-for="task in tasks" :key="task.taskId">
      <q-item-section>
        <q-item-label>Sprachauftrag · {{ taskStatus(task.status) }}</q-item-label>
        <q-item-label v-if="task.requiresUi" caption>{{ task.sessionId === chat.current?.id ? 'Beantworte die Freigabe oder Rückfrage hier im Chat.' : 'Öffne die Arbeitssession für die Freigabe oder Rückfrage.' }}</q-item-label>
      </q-item-section>
      <q-item-section side>
        <q-btn v-if="task.sessionId && task.sessionId !== chat.current?.id" flat dense no-caps label="Öffnen" :to="{ name: 'chat', query: { session: task.sessionId } }" @click="voice.active && voice.end()" />
        <q-btn v-if="task.active && task.runId" flat dense no-caps color="negative" label="Stoppen" @click="stopTask(task)" />
      </q-item-section>
    </q-item>
  </q-list>
  <q-banner v-if="taskError" rounded class="app-surface-tonal q-mb-sm">
    Sprachaufträge konnten nicht geladen werden.
    <q-btn flat dense no-caps label="Erneut laden" @click="refreshTasks" />
  </q-banner>
</template>

<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { api } from 'src/boot/axios'
import { useConfirm } from 'src/composables/confirm'
import { useChatStore } from 'src/features/chat/store'
import { useControlCenterStore } from 'stores/control-center'
import { useVoiceStore } from './store'
import { notifyError } from 'src/utils/notifications'

const chat = useChatStore(), voice = useVoiceStore()
const { confirm } = useConfirm()
const starting = ref(false), storedTasks = ref([])
const taskError = ref(false)
let refreshTimer, refreshGeneration = 0, disposed = false
const tasks = computed(() => voice.active && voice.sessionId === chat.current?.id ? voice.tasks : storedTasks.value)
const canStartWhileRunning = computed(() => tasks.value.some((task) => task.active && task.sessionId === chat.current?.id))
const phaseLabel = computed(() => voice.muted ? 'Mikrofon stumm' : ({
  starting: 'Verbindung wird aufgebaut', listening: 'Ich höre zu', thinking: 'Antwort wird vorbereitet', speaking: 'MatoLoop spricht'
})[voice.phase] || 'Verbindung wird beendet')
const taskStatus = (status) => ({
  starting: 'Wird gestartet', queued: 'Eingereiht', running: 'Läuft', waiting_approval: 'Freigabe erforderlich',
  waiting_input: 'Antwort erforderlich', completed: 'Abgeschlossen', cancelled: 'Gestoppt', failed: 'Fehlgeschlagen',
  outcome_unknown: 'Ausgang unklar – Auftrag prüfen'
})[status] || 'Status wird geprüft'

async function start() {
  if (starting.value || voice.active || disposed) return
  starting.value = true
  try {
    const session = chat.current || await chat.createEmptySession(useControlCenterStore())
    if (!session || disposed || chat.current?.id !== session.id) return
    await voice.start(session.id)
  } catch (error) {
    if (!error?.isAxiosError) notifyError(error)
  } finally { starting.value = false }
}
async function stopTask(task) {
  if (!await confirm('Soll der Sprachauftrag gestoppt werden? Bereits ausgeführte Aktionen werden nicht rückgängig gemacht.', 'Aufgabe stoppen', { okLabel: 'Stoppen', okColor: 'negative' })) return
  await api.post(`/sessions/${task.sessionId}/runs/${task.runId}/cancel`)
  await refreshTasks()
}
async function refreshTasks() {
  clearTimeout(refreshTimer)
  const generation = ++refreshGeneration
  if (disposed || starting.value || voice.active) return
  if (!chat.current?.id || chat.current.archived || chat.current.readOnly) { storedTasks.value = []; return }
  const id = chat.current.id
  try {
    const { data } = await api.get(`/sessions/${id}/voice`)
    if (disposed || generation !== refreshGeneration || chat.current?.id !== id) return
    storedTasks.value = data.tasks
    taskError.value = false
    if (data.tasks.some((task) => task.active)) refreshTimer = setTimeout(refreshTasks, 3000)
  } catch (error) {
    if (disposed || generation !== refreshGeneration || chat.current?.id !== id) return
    taskError.value = true
    if (!error?.isAxiosError) notifyError(error)
  }
}
watch(() => chat.current?.id, (id) => {
  if (voice.active && voice.sessionId !== id) void voice.end()
  storedTasks.value = []
  void refreshTasks()
}, { immediate: true })
watch(() => chat.current?.messages?.length, () => { void refreshTasks() })
watch(() => voice.active, (active) => { if (!active) void refreshTasks() })
onBeforeUnmount(() => {
  disposed = true
  clearTimeout(refreshTimer)
  if (voice.active) void voice.end()
})
defineExpose({ start, starting, canStartWhileRunning })
</script>
