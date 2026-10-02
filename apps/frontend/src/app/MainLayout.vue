<template>
  <q-layout
    view="hHh Lpr fFf"
    class="app-shell"
    :class="{
      'app-shell--chat': route.name === 'chat',
      'app-shell--navigation-overlay': showFloatingNavigation,
      'app-shell--floating-chat-controls': route.name === 'chat' && showFloatingControls
    }"
    :style="visualViewportStyle"
  >
    <AppNavigation v-model="drawerOpen" :confirm-discarded-edit="confirmDiscardedEdit" />

    <div v-if="showFloatingControls" class="app-floating-controls" aria-label="Schnellaktionen">
      <q-btn
        v-if="showFloatingNavigation"
        id="app-navigation-toggle"
        class="app-floating-control app-floating-control--flat"
        round
        flat
        icon="menu"
        :aria-label="navigationLabel"
        aria-controls="app-navigation-drawer"
        :aria-expanded="drawerOpen"
        @click="openNavigation"
      />
      <q-space />
      <q-btn
        v-if="showFloatingControlCenter"
        id="control-center-toggle"
        class="app-floating-control"
        round
        unelevated
        icon="apps"
        aria-label="Control Center öffnen"
        :aria-expanded="controlCenterVisible"
        aria-controls="control-center-panel"
        @click="openControlCenter"
      >
        <q-badge
          v-if="notificationBadgeCount"
          floating
          rounded
          :color="attentionCount ? 'warning' : 'primary'"
          :text-color="attentionCount ? 'dark' : 'white'"
          :label="notificationBadgeLabel"
          aria-hidden="true"
        />
        <q-tooltip>Control Center öffnen</q-tooltip>
      </q-btn>
    </div>

    <q-dialog
      v-if="route.name === 'chat' && $q.screen.lt.md"
      :model-value="controlCenterVisible"
      aria-label="Control Center"
      maximized
      transition-show="slide-left"
      transition-hide="slide-right"
      @update:model-value="setControlCenterOpen"
    >
      <q-card class="app-file-dialog">
        <ControlCenterPanel />
      </q-card>
    </q-dialog>

    <q-page-container>
      <router-view />
    </q-page-container>
  </q-layout>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useQuasar } from 'quasar'
import { onBeforeRouteLeave, onBeforeRouteUpdate, useRoute } from 'vue-router'
import AppNavigation from 'src/app/AppNavigation.vue'
import ControlCenterPanel from 'src/app/control-center/ControlCenterPanel.vue'
import { useShellSync } from 'src/app/useShellSync'
import { useApprovalStore } from 'stores/approvals'
import { useAutomationStore } from 'stores/automations'
import { useChatStore } from 'stores/chat'
import { useControlCenterStore } from 'stores/control-center'
import { useInAppNotificationStore } from 'stores/in-app-notifications'
import { useQuestionStore } from 'stores/questions'
import { useWorkspaceStore } from 'stores/workspace'
import { useConfirm } from 'src/composables/confirm'

defineOptions({ name: 'MainLayout' })

const drawerOpen = ref(true)
const $q = useQuasar()
const route = useRoute()
const chat = useChatStore()
const controlCenter = useControlCenterStore()
const workspace = useWorkspaceStore()
const { confirm } = useConfirm()
const approvals = useApprovalStore()
const automations = useAutomationStore()
const notifications = useInAppNotificationStore()
const questions = useQuestionStore()
const showFloatingNavigation = computed(() => !drawerOpen.value)
const controlCenterVisible = computed(() => (
  controlCenter.open && (!$q.screen.lt.md || !controlCenter.restoredOpen)
))
const showFloatingControlCenter = computed(() => route.name === 'chat' && !controlCenterVisible.value)
const showFloatingControls = computed(() => (
  showFloatingNavigation.value || showFloatingControlCenter.value
))
const attentionCount = computed(() => (
  approvals.pending.length + questions.pending.length
))
const notificationBadgeCount = computed(() => attentionCount.value + notifications.unreadCount)
const notificationBadgeLabel = computed(() => notificationBadgeCount.value > 99
  ? '99+'
  : String(notificationBadgeCount.value || ''))
const notificationBellLabel = computed(() => {
  const parts = []
  if (attentionCount.value) {
    parts.push(`${attentionCount.value} ${attentionCount.value === 1 ? 'Aktion' : 'Aktionen'} erforderlich`)
  }
  if (notifications.unreadCount) {
    parts.push(`${notifications.unreadCount} ungelesene ${notifications.unreadCount === 1 ? 'Benachrichtigung' : 'Benachrichtigungen'}`)
  }
  return parts.length ? `Benachrichtigungen öffnen, ${parts.join(', ')}` : 'Benachrichtigungen öffnen'
})
const navigationLabel = computed(() => {
  if (!attentionCount.value && !notifications.unreadCount) return 'Navigation öffnen'
  return `Navigation öffnen, ${notificationBellLabel.value.replace('Benachrichtigungen öffnen, ', '')}`
})
const { visualViewportStyle } = useShellSync({ drawerOpen, controlCenterVisible })

function openNavigation() {
  drawerOpen.value = true
}

async function confirmDiscardedEdit({ discard = true } = {}) {
  if (!workspace.hasDirtyEditors && !automations.dirty) return true
  const accepted = await confirm(
    'Sollen die ungespeicherten Änderungen verworfen werden?',
    'Änderungen verwerfen?',
    { okLabel: 'Verwerfen' }
  )
  if (accepted && discard) {
    workspace.discardAllEdits()
    automations.dirty = false
  }
  return accepted
}

async function openControlCenter() {
  try {
    await controlCenter.openApp(controlCenter.activeContentId || 'files')
  } catch {
    // The central API interceptor displays the error and restores the previous tabs.
  }
}

async function setControlCenterOpen(open) {
  try {
    await controlCenter.setOpen(open)
  } catch {
    // The central API interceptor displays the error and restores the previous state.
  }
}

onBeforeRouteUpdate((to, from) => {
  if (to.name === 'chat' && from.name === 'chat'
    && to.query.session === from.query.session && to.query.project === from.query.project) return true
  if (to.name === 'chat' && from.name === 'chat'
    && to.query.session === from.query.session
    && (to.query.subagent !== from.query.subagent
      || (to.query.project !== from.query.project
        && to.query.session === chat.current?.id
        && !controlCenter.fileTabs.some((tab) => (
          tab.fileTarget.scope === 'session'
          && tab.fileTarget.rootIdentity !== chat.current?.id
          && workspace.fileTabDirty(tab.tabId)
        ))))) return true
  return confirmDiscardedEdit()
})
onBeforeRouteLeave(confirmDiscardedEdit)
</script>
