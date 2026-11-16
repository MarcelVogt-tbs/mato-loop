<template>
  <AppPage :title="isTrash ? 'Papierkorb' : 'Archiv'" :subtitle="isTrash ? 'Gelöschte Projekte und Sessions bis zur angezeigten Frist wiederherstellen oder endgültig löschen.' : 'Archivierte Gespräche bleiben erhalten. Zum Weiterarbeiten wiederherstellen.'">
    <q-banner v-if="archive.failed" rounded class="app-surface-tonal q-mb-md">
      {{ isTrash ? 'Der Papierkorb' : 'Das Archiv' }} konnte nicht geladen werden.
      <template #action><q-btn flat no-caps label="Erneut laden" @click="load" /></template>
    </q-banner>
    <q-spinner v-if="archive.loading" color="primary" size="24px" :aria-label="isTrash ? 'Papierkorb wird geladen' : 'Archiv wird geladen'" />
    <template v-else-if="isTrash">
      <section v-for="kind in ['projects', 'sessions']" :key="kind" class="q-mb-lg">
        <h2 class="text-subtitle1">{{ kind === 'projects' ? 'Projekte' : 'Einzeln gelöschte Sessions' }}</h2>
        <q-list v-if="archive.trash[kind].length" bordered separator class="rounded-borders">
          <q-item v-for="item in archive.trash[kind]" :key="item.id">
            <q-item-section class="text-break">
              <q-item-label>{{ item.name || item.title }}</q-item-label>
              <q-item-label v-if="item.projectName" caption>{{ item.projectName }}</q-item-label>
              <q-item-label caption>{{ expired(item) ? 'Frist abgelaufen' : 'Wiederherstellbar bis' }}: {{ deadline(item.deleteAt) }}</q-item-label>
            </q-item-section>
            <q-item-section side>
              <div class="row">
                <q-btn flat round icon="restore_from_trash" :loading="restoring === item.id" :disable="expired(item) || Boolean(busy)" :aria-label="kind === 'projects' ? 'Projekt wiederherstellen' : 'Session wiederherstellen'" @click="restoreTrash(kind, item)" />
                <q-btn flat round color="negative" icon="delete_forever" :loading="deleting === item.id" :disable="Boolean(busy)" :aria-label="kind === 'projects' ? 'Projekt endgültig löschen' : 'Session endgültig löschen'" @click="deleteTrash(kind, item)" />
              </div>
            </q-item-section>
          </q-item>
        </q-list>
        <p v-else class="app-muted">{{ kind === 'projects' ? 'Keine gelöschten Projekte.' : 'Keine einzeln gelöschten Sessions.' }}</p>
      </section>
    </template>
    <template v-else>
      <h2 class="text-subtitle1"><q-icon name="folder_open" class="q-mr-sm" />Projekte</h2>
      <q-list bordered separator class="rounded-borders q-mb-lg" v-if="archive.projects.length">
        <q-expansion-item v-for="project in archive.projects" :key="project.id" :duration="0" @show="loadSessions(project.id)">
          <template #header>
            <q-item-section>{{ project.name }}</q-item-section>
            <q-item-section side><q-btn flat round icon="unarchive" :loading="restoring === project.id" aria-label="Projekt wiederherstellen" @click.stop="restoreProject(project)" /></q-item-section>
          </template>
          <q-list dense>
            <q-item v-for="session in archive.sessionLists[project.id]?.items || []" :key="session.id" clickable :to="sessionRoute(session)">
              <q-item-section><q-item-label>{{ session.title }}</q-item-label><q-item-label v-if="session.archivedAt" caption>Auch einzeln archiviert</q-item-label></q-item-section>
            </q-item>
            <q-item v-if="archive.sessionLists[project.id]?.nextCursor"><q-btn flat no-caps label="Weitere Sessions" @click="loadSessions(project.id, true)" /></q-item>
          </q-list>
        </q-expansion-item>
      </q-list>
      <p v-else class="app-muted">Keine archivierten Projekte.</p>
      <h2 class="text-subtitle1"><q-icon name="chat_bubble_outline" class="q-mr-sm" />Einzeln archivierte Sessions</h2>
      <q-list v-if="archive.sessionLists.sessions?.items.length" bordered separator class="rounded-borders">
        <q-item v-for="session in archive.sessionLists.sessions.items" :key="session.id" clickable :to="sessionRoute(session)">
          <q-item-section><q-item-label>{{ session.title }}</q-item-label><q-item-label v-if="session.projectId" caption>Aus einem Projekt</q-item-label></q-item-section>
          <q-item-section side><q-btn flat round icon="unarchive" :loading="restoring === session.id" aria-label="Session wiederherstellen" @click.stop.prevent="restoreSession(session)" /></q-item-section>
        </q-item>
      </q-list>
      <p v-else class="app-muted">Keine einzeln archivierten Sessions.</p>
      <q-btn v-if="archive.sessionLists.sessions?.nextCursor" flat no-caps label="Weitere Sessions" @click="loadSessions(null, true)" />
    </template>
  </AppPage>
