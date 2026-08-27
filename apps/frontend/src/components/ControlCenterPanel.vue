<template>
  <section id="control-center-panel" class="control-center column full-height no-wrap" aria-label="Control Center">
    <q-toolbar class="q-px-sm">
      <div
        v-if="chat.current"
        class="control-center__resources col row items-center no-wrap overflow-hidden text-caption app-muted"
      >
        <div class="control-center__resource row items-center no-wrap q-mr-sm" tabindex="0" :aria-label="requestDescription">
          <q-icon name="forum" size="18px" class="q-mr-xs" />
          <span class="ellipsis">{{ requestLabel }}</span>
          <q-tooltip>{{ requestDescription }}</q-tooltip>
        </div>
        <div
          v-if="contextPercentage !== null"
          class="control-center__resource row items-center no-wrap q-mr-sm"
          tabindex="0"
          :aria-label="contextDescription"
        >
          <q-icon name="token" size="18px" class="q-mr-xs" />
          <q-linear-progress
            :value="contextProgress"
            size="4px"
            rounded
            class="control-center__resource-progress q-mr-xs"
            aria-hidden="true"
          />
          <span class="ellipsis">≈ {{ contextPercentage }} %</span>
          <q-tooltip>{{ contextDescription }}</q-tooltip>
        </div>
      </div>
      <q-space v-if="!chat.current" />
      <q-btn
        v-for="item in controlCenterGlobalSections"
        :key="item.id"
        flat
        round
        dense
        class="col-auto"
        :icon="item.icon"
        :color="controlCenter.activeGlobalSectionId === item.id ? 'primary' : undefined"
        :loading="!controlCenter.ready && controlCenter.requestedAppId === item.id"
        :aria-label="`${item.label} öffnen`"
        @click="openContent(item.id)"
      >
        <q-badge
          v-if="badge(item.id)"
          floating
          rounded
          color="negative"
          text-color="white"
          :label="badge(item.id)"
        />
        <q-tooltip>{{ item.label }}</q-tooltip>
      </q-btn>
    </q-toolbar>
    <q-separator />

    <div class="control-center__tabs row items-center no-wrap q-pl-sm">
      <q-tabs
        :model-value="controlCenter.activeGlobalSectionId ? null : controlCenter.activeAppId"
        class="col"
        dense
        no-caps
        inline-label
        align="left"
        active-color="primary"
        indicator-color="primary"
        mobile-arrows
        outside-arrows
        @update:model-value="activate"
      >
        <q-tab
          v-for="tab in controlCenter.tabs"
          :key="tab.appId"
          :name="tab.appId"
          :icon="content(tab.appId).icon"
          :label="content(tab.appId).label"
          class="control-center__tab"
          :class="{
            'control-center__tab--dragging': draggedIntegrationId === tab.appId,
            'control-center__tab--drop-before': dropTarget?.appId === tab.appId && !dropTarget.after,
            'control-center__tab--drop-after': dropTarget?.appId === tab.appId && dropTarget.after
          }"
          :disable="controlCenter.saving || !controlCenter.ready"
          :draggable="controlCenter.tabs.length > 1 && controlCenter.ready && !controlCenter.saving"
          @dragstart="startDrag($event, tab.appId)"
          @dragover.prevent="updateDropTarget($event, tab.appId)"
          @drop.prevent="dropIntegration"
          @dragend="endDrag"
        />
      </q-tabs>

      <q-btn
        v-if="launcherIntegrations.length"
        flat
        round
        dense
        icon="add"
        aria-label="Integration hinzufügen"
        :disable="!launcherIntegrations.length || controlCenter.saving || !controlCenter.ready"
      >
        <q-tooltip>Integration hinzufügen</q-tooltip>
        <q-menu anchor="bottom right" self="top right" class="app-action-menu">
          <q-list padding>
            <q-item
              v-for="item in launcherIntegrations"
              :key="item.id"
              clickable
              v-ripple
              v-close-popup
              @click="openContent(item.id)"
            >
              <q-item-section avatar><q-icon :name="item.icon" /></q-item-section>
              <q-item-section>{{ item.label }}</q-item-section>
            </q-item>
          </q-list>
        </q-menu>
      </q-btn>
    </div>
    <q-separator />

    <div class="control-center__content col relative-position">
      <component
        :is="content(tab.appId).component"
        v-for="tab in controlCenter.tabs"
        v-show="controlCenter.activeContentId === tab.appId"
        :key="tab.appId"
        class="fit"
        @close="closePanel"
        @remove="removeIntegration"
        @move="moveIntegration"
      />
      <component
        :is="item.component"
        v-for="item in controlCenterGlobalSections"
        v-show="controlCenter.activeContentId === item.id"
        :key="item.id"
        class="fit"
        @close="closePanel"
      />
      <div v-if="!controlCenter.activeContentId" class="fit column no-wrap">
        <ControlCenterContentHeader icon="extension" title="Integrationen" @close="closePanel" />
        <div class="col column flex-center text-center q-pa-xl">
          <q-icon name="extension_off" size="52px" color="grey-6" />
          <div class="text-subtitle1 text-weight-medium q-mt-md">Keine Integration in dieser Session</div>
          <div class="text-body2 app-muted q-mt-xs q-mb-md">Füge eine Integration hinzu, um sie hier zu verwenden.</div>
          <q-btn
            unelevated
            no-caps
            color="primary"
            icon="add"
            label="Integration hinzufügen"
            :disable="!launcherIntegrations.length || !controlCenter.ready"
          >
            <q-menu anchor="bottom middle" self="top middle" class="app-action-menu">
              <q-list padding>
                <q-item v-for="item in launcherIntegrations" :key="item.id" clickable v-ripple v-close-popup @click="openContent(item.id)">
                  <q-item-section avatar><q-icon :name="item.icon" /></q-item-section>
                  <q-item-section>{{ item.label }}</q-item-section>
                </q-item>
              </q-list>
            </q-menu>
          </q-btn>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup>
