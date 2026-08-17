<template>
  <section class="approval-center-app column full-height no-wrap" aria-labelledby="approval-app-title">
    <ControlCenterContentHeader
      icon="pending_actions"
      title="Freigaben"
      title-id="approval-app-title"
      subtitle="Offene Entscheidungen und Rückfragen aus allen Sessions"
      refreshable
      refresh-label="Freigaben aktualisieren"
      :refreshing="loading"
      @refresh="load"
      @close="emit('close')"
    >
      <template #actions>
        <q-badge v-if="attentionCount" rounded color="warning" text-color="dark" :label="attentionCount" />
      </template>
    </ControlCenterContentHeader>

    <q-scroll-area class="col">
      <div class="q-pa-md">
        <q-banner v-if="loadFailed" rounded class="bg-negative text-white q-mb-md">
          Offene Freigaben oder Rückfragen konnten nicht vollständig geladen werden.
          <template #action><q-btn flat no-caps color="white" label="Erneut laden" @click="load" /></template>
        </q-banner>

        <section v-if="questions.pending.length" aria-labelledby="approval-questions-title" class="q-mb-lg">
          <div id="approval-questions-title" class="text-subtitle1 text-weight-bold q-mb-sm">Rückfragen</div>
          <q-list bordered separator class="rounded-borders app-surface">
            <q-item v-for="question in questions.pending" :key="question.id" clickable v-ripple @click="openSession(question.sessionId)">
              <q-item-section avatar><q-avatar color="warning" text-color="dark" icon="help_outline" /></q-item-section>
              <q-item-section>
                <q-item-label class="text-weight-medium">{{ question.questions?.[0]?.header || 'Rückfrage des Agenten' }}</q-item-label>
                <q-item-label caption>{{ question.questions?.[0]?.question || 'Der Agent benötigt eine Antwort.' }}</q-item-label>
                <q-item-label caption class="q-mt-xs">{{ sessionTitle(question.sessionId) }} · {{ formatRelativeDateTime(question.createdAt) }}</q-item-label>
              </q-item-section>
              <q-item-section side><q-icon name="chevron_right" /></q-item-section>
            </q-item>
          </q-list>
        </section>

        <section aria-labelledby="approval-decisions-title">
          <div class="row items-center q-gutter-md q-mb-md">
            <div id="approval-decisions-title" class="text-subtitle1 text-weight-bold">Offene Entscheidungen</div>
            <q-select v-model="selectedType" class="col" outlined dense emit-value map-options label="Typ" :options="typeOptions" />
          </div>
          <template v-if="approvals.loading && !approvals.loaded">
            <q-card v-for="index in 2" :key="index" flat class="app-surface q-mb-md">
              <q-card-section class="row items-center no-wrap q-gutter-md"><q-skeleton type="QAvatar" /><div class="col"><q-skeleton type="text" width="35%" /><q-skeleton type="text" width="75%" /></div></q-card-section>
            </q-card>
          </template>
          <q-card v-else-if="!filteredApprovals.length && !approvals.loadFailed" flat class="app-surface">
            <q-card-section class="column items-center text-center q-py-xl">
              <q-avatar color="positive" text-color="white" icon="check" size="48px" />
              <div class="text-subtitle1 text-weight-bold q-mt-md">Keine offenen Freigaben</div>
            </q-card-section>
          </q-card>
          <article v-for="approval in filteredApprovals" v-else :key="approval.id" class="q-mb-md">
            <div class="row items-center q-gutter-sm q-mb-sm">
              <q-icon :name="approval.sessionId ? 'forum' : 'auto_awesome'" color="primary" />
              <q-btn v-if="approval.sessionId" flat dense no-caps color="primary" :label="approval.sourceSession?.title || 'Session öffnen'" @click="openSession(approval.sessionId)" />
              <span v-else class="text-weight-medium">Dreaming im Hintergrund</span>
              <span class="app-muted text-caption">{{ formatRelativeDateTime(approval.createdAt) }}</span>
            </div>
            <ApprovalCard :approval="approval" :show-draft-action="false" />
          </article>
        </section>
      </div>
    </q-scroll-area>
  </section>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { isNavigationFailure, NavigationFailureType, useRouter } from 'vue-router'
import ApprovalCard from 'components/ApprovalCard.vue'
import ControlCenterContentHeader from 'components/ControlCenterContentHeader.vue'
import { useApprovalStore } from 'stores/approvals'
import { useChatStore } from 'stores/chat'
import { useControlCenterStore } from 'stores/control-center'
import { useQuestionStore } from 'stores/questions'
import { formatRelativeDateTime } from 'src/utils/date-time'

defineOptions({ name: 'ApprovalCenterApp' })
const emit = defineEmits(['close'])

const router = useRouter()
const approvals = useApprovalStore()
const chat = useChatStore()
const controlCenter = useControlCenterStore()
const questions = useQuestionStore()
const selectedType = ref('all')
const typeOptions = [
  { label: 'Alle Typen', value: 'all' },
  { label: 'Agent-Aktionen', value: 'agent_action' },
  { label: 'Memory-Änderungen', value: 'memory_change' }
]
const attentionCount = computed(() => approvals.pending.length + questions.pending.length)
const loading = computed(() => approvals.loading || questions.loading)
const loadFailed = computed(() => approvals.loadFailed || questions.loadFailed)
const filteredApprovals = computed(() => selectedType.value === 'all'
  ? approvals.pending
  : approvals.pending.filter((item) => (item.requestType || 'agent_action') === selectedType.value))

function sessionTitle(sessionId) {
  return chat.sessions.find((session) => session.id === sessionId)?.title || 'Session öffnen'
}

async function load() {
  await Promise.allSettled([approvals.fetchPending(), questions.fetchPending()])
}

async function openSession(sessionId) {
  if (!sessionId) return
  const failure = await router.push({ name: 'chat', query: { session: sessionId } })
  if (failure && !isNavigationFailure(failure, NavigationFailureType.duplicated)) return
  controlCenter.open = false
}

onMounted(load)
</script>