</template>

<script setup>
import { computed, inject, onBeforeUnmount, ref, watch } from 'vue'
import { useQuasar } from 'quasar'
import AppPage from 'src/shared/ui/AppPage.vue'
import { useArchiveStore } from './store'
import { useChatStore } from 'src/features/chat/store'
import { useProjectStore } from 'stores/projects'
import { useAutomationStore } from 'stores/automations'

const props = defineProps({ mode: { type: String, default: 'archive' } })
const isTrash = computed(() => props.mode === 'trash')
const deleteActions = inject('deleteActions', null)
const archive = useArchiveStore()
const chat = useChatStore()
const projects = useProjectStore()
const automations = useAutomationStore()
const $q = useQuasar()
const restoring = ref(null)
const deleting = ref(null)
const busy = computed(() => restoring.value || deleting.value)
const now = ref(Date.now())
const clock = window.setInterval(() => { now.value = Date.now() }, 1000)
onBeforeUnmount(() => window.clearInterval(clock))
const expired = (item) => Date.parse(item.deleteAt) <= now.value
const deadline = (value) => new Date(value).toLocaleString('de-DE')
const sessionRoute = (session) => ({ name: 'chat', query: { session: session.id, ...(session.projectId ? { project: session.projectId } : {}) } })
const load = () => archive.load(props.mode)

async function restoreTrash(kind, item) {
  if (busy.value || expired(item)) return
  restoring.value = item.id
  try {
    await archive.restoreTrash(kind, item.id)
    await Promise.all([load(), projects.fetchAll(), chat.refreshSessions(null, kind === 'sessions' ? item.projectId || null : item.id), automations.fetchAutomations()])
    $q.notify({ type: 'positive', message: kind === 'projects' ? 'Projekt wiederhergestellt.' : 'Session wiederhergestellt.' })
  } catch { /* Axios displays errors. */ } finally { restoring.value = null }
}

async function deleteTrash(kind, item) {
  if (busy.value) return
  deleting.value = item.id
  try {
    if (await deleteActions[kind](item)) await load()
  } catch { /* Axios displays errors. */ } finally { deleting.value = null }
}

async function loadSessions(id, more = false) {
  try { await archive.loadSessions(id, more) } catch { /* Axios displays errors. */ }
}

async function restoreSession(session) {
  if (restoring.value) return
  restoring.value = session.id
  try {
    await chat.setArchived(session.id, false)
    await Promise.all([archive.load(), automations.fetchAutomations()])
    $q.notify({ type: 'positive', message: 'Session wiederhergestellt' })
  } catch { /* Axios displays errors. */ } finally { restoring.value = null }
}

async function restoreProject(project) {
  if (restoring.value) return
  restoring.value = project.id
  try {
    await projects.setArchived(project.id, false)
    await Promise.all([archive.load(), automations.fetchAutomations(), chat.refreshSessions(null, project.id)])
    if (chat.current?.projectId === project.id) await chat.refreshSession(chat.current.id)
    $q.notify({ type: 'positive', message: 'Projekt wiederhergestellt' })
  } catch { /* Axios displays errors. */ } finally { restoring.value = null }
}
watch(() => props.mode, load, { immediate: true })
</script>