import { computed, nextTick, ref } from 'vue'
import ControlCenterContentHeader from 'components/ControlCenterContentHeader.vue'
import { useApprovalStore } from 'stores/approvals'
import { useControlCenterStore } from 'stores/control-center'
import { useInAppNotificationStore } from 'stores/in-app-notifications'
import { useQuestionStore } from 'stores/questions'
import { useMailStore } from 'stores/mail'
import { useChatStore } from 'stores/chat'
import { useProjectStore } from 'stores/projects'
import { useWorkspaceStore } from 'stores/workspace'
import { useConfirm } from 'src/composables/confirm'
import {
  controlCenterContentsById,
  controlCenterGlobalSections,
  controlCenterIntegrations
} from 'src/control-center/apps'
import { codingControlCenterAvailable, mailControlCenterAvailable } from 'src/control-center/manifest'
import {
  formatMetricInteger,
  modelContextPercentage
} from 'src/utils/resource-metrics'

defineOptions({ name: 'ControlCenterPanel' })

const emit = defineEmits(['close'])
const { confirm } = useConfirm()
const controlCenter = useControlCenterStore()
const workspace = useWorkspaceStore()
const approvals = useApprovalStore()
const questions = useQuestionStore()
const notifications = useInAppNotificationStore()
const mail = useMailStore()
const chat = useChatStore()
const projects = useProjectStore()
const draggedIntegrationId = ref(null)
const dropTarget = ref(null)
const closedIntegrations = computed(() => controlCenterIntegrations.filter(
  (item) => !controlCenter.tabs.some((tab) => tab.appId === item.id)
))
const activeProject = computed(() => projects.items.find(
  (project) => project.id === (chat.current ? chat.current.projectId : chat.sessionsProjectId)
))
const launcherIntegrations = computed(() => closedIntegrations.value.filter((item) => (
  (item.id !== 'mail' || mailControlCenterAvailable(mail))
  && (item.id !== 'coding' || codingControlCenterAvailable(activeProject.value))
)))
const requestCount = computed(() => {
  const count = chat.current?.userRequestCount
  return Number.isInteger(count) && count >= 0 ? count : null
})
const requestLabel = computed(() => formatMetricInteger(requestCount.value) ?? '–')
const requestDescription = computed(() => requestCount.value === null
  ? 'Anfragezahl nicht verfügbar.'
  : `${formatMetricInteger(requestCount.value)} Nutzeranfragen in dieser Session.`)
