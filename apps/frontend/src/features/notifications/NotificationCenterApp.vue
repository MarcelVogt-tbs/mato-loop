<template>
  <section class="notification-center-app column full-height no-wrap" aria-labelledby="notification-app-title">
    <q-scroll-area class="col">
      <div class="q-pa-md">
        <div class="row items-start no-wrap q-gutter-sm q-mb-md">
          <div class="col">
            <h2 id="notification-app-title" class="text-subtitle1 text-weight-bold q-my-none">Benachrichtigungen</h2>
            <div class="text-caption app-muted">Antworten, Hinweise und offene Aufgaben</div>
          </div>
          <q-btn
            flat
            round
            dense
            icon="done_all"
            aria-label="Alle Benachrichtigungen als gelesen markieren"
            :disable="!notifications.unreadCount || Boolean(notifications.markingId || notifications.deletingId)"
            :loading="notifications.markingAll"
            @click="markAllRead"
          >
            <q-tooltip>Alle als gelesen markieren</q-tooltip>
          </q-btn>
          <q-btn
            flat
            round
            dense
            icon="refresh"
            aria-label="Benachrichtigungen aktualisieren"
            :loading="loading"
            @click="refresh"
          >
            <q-tooltip>Benachrichtigungen aktualisieren</q-tooltip>
          </q-btn>
        </div>
        <section aria-labelledby="notification-actions-title" class="q-mb-lg">
          <div class="row items-center q-gutter-sm q-mb-md">
            <h3 id="notification-actions-title" class="text-subtitle1 text-weight-bold q-my-none">Aktion erforderlich</h3>
            <q-badge v-if="attentionCount" rounded color="warning" text-color="dark" :label="attentionCount" />
          </div>
          <q-banner v-if="approvals.loadFailed || questions.loadFailed" rounded class="bg-negative text-white q-mb-md">
            Freigaben oder Rückfragen konnten nicht vollständig geladen werden.
            <template #action><q-btn flat no-caps color="white" label="Erneut laden" @click="refresh" /></template>
          </q-banner>
          <div v-if="questions.loading && !questions.loaded" class="q-mb-md">
            <q-skeleton type="text" width="45%" /><q-skeleton type="text" width="80%" />
          </div>
          <section v-if="questions.pending.length" aria-labelledby="notification-questions-title" class="q-mb-lg">
            <h4 id="notification-questions-title" class="text-subtitle2 text-weight-bold q-mt-none q-mb-sm">Rückfragen</h4>
            <q-expansion-item
              v-for="question in questions.pending" :key="question.id"
              icon="help_outline" label="Rückfrage vom Agenten" :caption="question.questions?.[0]?.question"
              class="app-surface rounded-borders q-mb-sm" expand-separator
            >
              <div class="q-pa-sm">
                <div class="row items-center q-gutter-sm q-mb-sm">
                  <q-btn v-if="question.sessionId" flat dense no-caps color="primary" :label="sessionTitle(question.sessionId)" :to="{ name: 'chat', query: { session: question.sessionId } }" />
                  <span class="app-muted text-caption">{{ formatRelativeDateTime(question.createdAt, timestampNow) }}</span>
                </div>
                <QuestionDock :request="question" :show-stop="false" class="full-width" />
              </div>
            </q-expansion-item>
          </section>
          <section aria-labelledby="notification-decisions-title">
            <h4 id="notification-decisions-title" class="text-subtitle2 text-weight-bold q-mt-none q-mb-sm">Offene Entscheidungen</h4>
            <template v-if="approvals.loading && !approvals.loaded">
              <q-card v-for="index in 2" :key="index" flat class="app-surface q-mb-md">
                <q-card-section class="row items-center no-wrap q-gutter-md"><q-skeleton type="QAvatar" /><div class="col"><q-skeleton type="text" width="35%" /><q-skeleton type="text" width="75%" /></div></q-card-section>
              </q-card>
            </template>
            <div v-else-if="approvals.loaded && !approvals.pending.length && !approvals.loadFailed" class="text-body2 app-muted">
              Keine offenen Freigaben
            </div>
            <q-expansion-item
              v-for="approval in approvals.pending" :key="approval.id"
              icon="verified_user" :label="approval.title || 'Freigabe erforderlich'" :caption="approval.summary"
              class="app-surface rounded-borders q-mb-sm" expand-separator
            >
              <div class="q-pa-sm">
                <div class="row items-center q-gutter-sm q-mb-sm">
                  <q-icon :name="approval.sessionId ? 'forum' : 'auto_awesome'" color="primary" />
                  <q-btn v-if="approval.sessionId" flat dense no-caps color="primary" :label="approval.sourceSession?.title || 'Session öffnen'" :to="{ name: 'chat', query: { session: approval.sessionId } }" />
                  <span v-else class="text-weight-medium">Dreaming im Hintergrund</span>
                  <span class="app-muted text-caption">{{ formatRelativeDateTime(approval.createdAt, timestampNow) }}</span>
                </div>
                <ApprovalCard :approval="approval" :show-draft-action="false" />
              </div>
            </q-expansion-item>
          </section>
        </section>
        <q-separator class="q-mb-lg" />
        <h3 id="notification-info-title" class="text-subtitle1 text-weight-bold q-mt-none q-mb-md">Antworten und Hinweise</h3>
        <q-item class="app-surface-tonal rounded-borders q-mb-md">
          <q-item-section avatar><q-icon name="notifications_active" color="primary" /></q-item-section>
          <q-item-section>
            <q-item-label>Push-Benachrichtigungen</q-item-label>
            <q-item-label caption aria-live="polite">{{ pushStatus }}</q-item-label>
          </q-item-section>
          <q-item-section side>
            <q-btn
              v-if="(!notifications.pushConfigured || !notifications.pushServerEnabled) && auth.isAdmin"
              flat
              no-caps
              color="primary"
              icon="settings"
              label="Einrichten"
              :to="{ name: 'settings-notifications' }"
            />
            <q-toggle
              v-else-if="notifications.pushConfigured && notifications.pushServerEnabled"
              color="primary"
              :model-value="notifications.pushSubscribed"
              :disable="pushToggleDisabled"
              aria-label="Push-Benachrichtigungen aktivieren"
              @update:model-value="setPushEnabled"
            />
          </q-item-section>
        </q-item>
        <div v-if="notifications.loading && !notifications.loaded">
          <div v-for="index in 3" :key="index" class="row items-center no-wrap q-gutter-md q-py-sm">
            <q-skeleton type="QAvatar" />
            <div class="col"><q-skeleton type="text" width="45%" /><q-skeleton type="text" width="80%" /></div>
          </div>
        </div>
        <q-banner v-else-if="notifications.loadFailed" class="bg-negative text-white" rounded>
          Die Benachrichtigungen konnten nicht geladen werden.
          <template #action><q-btn flat no-caps color="white" label="Erneut laden" @click="refresh" /></template>
        </q-banner>
        <AppStateMessage
          v-else-if="notifications.loaded && !notifications.items.length"
          class="q-pa-xl"
          icon="notifications_none"
          title="Keine Benachrichtigungen"
          description="Neue Antworten und Hinweise erscheinen hier, wenn du sie nicht bereits in der Session liest."
        />
        <q-list v-else bordered separator class="rounded-borders app-surface">
          <q-item
            v-for="notification in notifications.items"
            :key="notification.id"
            :clickable="!notification.readAt || Boolean(notificationTargetRoute(notification.target))"
            :disable="Boolean(notifications.markingId || notifications.markingAll || notifications.deletingId)"
            :class="['relative-position', { 'app-surface-tonal': !notification.readAt }]"
            @click="openNotification(notification)"
          >
            <q-item-section avatar>
              <q-avatar :color="notification.readAt ? 'grey-6' : 'primary'" text-color="white" icon="notifications" />
            </q-item-section>
            <q-item-section>
              <q-item-label :class="{ 'text-weight-bold': !notification.readAt }">
                {{ notification.title }}<span v-if="!notification.readAt" class="app-sr-only">, ungelesen</span>
              </q-item-label>
              <q-item-label v-if="notification.body" caption class="notification-center__text">{{ notification.body }}</q-item-label>
              <q-item-label caption class="q-mt-xs">
                <time :datetime="notification.createdAt" :title="formatAbsoluteDateTime(notification.createdAt)">
                  {{ formatRelativeDateTime(notification.createdAt, timestampNow) }}
                </time>
              </q-item-label>
            </q-item-section>
            <q-item-section v-if="notificationTargetRoute(notification.target)" side><q-icon name="chevron_right" /></q-item-section>
            <q-item-section side>
              <q-btn flat round dense icon="delete_outline" aria-label="Benachrichtigung löschen" @click.stop="deleteNotification(notification)">
                <q-tooltip>Löschen</q-tooltip>
              </q-btn>
            </q-item-section>
            <q-inner-loading :showing="notifications.markingId === notification.id" />
          </q-item>
        </q-list>
        <div v-if="notifications.nextCursor || notifications.loadMoreFailed" class="q-pt-md">
          <q-banner v-if="notifications.loadMoreFailed" rounded class="app-surface-tonal q-mb-sm">Weitere Hinweise konnten nicht geladen werden.</q-banner>
          <q-btn outline no-caps class="full-width" color="primary" :label="notifications.loadMoreFailed ? 'Erneut versuchen' : 'Weitere laden'" :loading="notifications.loadingMore" @click="loadMore" />
        </div>
      </div>
    </q-scroll-area>
  </section>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { isNavigationFailure, NavigationFailureType, useRouter } from 'vue-router'
