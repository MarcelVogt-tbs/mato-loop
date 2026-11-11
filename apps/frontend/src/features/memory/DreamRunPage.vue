<template>
  <section class="settings-list-name">
    <q-breadcrumbs separator="›" class="q-mb-md" aria-label="Verarbeitungsnavigation">
      <q-breadcrumbs-el label="Verarbeitung" :to="{ name: 'settings-memory', query: { tab: 'dreaming' } }" />
      <q-breadcrumbs-el label="Durchläufe" :to="{ name: 'settings-dream-runs' }" />
      <q-breadcrumbs-el label="Details" aria-current="page" />
    </q-breadcrumbs>
    <header class="settings-section-header">
      <div>
        <h2 class="text-h6 text-weight-bold q-my-none">Durchlaufdetails</h2>
        <p v-if="run" class="text-body2 app-muted q-mt-xs q-mb-none">{{ formatAbsoluteDateTime(run.createdAt) || 'unbekannt' }} · {{ dreamRunMeta(run.status).label }}</p>
      </div>
      <q-btn flat round icon="refresh" aria-label="Durchlauf aktualisieren" :loading="memories.dreamRunLoading" @click="load" />
    </header>
    <div v-if="memories.dreamRunLoading" class="text-center q-pa-lg"><q-spinner color="primary" size="32px" /></div>
    <template v-else-if="run">
      <q-card flat class="app-surface q-mb-md">
        <q-card-section>
          <div class="text-subtitle1">Quelle</div>
          <q-btn flat no-caps icon="chat" :label="run.sourceSession?.title || 'Session öffnen'" :to="{ name: 'chat', query: { session: run.sessionId, message: run.sourceToMessageId } }" />
          <p class="text-body2 app-muted q-mb-none">{{ run.sourceMessageCount }} Nachrichten · {{ run.trigger === 'manual' ? 'Manuell gestartet' : 'Automatisch gestartet' }} · {{ run.attempts }} Versuch(e)</p>
          <p class="text-body2 q-mb-none">Memory-Verarbeitung: {{ run.memoryCompletedAt ? 'Abgeschlossen' : dreamRunMeta(run.status).label }}</p>
          <p v-if="run.error" class="text-negative q-mb-none">{{ apiErrorMessage(run.error) }}</p>
        </q-card-section>
      </q-card>
      <q-card flat class="app-surface q-mb-md">
        <q-card-section>
          <h3 class="text-subtitle1 q-mt-none">Erinnerungen</h3>
          <p class="text-body2 app-muted">{{ run.createdMemories }} neu · {{ run.updatedMemories }} geändert · {{ run.removedMemories }} entfernt</p>
          <p v-if="!run.changes.length" class="text-body2 app-muted">Keine gespeicherten Änderungsdetails. Frühere Läufe enthalten nur Zähler.</p>
          <div v-for="change in run.changes" :key="change.id" class="q-mb-lg">
            <div class="text-weight-medium">{{ actionLabel(change.action) }}</div>
            <template v-if="change.before?.length">
              <div class="text-caption app-muted">Vorher</div>
              <p v-for="(item, index) in change.before" :key="index" class="text-body2 q-mt-xs">{{ item.content || 'Inhalt wurde entfernt.' }}</p>
            </template>
            <template v-if="change.after">
              <div class="text-caption app-muted">{{ change.action === 'create' ? 'Neue Erinnerung' : 'Nachher' }}</div>
              <p class="text-body2 q-mt-xs">{{ change.after.content }}</p>
            </template>
            <p v-if="!change.before && !change.after" class="text-body2 app-muted">Der Inhalt ist nicht mehr gespeichert.</p>
          </div>
          <div v-if="run.proposals.length">
            <div class="text-subtitle2">Änderungsvorschläge</div>
            <q-list>
              <q-item v-for="proposal in run.proposals" :key="proposal.id" :clickable="proposal.status === 'pending'" :to="proposal.status === 'pending' ? notificationTargetRoute({ type: 'memoryApproval', approvalId: proposal.id, sessionId: proposal.sessionId }) : undefined">
                <q-item-section>
                  <q-item-label>{{ actionLabel(proposal.action.replace('memory_', '')) }}</q-item-label>
                  <q-item-label caption>{{ proposalStatusLabel(proposal.status) }}{{ proposal.status === 'pending' ? ' · Im Chat prüfen' : '' }}</q-item-label>
                </q-item-section>
              </q-item>
            </q-list>
          </div>
        </q-card-section>
      </q-card>
      <q-card flat class="app-surface">
        <q-card-section>
          <h3 class="text-subtitle1 q-mt-none">Session-Zusammenfassung</h3>
          <p class="text-body2 app-muted">{{ summaryStatusLabel(run.summaryStatus) }}</p>
          <p v-if="run.summaryError" class="text-negative">{{ apiErrorMessage(run.summaryError) }}</p>
          <template v-if="run.summary">
            <p class="text-body2">{{ run.summary.overview }}</p>
            <q-list separator>
              <q-item v-for="(item, index) in run.summary.items" :key="index" class="q-px-none">
                <q-item-section>
                  <q-item-label>{{ item.text }}</q-item-label>
                  <q-item-label caption>{{ summaryItemStatus(item.status) }}</q-item-label>
                  <div>
                    <q-btn v-for="(id, number) in item.evidence" :key="id" flat dense no-caps icon="link" :label="`Beleg ${number + 1}`" :to="{ name: 'chat', query: { session: run.sessionId, message: id } }" />
                  </div>
                </q-item-section>
              </q-item>
            </q-list>
            <p class="text-caption app-muted q-mt-md">Stand dieses Laufs bis {{ formatAbsoluteDateTime(run.summary.coveredThroughAt) || 'unbekannt' }}. Spätere Änderungen sind hier nicht enthalten.</p>
          </template>
          <p v-else-if="run.summaryStatus === 'completed'" class="text-body2 app-muted">Diese Fassung ist nach einer Änderung des Quellverlaufs nicht mehr verfügbar.</p>
        </q-card-section>
      </q-card>
    </template>
    <p v-else class="app-muted">Der Durchlauf konnte nicht geladen werden.</p>
  </section>
</template>

<script setup>
import { computed, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useMemoryStore } from 'src/stores/memories'
import { apiErrorMessage } from 'src/utils/api-errors'
import { formatAbsoluteDateTime } from 'src/utils/date-time'
import { dreamRunMeta, summaryStatusLabel } from './dreaming'
import { notificationTargetRoute } from 'src/features/notifications/target'

const route = useRoute()
const memories = useMemoryStore()
const run = computed(() => memories.dreamRun)
function actionLabel(action) {
  return ({ create: 'Neu angelegt', update: 'Geändert', merge: 'Zusammengeführt', remove: 'Entfernt' })[action] || 'Änderung'
}
function proposalStatusLabel(status) {
  return ({ pending: 'Wartet auf Freigabe – noch nicht angewendet', approved: 'Genehmigt', denied: 'Abgelehnt', expired: 'Verfallen' })[status] || 'Abgeschlossen'
}
function summaryItemStatus(status) {
  return ({ discussed: 'Besprochen', decided: 'Entschieden', implemented: 'Umgesetzt', verified: 'Verifiziert', open: 'Offen', superseded: 'Überholt', unverified: 'Nicht verifiziert' })[status] || ''
}
async function load() {
  try { await memories.fetchDreamRun(route.params.runId) } catch {
    // The central API interceptor displays the error.
  }
}
watch(() => route.params.runId, load, { immediate: true })
</script>
