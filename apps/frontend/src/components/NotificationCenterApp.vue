<template>
  <section class="notification-center-app column full-height no-wrap" aria-labelledby="notification-app-title">
    <ControlCenterContentHeader
      icon="notifications"
      title="Benachrichtigungen"
      title-id="notification-app-title"
      subtitle="Dauerhafte Hinweise zu Automationsläufen"
      refreshable
      refresh-label="Benachrichtigungen aktualisieren"
      :refreshing="notifications.loading"
      @refresh="refresh"
      @close="emit('close')"
    >
      <template #actions>
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
      </template>
    </ControlCenterContentHeader>

    <q-scroll-area class="col">
      <div class="q-pa-md">
        <q-item class="app-surface-tonal rounded-borders q-mb-md">
          <q-item-section avatar><q-icon name="notifications_active" color="primary" /></q-item-section>
          <q-item-section>
            <q-item-label>Push-Benachrichtigungen</q-item-label>
            <q-item-label caption aria-live="polite">{{ pushStatus }}</q-item-label>
          </q-item-section>
          <q-item-section side>
            <q-toggle
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
        <div v-else-if="notifications.loaded && !notifications.items.length" class="column items-center text-center q-pa-xl">
          <q-icon name="notifications_none" size="48px" color="grey-6" />
          <div class="text-subtitle1 text-weight-medium q-mt-sm">Noch keine Hinweise</div>
          <div class="text-body2 app-muted q-mt-xs">Ergebnisse und Fehler von Automationen erscheinen hier.</div>
        </div>
        <q-list v-else bordered separator class="rounded-borders app-surface">
          <q-item
            v-for="notification in notifications.items"
            :key="notification.id"
            :clickable="!notification.readAt || Boolean(notificationTargetRoute(notification.target))"
            :disable="!notification.readAt && Boolean(notifications.markingId || notifications.markingAll)"
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
import ControlCenterContentHeader from 'components/ControlCenterContentHeader.vue'
import { useInAppNotificationStore } from 'stores/in-app-notifications'
import { formatAbsoluteDateTime, formatRelativeDateTime } from 'src/utils/date-time'
import { notificationTargetRoute } from 'src/utils/notification-target'

defineOptions({ name: 'NotificationCenterApp' })
const emit = defineEmits(['close'])

const router = useRouter()
const notifications = useInAppNotificationStore()
const timestampNow = ref(new Date())
const pushToggleDisabled = computed(() => (
  !notifications.pushSupported
  || !notifications.pushLoaded
  || notifications.pushLoadFailed
  || !notifications.pushServerEnabled
  || !notifications.pushPublicKey
  || notifications.pushPermission === 'denied'
  || notifications.pushLoading
))
const pushStatus = computed(() => {
  if (!notifications.pushSupported) return 'Dieser Browser unterstützt Push-Benachrichtigungen nicht.'
  if (notifications.pushLoadFailed) return 'Die Push-Einstellungen konnten nicht geladen werden.'
  if (!notifications.pushLoaded) return 'Push-Einstellungen werden geladen.'
  if (!notifications.pushServerEnabled) return 'Push-Benachrichtigungen sind auf diesem Server nicht verfügbar.'
  if (notifications.pushPermission === 'denied') return 'Benachrichtigungen sind in den Browser-Einstellungen blockiert.'
  return notifications.pushSubscribed
    ? 'Push-Benachrichtigungen sind auf diesem Gerät aktiv.'
    : 'Push-Benachrichtigungen sind auf diesem Gerät deaktiviert.'
})
let timestampTimer = null

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
  try {
    await notifications.fetchLatest()
  } catch {
    // The central API interceptor displays the error; the retry action remains available.
  }
}

async function openNotification(notification) {
  if (!notification.readAt && (notifications.markingId || notifications.markingAll)) return
  const target = notificationTargetRoute(notification.target)
  if (target) {
    const failure = await router.push(target)
    if (failure && !isNavigationFailure(failure, NavigationFailureType.duplicated)) return
  }
  try {
    if (!notification.readAt) await notifications.markRead(notification.id)
  } catch {
    // Navigation already succeeded; the unread notification remains available for retry.
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
