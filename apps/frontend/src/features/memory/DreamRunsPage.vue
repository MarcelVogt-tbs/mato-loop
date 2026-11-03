<template>
  <section>
    <q-breadcrumbs separator="›" class="q-mb-md" aria-label="Verarbeitungsnavigation">
      <q-breadcrumbs-el label="Verarbeitung" :to="{ name: 'settings-memory', query: { tab: 'dreaming' } }" />
      <q-breadcrumbs-el label="Durchläufe" aria-current="page" />
    </q-breadcrumbs>
    <header class="settings-section-header">
      <div>
        <h2 class="text-h6 text-weight-bold q-my-none">Durchläufe</h2>
        <p class="text-body2 app-muted q-mt-xs q-mb-none">Ergebnisse und verarbeitete Sessions, neueste zuerst.</p>
      </div>
      <q-btn flat round icon="refresh" aria-label="Durchläufe aktualisieren" :loading="memories.dreamRunsLoading" @click="load({ pagination })" />
    </header>
    <q-table
      flat wrap-cells class="app-surface" row-key="id"
      :rows="memories.dreamRuns" :columns="columns" :pagination="pagination"
      :rows-per-page-options="[10, 20, 50]" :loading="memories.dreamRunsLoading"
      :grid="$q.screen.lt.md" @request="load"
      @row-click="(_event, row) => router.push(detailRoute(row))"
    >
      <template #body-cell-session="props">
        <q-td :props="props" class="settings-list-name">
          <router-link :to="detailRoute(props.row)" class="text-primary" @click.stop>{{ props.value }}</router-link>
        </q-td>
      </template>
      <template #item="props">
        <div class="col-12 q-pa-xs">
          <q-card flat bordered>
            <q-item clickable :to="detailRoute(props.row)">
              <q-item-section>
                <q-item-label class="settings-list-name">{{ props.row.sourceSession?.title || 'Session' }}</q-item-label>
                <q-item-label caption>{{ formatAbsoluteDateTime(props.row.createdAt) || 'unbekannt' }} · {{ dreamRunMeta(props.row.status).label }}</q-item-label>
                <q-item-label caption>{{ memoryCounts(props.row) }}</q-item-label>
                <q-item-label caption>Zusammenfassung: {{ summaryStatusLabel(props.row.summaryStatus) }}</q-item-label>
              </q-item-section>
              <q-item-section side><q-icon name="chevron_right" /></q-item-section>
            </q-item>
          </q-card>
        </div>
      </template>
      <template #no-data><span>Noch keine Durchläufe vorhanden.</span></template>
    </q-table>
  </section>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useMemoryStore } from 'src/stores/memories'
import { formatAbsoluteDateTime } from 'src/utils/date-time'
import { dreamRunMeta, summaryStatusLabel } from './dreaming'

const memories = useMemoryStore()
const router = useRouter()
const pagination = ref({ page: 1, rowsPerPage: 20, rowsNumber: 0 })
const columns = [
  { name: 'created', label: 'Zeitpunkt', field: 'createdAt', format: (value) => formatAbsoluteDateTime(value) || 'unbekannt', align: 'left' },
  { name: 'session', label: 'Session', field: (row) => row.sourceSession?.title || 'Session', align: 'left' },
  { name: 'status', label: 'Status', field: (row) => dreamRunMeta(row.status).label, align: 'left' },
  { name: 'memories', label: 'Erinnerungen', field: memoryCounts, align: 'left' },
  { name: 'summary', label: 'Zusammenfassung', field: (row) => summaryStatusLabel(row.summaryStatus), align: 'left' }
]
function memoryCounts(row) {
  return `${row.createdMemories || 0} neu · ${row.updatedMemories || 0} geändert · ${row.removedMemories || 0} entfernt`
}
function detailRoute(row) { return { name: 'settings-dream-run', params: { runId: row.id } } }
async function load({ pagination: requested }) {
  try {
    const data = await memories.fetchDreamRuns({ offset: (requested.page - 1) * requested.rowsPerPage, limit: requested.rowsPerPage })
    if (data) pagination.value = { ...requested, rowsNumber: data.total }
  } catch {
    // The central API interceptor displays the error.
  }
}
onMounted(() => load({ pagination: pagination.value }))
</script>
