<template>
  <section class="column full-height no-wrap" aria-label="tbs.Desk">
    <AppStateMessage
      v-if="!available"
      class="col q-pa-xl"
      icon="confirmation_number"
      title="tbs.Desk nicht verfügbar"
      description="Richte in den persönlichen Einstellungen ein aktives tbs.Desk-Konto ein."
    />
    <div v-else-if="desk.loading && !desk.state" class="col flex flex-center">
      <q-spinner color="primary" size="40px" aria-label="tbs.Desk wird geladen" />
    </div>
    <AppStateMessage v-else-if="desk.loadFailed && !desk.state" class="col q-pa-xl" icon="cloud_off" title="tbs.Desk konnte nicht geladen werden">
      <template #actions>
        <q-btn outline no-caps color="primary" label="Erneut laden" @click="load" />
      </template>
    </AppStateMessage>
    <AppStateMessage v-else-if="desk.state && !desk.state.available" class="col q-pa-xl" icon="cloud_off" title="tbs.Desk ist derzeit nicht erreichbar" />

    <template v-else-if="desk.state">
      <q-banner v-if="desk.state.questionId" rounded class="app-surface-tonal q-ma-sm">
        <template #avatar><q-icon name="person_search" color="primary" /></template>
        <div class="text-subtitle2">Kunden auswählen</div>
        <div class="text-body2 app-muted q-mb-sm">Wähle den passenden Kunden für diese Session.</div>
        <q-list bordered separator class="rounded-borders app-surface">
          <q-item
            v-for="candidate in desk.state.candidates"
            :key="candidate.id"
            clickable
            tag="label"
            :active="selectedLabel === candidate.label"
          >
            <q-item-section avatar><q-radio dense v-model="selectedLabel" :val="candidate.label" /></q-item-section>
            <q-item-section>
              <q-item-label>{{ candidate.displayName }}</q-item-label>
              <q-item-label caption>{{ candidate.erpId }}<span v-if="candidate.activeCall"> · Aktiver Anruf</span></q-item-label>
            </q-item-section>
          </q-item>
        </q-list>
        <div class="row justify-end q-mt-sm">
          <q-btn
            unelevated
            no-caps
            color="primary"
            label="Kunden übernehmen"
            :disable="!selectedLabel"
            :loading="questions.submittingId === desk.state.questionId"
            @click="submitCustomer"
          />
        </div>
      </q-banner>

      <div v-if="desk.state.selectedCustomer" class="row items-center no-wrap q-pa-sm app-surface-tonal">
        <q-icon name="person" color="primary" size="24px" class="q-mr-sm" />
        <div class="col">
          <div class="text-weight-medium">{{ desk.state.selectedCustomer.displayName }}</div>
          <div class="text-caption app-muted">{{ desk.state.selectedCustomer.erpId }}</div>
        </div>
        <q-btn
          flat
          dense
          no-caps
          color="primary"
          label="Ändern / aufheben"
          aria-label="Kundenauswahl ändern oder aufheben"
          :loading="desk.loading"
          @click="clearCustomer"
        />
      </div>
      <q-separator v-if="desk.state.selectedCustomer" />

      <div class="tbs-desk-layout col">
        <div v-show="!mobileDetail" class="column no-wrap tbs-desk-list-pane">
          <q-scroll-area class="col">
            <q-list separator>
              <q-item v-if="!desk.state.tickets.length" class="q-pa-lg">
                <q-item-section class="text-center app-muted">Keine Tickets für diesen Kunden gefunden.</q-item-section>
              </q-item>
              <q-item
                v-for="ticket in desk.state.tickets"
                :key="ticket.id"
                clickable
                v-ripple
                :active="desk.detail?.id === ticket.id"
                active-class="app-surface-tonal text-primary"
                @click="openTicket(ticket.id)"
              >
                <q-item-section avatar><q-icon name="confirmation_number" /></q-item-section>
                <q-item-section>
                  <q-item-label lines="2">{{ ticket.header }}</q-item-label>
                  <q-item-label caption>{{ ticket.state }}</q-item-label>
                </q-item-section>
                <q-item-section side top><span class="text-caption">{{ formatAbsoluteDateTime(ticket.created) }}</span></q-item-section>
              </q-item>
            </q-list>
          </q-scroll-area>
        </div>

        <div v-show="!$q.screen.lt.md || mobileDetail" class="column no-wrap tbs-desk-detail-pane">
          <q-toolbar v-if="$q.screen.lt.md && mobileDetail">
            <q-btn flat round dense icon="arrow_back" aria-label="Zurück zur Ticketliste" @click="backToList" />
            <q-toolbar-title class="text-subtitle1">Ticket</q-toolbar-title>
          </q-toolbar>
          <q-separator v-if="$q.screen.lt.md && mobileDetail" />
          <div v-if="desk.detailLoading" class="col flex flex-center"><q-spinner color="primary" size="36px" /></div>
          <AppStateMessage v-else-if="!desk.detail" class="col q-pa-xl" icon="confirmation_number" description="Wähle ein Ticket aus." />
          <q-scroll-area v-else class="col">
            <article class="q-pa-md">
              <h3 class="text-h6 q-my-none">{{ desk.detail.header }}</h3>
              <div class="text-caption app-muted q-mt-xs">
                {{ desk.detail.state }}<span v-if="desk.detail.created"> · {{ formatAbsoluteDateTime(desk.detail.created) }}</span>
              </div>
              <q-separator class="q-my-md" />
              <q-list separator>
                <q-item v-for="(entry, index) in desk.detail.entries || []" :key="entry.id || index" class="q-px-none">
                  <q-item-section>
                    <q-item-label v-if="entry.starttime" caption>{{ formatAbsoluteDateTime(entry.starttime) }}</q-item-label>
                    <q-item-label class="tbs-desk-entry q-mt-xs">{{ entry.text || entry.content || entry.message || '' }}</q-item-label>
                  </q-item-section>
                </q-item>
              </q-list>
            </article>
          </q-scroll-area>
        </div>
      </div>
    </template>
  </section>
