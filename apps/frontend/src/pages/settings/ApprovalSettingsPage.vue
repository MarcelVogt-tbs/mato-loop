<template>
  <section>
    <header class="settings-section-header">
      <div>
        <div class="row items-center q-gutter-sm">
          <h2 class="text-h6 text-weight-bold q-my-none">Freigaben</h2>
          <q-badge
            v-if="approvals.pendingCount"
            rounded
            color="warning"
            text-color="dark"
            :label="approvals.pendingCount"
          />
        </div>
        <p class="text-body2 app-muted q-mb-none q-mt-xs">
          Offene Entscheidungen aus allen Sessions zentral prüfen.
        </p>
      </div>
      <q-btn
        flat
        round
        icon="refresh"
        aria-label="Freigaben aktualisieren"
        :loading="approvals.loading"
        @click="load"
      >
        <q-tooltip>Aktualisieren</q-tooltip>
      </q-btn>
    </header>

    <div class="row q-mb-md">
      <q-select
        v-model="selectedType"
        class="col-12 col-sm-5"
        outlined
        dense
        emit-value
        map-options
        label="Typ"
        :options="typeOptions"
      />
    </div>

    <q-banner v-if="approvals.loadFailed" rounded class="bg-negative text-white q-mb-md">
      <template #avatar><q-icon name="error_outline" /></template>
      Die offenen Freigaben konnten nicht geladen werden.
      <template #action>
        <q-btn flat no-caps color="white" label="Erneut laden" @click="load" />
      </template>
    </q-banner>

    <template v-if="approvals.loading && !approvals.loaded">
      <q-card v-for="index in 2" :key="index" flat class="app-surface q-mb-md">
        <q-card-section class="row items-center no-wrap q-gutter-md">
          <q-skeleton type="QAvatar" />
          <div class="col">
            <q-skeleton type="text" width="35%" />
            <q-skeleton type="text" width="75%" />
          </div>
        </q-card-section>
      </q-card>
    </template>

    <q-card
      v-else-if="!filteredApprovals.length && !approvals.loadFailed"
      flat
      class="app-surface"
    >
      <q-card-section class="column items-center text-center q-py-xl">
        <q-avatar color="positive" text-color="white" icon="check" size="48px" />
        <div class="text-subtitle1 text-weight-bold q-mt-md">Keine offenen Freigaben</div>
        <div class="text-body2 app-muted q-mt-xs">
          Neue Anfragen erscheinen hier und weiterhin direkt in der betreffenden Session.
        </div>
      </q-card-section>
    </q-card>

    <template v-else>
      <article
        v-for="approval in filteredApprovals"
        :key="approval.id"
        class="q-mb-md"
      >
        <div class="row items-center q-gutter-sm q-mb-sm">
          <q-icon name="forum" color="primary" />
          <router-link
            :to="{ name: 'chat', query: { session: approval.sessionId } }"
            class="text-primary text-weight-medium"
          >
            {{ approval.sourceSession?.title || 'Session öffnen' }}
          </router-link>
          <span class="app-muted text-caption">
            {{ formatRelativeDateTime(approval.createdAt) }}
          </span>
        </div>
        <ApprovalCard :approval="approval" :show-draft-action="false" />
      </article>
    </template>
  </section>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import ApprovalCard from 'components/ApprovalCard.vue'
import { useApprovalStore } from 'stores/approvals'
import { formatRelativeDateTime } from 'src/utils/date-time'

defineOptions({ name: 'ApprovalSettingsPage' })

const approvals = useApprovalStore()
const selectedType = ref('all')
const typeOptions = [
  { label: 'Alle Typen', value: 'all' },
  { label: 'Agent-Aktionen', value: 'agent_action' }
]
const filteredApprovals = computed(() => selectedType.value === 'all'
  ? approvals.pending
  : approvals.pending.filter((item) => (item.requestType || 'agent_action') === selectedType.value))

async function load() {
  try {
    await approvals.fetchPending()
  } catch {
    // The central API interceptor displays the error; the retry action remains available.
  }
}

onMounted(load)
</script>