const contextPercentage = computed(() => modelContextPercentage(chat.current?.latestModelContext))
const contextProgress = computed(() => Math.min((contextPercentage.value || 0) / 100, 1))
const contextDescription = computed(() => {
  const context = chat.current?.latestModelContext
  return `Letzter Modellaufruf: ≈ ${contextPercentage.value} % (${formatMetricInteger(context.estimatedInputTokens)} / ${formatMetricInteger(context.inputBudgetTokens)} Tokens).`
})

function content(contentId) {
  return controlCenterContentsById.get(contentId)
}

function badge(appId) {
  const count = appId === 'approvals'
    ? approvals.pending.length + questions.pending.length
    : appId === 'notifications' ? notifications.unreadCount : 0
  return count > 99 ? '99+' : count || null
}

async function confirmDiscardedEdit() {
  if (!workspace.editorDirty) return true
  const accepted = await confirm(
    'Sollen die ungespeicherten Änderungen verworfen werden?',
    'Änderungen verwerfen?',
    { okLabel: 'Verwerfen' }
  )
  if (accepted) workspace.discardEdit()
  return accepted
}

async function closePanel() {
  if (!await confirmDiscardedEdit()) return
  try {
    await controlCenter.setOpen(false)
  } catch {
    // The central API interceptor displays the error and restores the open panel.
    return
  }
  emit('close')
  await nextTick()
  document.getElementById('control-center-toggle')?.focus()
}

async function removeIntegration(appId) {
  const item = content(appId)
  if (!item) return
  const unsavedHint = appId === 'files' && workspace.editorDirty
    ? ' Ungespeicherte Änderungen gehen verloren.'
    : ''
  const accepted = await confirm(
    `„${item.label}“ wird aus dieser Session entfernt.${unsavedHint} Gespeicherte Daten werden nicht gelöscht. Die Integration kann später erneut hinzugefügt werden.`,
    'Integration aus Session entfernen?',
    { okLabel: 'Entfernen', okColor: 'negative' }
  )
  if (!accepted) return
  if (appId === 'files') {
    if (workspace.editorDirty) workspace.discardEdit()
    workspace.returnToCurrentContext()
  }
  try {
    await controlCenter.removeIntegration(appId)
  } catch {
    // The central API interceptor displays the error and the store restores server state.
  }
}

async function activate(appId) {
  try {
    await controlCenter.activate(appId)
  } catch {
    // The central API interceptor displays the error and the store restores server state.
  }
}

async function openContent(contentId) {
  try {
    await controlCenter.openApp(contentId)
  } catch {
    // The central API interceptor displays the error and the store restores server state.
  }
}

async function moveIntegration(appId, offset) {
  try {
    await controlCenter.moveIntegration(appId, offset)
  } catch {
    // The central API interceptor displays the error and the store restores server state.
  }
}

function startDrag(event, appId) {
  draggedIntegrationId.value = appId
  event.dataTransfer.effectAllowed = 'move'
  event.dataTransfer.setData('text/plain', appId)
}

function updateDropTarget(event, appId) {
  if (!draggedIntegrationId.value || draggedIntegrationId.value === appId) {
    dropTarget.value = null
    return
  }
  const bounds = event.currentTarget.getBoundingClientRect()
  dropTarget.value = { appId, after: event.clientX > bounds.left + bounds.width / 2 }
  event.dataTransfer.dropEffect = 'move'
}

async function dropIntegration() {
  const dragged = draggedIntegrationId.value
  const target = dropTarget.value
  endDrag()
  if (!dragged || !target) return
  try {
    await controlCenter.reorderIntegration(dragged, target.appId, target.after)
  } catch {
    // The central API interceptor displays the error and the store restores server state.
  }
}

function endDrag() {
  draggedIntegrationId.value = null
  dropTarget.value = null
}
</script>