</template>

<script setup>
import { computed, onUnmounted, ref, watch } from 'vue'
import { useQuasar } from 'quasar'
import AppStateMessage from 'src/shared/ui/AppStateMessage.vue'
import { formatAbsoluteDateTime } from 'src/utils/date-time'
import { useChatStore } from 'src/features/chat/store'
import { useControlCenterStore } from 'stores/control-center'
import { useIntegrationStore } from 'stores/integrations'
import { useQuestionStore } from 'stores/questions'
import { useTbsDeskStore } from 'stores/tbs-desk'

defineOptions({ name: 'TbsDeskApp' })

const $q = useQuasar()
const chat = useChatStore()
const controlCenter = useControlCenterStore()
const integrations = useIntegrationStore()
const questions = useQuestionStore()
const desk = useTbsDeskStore()
const selectedLabel = ref(null)
const selectedTicketId = ref(null)
const sessionId = computed(() => chat.current?.id || null)
const active = computed(() => controlCenter.activeContentId === 'tbs-desk')
const available = computed(() => Boolean(sessionId.value && integrations.tbsDesk.accounts.some((account) => account.enabled)))
const runActive = computed(() => Boolean(chat.running || chat.runId || chat.current?.activeRun))
const mobileDetail = computed(() => $q.screen.lt.md && Boolean(selectedTicketId.value))
let pollTimer = null

async function load() {
  if (!active.value || !available.value || !sessionId.value || desk.loading) return
  try {
    await desk.fetchSession(sessionId.value)
  } catch {
    // Axios errors are displayed centrally.
  }
}

async function submitCustomer() {
  if (!desk.state?.questionId || !selectedLabel.value) return
  try {
    await questions.answer(desk.state.questionId, { tbs_customer: [selectedLabel.value] })
    selectedLabel.value = null
    await load()
  } catch {
    // The pending choice remains available for retry.
  }
}

async function clearCustomer() {
  if (!sessionId.value) return
  try {
    await desk.clearCustomer(sessionId.value)
    selectedLabel.value = null
    selectedTicketId.value = null
  } catch {
    // Confirmed state remains visible after a failed request.
  }
}

async function openTicket(ticketId) {
  selectedTicketId.value = ticketId
  desk.clearDetail()
  try {
    await desk.fetchTicket(sessionId.value, ticketId)
  } catch {
    // Axios errors are displayed centrally.
  }
}

function backToList() {
  selectedTicketId.value = null
  desk.clearDetail()
}

function syncPolling() {
  clearInterval(pollTimer)
  pollTimer = null
  if (active.value && available.value && (desk.state?.questionId || runActive.value)) {
    pollTimer = setInterval(load, 1000)
  }
}

watch([active, sessionId, available], ([isActive]) => {
  selectedLabel.value = null
  selectedTicketId.value = null
  if (isActive) void load()
}, { immediate: true })
watch([active, available, () => desk.state?.questionId, runActive], syncPolling, { immediate: true })
onUnmounted(() => clearInterval(pollTimer))
</script>

<style scoped>
.tbs-desk-layout {
  display: grid;
  grid-template-columns: minmax(15rem, 38%) minmax(0, 1fr);
  min-height: 0;
}

.tbs-desk-list-pane {
  min-width: 0;
  border-right: 1px solid var(--app-surface-border);
}

.tbs-desk-detail-pane {
  min-width: 0;
  min-height: 0;
}

.tbs-desk-entry {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

@media (max-width: 1023px) {
  .tbs-desk-layout {
    display: block;
  }

  .tbs-desk-list-pane,
  .tbs-desk-detail-pane {
    height: 100%;
    border-right: 0;
  }
}
</style>