import AppStateMessage from 'src/shared/ui/AppStateMessage.vue'
import ApprovalCard from 'src/features/approvals/ApprovalCard.vue'
import QuestionDock from 'src/features/questions/QuestionDock.vue'
import { useApprovalStore } from 'stores/approvals'
import { useAuthStore } from 'stores/auth'
import { useChatStore } from 'stores/chat'
import { useControlCenterStore } from 'stores/control-center'
import { useInAppNotificationStore } from 'stores/in-app-notifications'
import { useQuestionStore } from 'stores/questions'
import { formatAbsoluteDateTime, formatRelativeDateTime } from 'src/utils/date-time'
import { notificationTargetRoute } from 'src/utils/notification-target'
import { useConfirm } from 'src/composables/confirm'

defineOptions({ name: 'NotificationCenterApp' })

const router = useRouter()
const { confirm } = useConfirm()
const approvals = useApprovalStore()
const auth = useAuthStore()
const chat = useChatStore()
const controlCenter = useControlCenterStore()
const notifications = useInAppNotificationStore()
const questions = useQuestionStore()
const attentionCount = computed(() => approvals.pending.length + questions.pending.length)
const loading = computed(() => approvals.loading || questions.loading || notifications.loading)
const timestampNow = ref(new Date())
const pushToggleDisabled = computed(() => (
  !notifications.pushSupported
  || !notifications.pushLoaded
  || notifications.pushLoadFailed
  || !notifications.pushServerEnabled
  || !notifications.pushPublicKey
  || !notifications.pushRegistrationReady
  || notifications.pushPermission === 'denied'
  || notifications.pushLoading
))
const pushStatus = computed(() => {
  if (!notifications.pushSupported) return 'Dieser Browser unterstützt Push-Benachrichtigungen nicht.'
  if (notifications.pushLoadFailed) return 'Die Push-Einstellungen konnten nicht geladen werden.'
  if (!notifications.pushLoaded) return 'Push-Einstellungen werden geladen.'
  if (!notifications.pushConfigured) return auth.isAdmin
    ? 'Web Push muss für diese Instanz noch eingerichtet werden.'
    : 'Web Push wurde für diese Instanz noch nicht eingerichtet.'
  if (!notifications.pushServerEnabled) return 'Web Push ist für diese Instanz deaktiviert.'
  if (!notifications.pushRegistrationReady) return 'Der Push-Dienst wird vorbereitet.'
  if (notifications.pushPermission === 'denied') return 'Benachrichtigungen sind in den Browser-Einstellungen blockiert.'
  return notifications.pushSubscribed
    ? 'Push-Benachrichtigungen sind auf diesem Gerät aktiv.'
    : 'Push-Benachrichtigungen sind auf diesem Gerät deaktiviert.'
})
let timestampTimer = null

