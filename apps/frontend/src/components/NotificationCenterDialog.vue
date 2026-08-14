<template>
  <q-dialog
    :model-value="modelValue"
    :maximized="$q.screen.lt.sm"
    aria-labelledby="notification-center-title"
    @show="refresh"
    @hide="stopTimestampTimer"
    @update:model-value="$emit('update:modelValue', $event)"
  >
    <q-card class="notification-center app-surface column no-wrap">
      <q-card-section class="row items-center no-wrap q-gutter-sm">
        <q-icon name="notifications" color="primary" size="28px" />
        <div class="col">
          <div id="notification-center-title" class="text-h6">Benachrichtigungen</div>
          <div class="text-caption app-muted">Hinweise und offene Aktionen an einem Ort</div>
        </div>
        <q-btn
          flat
          round
          dense
          icon="done_all"
          aria-label="Alle Benachrichtigungen als gelesen markieren"
          :disable="!notifications.unreadCount || Boolean(notifications.markingId)"
          :loading="notifications.markingAll"
          @click="markAllRead"
        >
          <q-tooltip>Alle als gelesen markieren</q-tooltip>
        </q-btn>
        <q-btn flat round dense icon="close" aria-label="Dialog schließen" v-close-popup />
      </q-card-section>

      <q-separator />

      <q-card-section class="col scroll q-pa-none">
        <section
          v-if="attentionCount || attentionLoading || attentionLoadFailed"
          aria-labelledby="notification-attention-title"
        >
          <div class="row items-center q-px-md q-pt-md q-pb-sm">
            <div id="notification-attention-title" class="text-subtitle2 text-weight-bold">
              Aktion erforderlich
            </div>
            <q-badge
              class="q-ml-sm"
              rounded
              color="warning"
              text-color="dark"
              :label="attentionCount"
            />
          </div>
          <q-linear-progress v-if="attentionLoading && !attentionCount" indeterminate color="warning" />
          <q-banner
            v-if="attentionLoadFailed"
            class="q-ma-md bg-warning text-dark"
            rounded
            role="alert"
          >
            Offene Freigaben oder Rückfragen konnten nicht vollständig geladen werden.
            <template #action>
              <q-btn flat no-caps color="dark" label="Erneut laden" @click="refreshAttention" />
            </template>
          </q-banner>
          <q-list separator>
            <q-item
              v-for="approval in approvals.pending"
              :key="`approval-${approval.id}`"
              clickable
              v-ripple
              @click="openSession(approval.sessionId)"
            >
              <q-item-section avatar>
                <q-avatar color="warning" text-color="dark" icon="pending_actions" />
              </q-item-section>
              <q-item-section>
                <q-item-label class="text-weight-medium">{{ approval.title }}</q-item-label>
                <q-item-label caption class="notification-center__text">
                  {{ approval.summary }}
                </q-item-label>
                <q-item-label caption class="q-mt-xs">
                  {{ approval.sourceSession?.title || sessionTitle(approval.sessionId) }} ·
                  {{ formatRelativeDateTime(approval.createdAt, timestampNow) }}
                </q-item-label>
              </q-item-section>
              <q-item-section side><q-icon name="chevron_right" /></q-item-section>
            </q-item>

            <q-item
              v-for="question in questions.pending"
              :key="`question-${question.id}`"
              clickable
              v-ripple
              @click="openSession(question.sessionId)"
            >
              <q-item-section avatar>
                <q-avatar color="warning" text-color="dark" icon="help_outline" />
              </q-item-section>
              <q-item-section>
                <q-item-label class="text-weight-medium">
                  {{ question.questions?.[0]?.header || 'Rückfrage des Agenten' }}
                </q-item-label>
                <q-item-label caption class="notification-center__text">
                  {{ question.questions?.[0]?.question || 'Der Agent benötigt eine Antwort.' }}
                </q-item-label>
                <q-item-label caption class="q-mt-xs">
                  {{ sessionTitle(question.sessionId) }} ·
                  {{ formatRelativeDateTime(question.createdAt, timestampNow) }}
                </q-item-label>
              </q-item-section>
              <q-item-section side><q-icon name="chevron_right" /></q-item-section>
            </q-item>
          </q-list>
          <q-separator />
        </section>

        <section aria-labelledby="notification-history-title">
          <div class="row items-center q-px-md q-pt-md q-pb-sm">
            <div id="notification-history-title" class="text-subtitle2 text-weight-bold">
              Hinweise
            </div>
            <q-badge
              v-if="notifications.unreadCount"
              class="q-ml-sm"
              rounded
              color="primary"
              :label="notifications.unreadCount"
            />
          </div>

          <div v-if="notifications.loading && !notifications.loaded" class="q-px-md q-pb-md">
            <div v-for="index in 3" :key="index" class="row items-center no-wrap q-gutter-md q-py-sm">
              <q-skeleton type="QAvatar" />
              <div class="col">
                <q-skeleton type="text" width="45%" />
                <q-skeleton type="text" width="80%" />
              </div>
            </div>
          </div>

          <q-banner v-else-if="notifications.loadFailed" class="q-ma-md bg-negative text-white" rounded>
            Die Benachrichtigungen konnten nicht geladen werden.
            <template #action>
              <q-btn flat no-caps color="white" label="Erneut laden" @click="refresh" />
            </template>
          </q-banner>

          <div
            v-else-if="notifications.loaded && !notifications.items.length"
            class="column items-center text-center q-pa-xl"
          >
            <q-icon name="notifications_none" size="48px" color="grey-6" />
            <div class="text-subtitle1 text-weight-medium q-mt-sm">Noch keine Hinweise</div>
            <div class="text-body2 app-muted q-mt-xs">
              Erinnerungen und Ergebnisse von Automationen erscheinen später hier.
            </div>
          </div>

          <q-list v-else separator>
            <q-item
              v-for="notification in notifications.items"
              :key="notification.id"
              :clickable="!notification.readAt || notification.target?.type === 'session'"
              :disable="!notification.readAt && Boolean(notifications.markingId || notifications.markingAll)"
              :class="['relative-position', { 'app-surface-tonal': !notification.readAt }]"
              @click="openNotification(notification)"
            >
              <q-item-section avatar>
                <q-avatar
                  :color="notification.readAt ? 'grey-6' : 'primary'"
                  text-color="white"
                  icon="notifications"
                />
              </q-item-section>
              <q-item-section>
                <q-item-label :class="{ 'text-weight-bold': !notification.readAt }">
                  {{ notification.title }}
                  <span v-if="!notification.readAt" class="app-sr-only">, ungelesen</span>
                </q-item-label>
                <q-item-label v-if="notification.body" caption class="notification-center__text">
                  {{ notification.body }}
                </q-item-label>
                <q-item-label caption class="q-mt-xs">
                  <time
                    :datetime="notification.createdAt"
                    :title="formatAbsoluteDateTime(notification.createdAt)"
                  >
                    {{ formatRelativeDateTime(notification.createdAt, timestampNow) }}
                  </time>
                </q-item-label>
              </q-item-section>
              <q-item-section v-if="notification.target?.type === 'session'" side>
                <q-icon name="chevron_right" />
              </q-item-section>
              <q-inner-loading :showing="notifications.markingId === notification.id" />
            </q-item>
          </q-list>

          <div v-if="notifications.nextCursor || notifications.loadMoreFailed" class="q-pa-md">
            <q-banner v-if="notifications.loadMoreFailed" rounded class="app-surface-tonal q-mb-sm">
              Weitere Hinweise konnten nicht geladen werden.
            </q-banner>
            <q-btn
              outline
              no-caps
              class="full-width"
              color="primary"
              :label="notifications.loadMoreFailed ? 'Erneut versuchen' : 'Weitere laden'"
              :loading="notifications.loadingMore"
              @click="loadMore"
            />
          </div>
        </section>
      </q-card-section>
    </q-card>
  </q-dialog>
