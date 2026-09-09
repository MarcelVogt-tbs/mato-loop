<template>
  <router-view v-if="!listVisible" />
  <section v-else>
    <header class="settings-section-header">
      <div>
        <h2 class="text-h6 q-my-none">Automationen</h2>
        <p class="text-body2 app-muted q-mb-none q-mt-xs">Aufträge manuell oder nach Zeitplan ausführen.</p>
      </div>
      <q-btn unelevated no-caps no-wrap color="primary" icon="add" label="Automation erstellen" :to="{ name: 'settings-automation-create' }" />
    </header>
    <q-tabs v-model="listTab" dense no-caps align="left" active-color="primary" indicator-color="primary">
      <q-tab name="current" label="Automationen" />
      <q-tab name="archive" label="Archiv" />
    </q-tabs>
    <q-separator />
    <q-banner v-if="loadFailed" class="bg-negative text-white q-mt-md">
      Die Automationen konnten nicht geladen werden.
      <template #action><q-btn flat no-caps color="white" label="Erneut laden" @click="loadAutomations" /></template>
    </q-banner>
    <div v-if="automations.loading && !loaded" class="text-center q-pa-lg">
      <q-spinner color="primary" size="24px" aria-label="Automationen werden geladen" />
    </div>
    <p v-else-if="loaded && !automations.items.length" class="text-body2 app-muted q-py-md">
      {{ showArchived ? 'Keine archivierten Automationen.' : 'Noch keine Automationen. Erstelle deinen ersten Auftrag.' }}
    </p>
    <q-list v-else separator>
      <q-item v-for="automation in automations.items" :key="automation.id" clickable role="link" :aria-label="`${automation.name} öffnen`" class="q-py-md" @click="router.push({ name: 'settings-automation-detail', params: { automationId: automation.id } })">
        <q-item-section>
          <q-item-label class="text-weight-medium automation-name">{{ automation.name }}</q-item-label>
          <q-item-label caption lines="1" class="q-mt-xs">{{ automation.taskExcerpt ?? automation.task }}</q-item-label>
          <q-item-label caption class="q-mt-xs">
            <q-icon :name="automationStatus(automation).icon" :color="automationStatus(automation).color" class="q-mr-xs" />{{ automationStatus(automation).label }}
            <span v-if="automation.effectiveState?.activeRunStatus || (automation.enabled && automation.effectiveState?.nextRunAt) || automation.archivedAt"> · {{ automationTiming(automation) }}</span>
          </q-item-label>
        </q-item-section>
        <q-item-section side top @click.stop @keydown.stop @keyup.stop>
          <div class="row no-wrap q-gutter-xs">
            <q-btn v-if="!automation.archivedAt" flat round dense icon="play_arrow" :aria-label="`${automation.name} jetzt ausführen`" :loading="runningId === automation.id" :disable="Boolean(automation.effectiveState?.activeRunStatus) || Boolean(runningId)" @click="runNow(automation)">
              <q-tooltip>{{ automation.effectiveState?.activeRunStatus ? 'Es läuft bereits ein Auftrag.' : 'Jetzt ausführen' }}</q-tooltip>
            </q-btn>
            <q-btn flat round dense icon="more_vert" :aria-label="`Aktionen für ${automation.name}`">
              <q-menu>
                <q-list>
                  <q-item clickable v-close-popup :to="{ name: 'settings-automation-detail', params: { automationId: automation.id } }">
                    <q-item-section>Öffnen</q-item-section>
                  </q-item>
                  <q-item clickable v-close-popup class="text-negative" @click="removeAutomation(automation)">
                    <q-item-section>Löschen</q-item-section>
                  </q-item>
                </q-list>
              </q-menu>
            </q-btn>
          </div>
        </q-item-section>
      </q-item>
    </q-list>
  </section>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useQuasar } from 'quasar'
import { useRoute, useRouter } from 'vue-router'
import { useConfirm } from 'src/composables/confirm'
import { automationStatus, automationTiming } from 'src/utils/automation-status'
import { useAutomationStore } from 'stores/automations'

defineOptions({ name: 'AutomationSettingsPage' })
const $q = useQuasar()
const route = useRoute()
const router = useRouter()
const { confirm } = useConfirm()
const automations = useAutomationStore()
const loaded = ref(false)
const loadFailed = ref(false)
const listTab = ref('current')
const showArchived = computed(() => listTab.value === 'archive')
const runningId = ref(null)
let refreshTimer
let refreshPending = false
let alive = true

const legacyAutomationId = computed(() => route.name === 'settings-automations' ? [route.query.automation].flat()[0] : null)
const listVisible = computed(() => route.name === 'settings-automations' && !legacyAutomationId.value)

async function loadAutomations(background = false) {
  if (!listVisible.value || (background && (refreshPending || document.visibilityState !== 'visible'))) return
  refreshPending = true
  try {
    await automations.fetchAutomations({ archived: showArchived.value, suppressErrorToast: background })
    loaded.value = true
    loadFailed.value = false
  } catch {
    if (!background) loadFailed.value = true
  } finally {
    refreshPending = false
  }
}

async function removeAutomation(automation) {
  if (!await confirm(`Soll die Automation „${automation.name}“ samt Laufhistorie dauerhaft gelöscht werden?`, 'Automation löschen?', { okLabel: 'Löschen', okColor: 'negative' })) return
  try {
    await automations.deleteAutomation(automation.id, automation.revision)
    $q.notify({ type: 'positive', message: 'Automation gelöscht' })
  } catch {
    // The central Axios interceptor displays the error.
  }
}

async function runNow(automation) {
  if (runningId.value || automation.archivedAt || automation.effectiveState?.activeRunStatus) return
  runningId.value = automation.id
  try {
    const run = await automations.runNow(automation.id, automation.revision)
    if (alive && listVisible.value) await router.push({ name: 'settings-automation-detail', params: { automationId: automation.id }, query: { run: run.id } })
  } catch {
    // The central Axios interceptor displays the error.
  } finally {
    runningId.value = null
  }
}

function refresh() { void loadAutomations(true) }

watch([listVisible, legacyAutomationId, showArchived], () => {
  window.clearInterval(refreshTimer)
  if (legacyAutomationId.value) {
    const run = [route.query.run].flat()[0]
    void router.replace({ name: 'settings-automation-detail', params: { automationId: legacyAutomationId.value }, query: run ? { run } : {} })
  } else if (listVisible.value) {
    loaded.value = false
    void loadAutomations()
    refreshTimer = window.setInterval(refresh, 5000)
  }
}, { immediate: true })

onMounted(() => document.addEventListener('visibilitychange', refresh))
onBeforeUnmount(() => {
  alive = false
  window.clearInterval(refreshTimer)
  document.removeEventListener('visibilitychange', refresh)
})
</script>

<style scoped>
.automation-name { overflow-wrap: anywhere; }
</style>
