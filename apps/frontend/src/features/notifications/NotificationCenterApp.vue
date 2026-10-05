<template>
  <section class="notification-center-app column full-height no-wrap" aria-labelledby="notification-app-title">
    <q-scroll-area class="col">
      <div class="q-pa-md">
        <div class="row items-start no-wrap q-gutter-sm q-mb-md">
          <div class="col">
            <h2 id="notification-app-title" class="text-subtitle1 text-weight-bold q-my-none">
              Benachrichtigungen
              <q-badge v-if="attentionCount + notifications.unreadCount" rounded color="primary" :label="attentionCount + notifications.unreadCount" />
            </h2>
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
              :to="{ name: 'admin-notifications' }"
            />
            <q-toggle dense
              v-else-if="notifications.pushConfigured && notifications.pushServerEnabled"
              color="primary"
              :model-value="notifications.pushSubscribed"
              :disable="pushToggleDisabled"
              aria-label="Push-Benachrichtigungen aktivieren"
              @update:model-value="setPushEnabled"
            />
          </q-item-section>
        </q-item>
        <div v-if="(notifications.loading && !notifications.loaded) || (approvals.loading && !approvals.loaded) || (questions.loading && !questions.loaded)">
          <div v-for="index in 3" :key="index" class="row items-center no-wrap q-gutter-md q-py-sm">
            <q-skeleton type="QAvatar" />
            <div class="col"><q-skeleton type="text" width="45%" /><q-skeleton type="text" width="80%" /></div>
          </div>
        </div>
        <q-banner v-if="approvals.loadFailed || questions.loadFailed" rounded class="bg-negative text-white q-mb-md">
          Freigaben oder Rückfragen konnten nicht vollständig geladen werden.
          <template #action><q-btn flat no-caps color="white" label="Erneut laden" @click="refresh" /></template>
        </q-banner>
        <q-banner v-if="notifications.loadFailed" class="bg-negative text-white q-mb-md" rounded>
          Die Benachrichtigungen konnten nicht geladen werden.
          <template #action><q-btn flat no-caps color="white" label="Erneut laden" @click="refresh" /></template>
        </q-banner>
        <AppStateMessage
          v-if="approvals.loaded && questions.loaded && notifications.loaded && !loading && !approvals.loadFailed && !questions.loadFailed && !notifications.loadFailed && !entries.length"
          class="q-pa-xl"
          icon="notifications_none"
          title="Keine Benachrichtigungen"
          description="Neue Antworten, Hinweise, Freigaben und Rückfragen erscheinen hier."
        />
        <q-list v-if="entries.length" bordered separator class="rounded-borders app-surface" aria-label="Benachrichtigungen">
          <template v-for="{ kind, item: notification } in entries" :key="`${kind}:${notification.id}`">
            <q-expansion-item v-if="kind !== 'notification'" expand-separator>
              <template #header>
                <q-item-section avatar>
                  <q-avatar color="warning" text-color="dark" :icon="kind === 'question' ? 'help_outline' : 'verified_user'" />
                </q-item-section>
                <q-item-section>
                  <q-item-label class="text-weight-bold">{{ kind === 'question' ? 'Rückfrage vom Agenten' : notification.title || 'Freigabe erforderlich' }}</q-item-label>
                  <q-item-label caption>{{ kind === 'question' ? 'Antwort erforderlich' : 'Freigabe erforderlich' }}</q-item-label>
                  <q-item-label caption>{{ kind === 'question' ? notification.questions?.[0]?.question : notification.summary }}</q-item-label>
                  <q-item-label caption class="q-mt-xs">
                    <time :datetime="notification.createdAt" :title="formatAbsoluteDateTime(notification.createdAt)">{{ formatRelativeDateTime(notification.createdAt, timestampNow) }}</time>
                  </q-item-label>
                </q-item-section>
              </template>
              <div class="q-pa-sm">
                <div class="row items-center q-gutter-sm q-mb-sm">
                  <q-btn v-if="notification.sessionId" flat dense no-caps color="primary" :label="notification.sourceSession?.title || sessionTitle(notification.sessionId)" :to="{ name: 'chat', query: { session: notification.sessionId } }" />
                  <span v-else-if="kind === 'approval'" class="text-weight-medium">Dreaming im Hintergrund</span>
                </div>
                <QuestionDock v-if="kind === 'question'" :request="notification" :show-stop="false" class="full-width" />
                <ApprovalCard v-else :approval="notification" :show-draft-action="false" />
              </div>
            </q-expansion-item>
            <q-item
              v-else
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
          </template>
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
const entries = computed(() => [
  ...questions.pending.map((item) => ({ kind: 'question', item })),
  ...approvals.pending.map((item) => ({ kind: 'approval', item })),
  ...notifications.items.map((item) => ({ kind: 'notification', item }))
].sort((left, right) => Number(left.kind === 'notification') - Number(right.kind === 'notification')
  || new Date(right.item.createdAt) - new Date(left.item.createdAt)))
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