</template>

<script setup>
import { computed, onBeforeUnmount, ref } from 'vue'
import { isNavigationFailure, NavigationFailureType, useRouter } from 'vue-router'
import { useApprovalStore } from 'stores/approvals'
import { useChatStore } from 'stores/chat'
import { useInAppNotificationStore } from 'stores/in-app-notifications'
import { useQuestionStore } from 'stores/questions'
import { formatAbsoluteDateTime, formatRelativeDateTime } from 'src/utils/date-time'

defineOptions({ name: 'NotificationCenterDialog' })
defineProps({ modelValue: Boolean })
const emit = defineEmits(['update:modelValue', 'navigated'])

const router = useRouter()
const approvals = useApprovalStore()
const chat = useChatStore()
const notifications = useInAppNotificationStore()
const questions = useQuestionStore()
const timestampNow = ref(new Date())
const attentionCount = computed(() => approvals.pending.length + questions.pending.length)
const attentionLoading = computed(() => approvals.loading || questions.loading)
const attentionLoadFailed = computed(() => approvals.loadFailed || questions.loadFailed)
let timestampTimer = null

function sessionTitle(sessionId) {
  return chat.sessions.find((session) => session.id === sessionId)?.title || 'Session öffnen'
}

function stopTimestampTimer() {
  window.clearInterval(timestampTimer)
  timestampTimer = null
}

async function refresh() {
  stopTimestampTimer()
  timestampNow.value = new Date()
  timestampTimer = window.setInterval(() => { timestampNow.value = new Date() }, 60_000)
  await Promise.allSettled([
    notifications.fetchLatest(),
    approvals.fetchPending({ suppressErrorToast: true }),
    questions.fetchPending({ suppressErrorToast: true })
  ])
}

async function refreshAttention() {
  await Promise.allSettled([
    approvals.fetchPending(),
    questions.fetchPending()
  ])
}

async function openSession(sessionId) {
  if (!sessionId) return
  const failure = await router.push({ name: 'chat', query: { session: sessionId } })
  if (failure && !isNavigationFailure(failure, NavigationFailureType.duplicated)) return
  emit('update:modelValue', false)
  emit('navigated')
}

async function openNotification(notification) {
  if (!notification.readAt && (notifications.markingId || notifications.markingAll)) return
  if (!notification.readAt) {
    try {
      await notifications.markRead(notification.id)
    } catch {
      return
    }
  }
  if (notification.target?.type === 'session') {
    await openSession(notification.target.sessionId)
  }
}

async function markAllRead() {
  try {
    await notifications.markAllRead()
  } catch {
    // The central Axios interceptor displays the error; existing state remains unchanged.
  }
}

async function loadMore() {
  try {
    await notifications.fetchMore()
  } catch {
    // The central Axios interceptor displays the error; existing items remain visible.
  }
}

onBeforeUnmount(stopTimestampTimer)
</script>

<style scoped>
.notification-center {
  width: min(560px, calc(100vw - 32px));
  height: min(720px, calc(100dvh - 32px));
}

.notification-center__text {
  overflow-wrap: anywhere;
  white-space: normal;
}

@media (max-width: 599px) {
  .notification-center {
    width: 100%;
    height: 100dvh;
  }
}
</style>