function sessionTitle(sessionId) {
  return chat.cachedSessions.find((session) => session.id === sessionId)?.title || 'Agent-Auftrag'
}

async function setPushEnabled(enabled) {
  try {
    if (enabled) await notifications.enablePush()
    else await notifications.disablePush()
  } catch {
    // Axios errors are displayed centrally; browser permission state remains visible above.
  }
}

async function refresh() {
  timestampNow.value = new Date()
  await Promise.allSettled([approvals.fetchPending(), questions.fetchPending(), notifications.fetchLatest()])
}

async function openNotification(notification) {
  if (notifications.markingId || notifications.markingAll || notifications.deletingId) return
  const target = notificationTargetRoute(notification.target)
  if (target) {
    const failure = await router.push(target)
    if (failure && !isNavigationFailure(failure, NavigationFailureType.duplicated)) return
  }
  try {
    if (!notification.readAt) await notifications.markRead(notification.id)
    if (target?.name === 'chat' && controlCenter.open) await controlCenter.setOpen(false)
  } catch {
    // Navigation already succeeded; the unread notification remains available for retry.
  }
}

async function deleteNotification(notification) {
  if (!await confirm('Soll diese Benachrichtigung gelöscht werden?', 'Benachrichtigung löschen?', { okLabel: 'Löschen' })) return
  try {
    await notifications.remove(notification.id)
  } catch {
    // API errors are displayed centrally; the notification remains available.
  }
}

async function markAllRead() {
  try {
    await notifications.markAllRead()
  } catch {
    // The central API interceptor displays the error; existing state remains unchanged.
  }
}

async function loadMore() {
  try {
    await notifications.fetchMore()
  } catch {
    // The central API interceptor displays the error; existing items remain visible.
  }
}

onMounted(() => {
  void refresh()
  timestampTimer = window.setInterval(() => { timestampNow.value = new Date() }, 60_000)
})
onBeforeUnmount(() => window.clearInterval(timestampTimer))
</script>

<style scoped>
.notification-center-app :deep(.question-dock),
.notification-center-app :deep(.question-dock__body) {
  max-height: none;
}
</style>
