<template>
  <section id="control-center-panel" class="control-center column full-height no-wrap" aria-label="Control Center">
    <div class="control-center__tabs row items-center no-wrap q-pl-sm">
      <q-tabs
        :model-value="activeFlexibleTabId"
        class="col"
        dense
        no-caps
        inline-label
        align="left"
        :breakpoint="0"
        indicator-color="transparent"
        mobile-arrows
        outside-arrows
        @update:model-value="activate"
      >
        <div
          v-for="tab in flexibleTabs"
          :key="tab.tabId"
          class="control-center__tab-shell row items-stretch no-wrap"
          :class="{
            'control-center__tab-shell--active': controlCenter.activeAppId === tab.tabId && !controlCenter.activeGlobalSectionId,
            'control-center__tab--dragging': draggedIntegrationId === tab.tabId,
            'control-center__tab--drop-before': dropTarget?.appId === tab.tabId && !dropTarget.after,
            'control-center__tab--drop-after': dropTarget?.appId === tab.tabId && dropTarget.after
          }"
          :draggable="controlCenter.ready && !controlCenter.saving"
          @dragstart="startDrag($event, tab.tabId)"
          @dragover.prevent="updateDropTarget($event, tab.tabId)"
          @drop.prevent="dropIntegration"
          @dragend="endDrag"
        >
          <q-tab
            :id="`control-center-tab-${tab.tabId}`"
            :name="tab.tabId"
            :alert="tab.appId === 'terminal' && terminal.pendingProposal ? 'warning' : false"
            :alert-icon="tab.appId === 'terminal' ? 'priority_high' : undefined"
            :aria-label="tabAriaLabel(tab)"
            class="control-center__tab"
            :disable="controlCenter.saving || !controlCenter.ready"
          >
            <q-icon :name="content(tab.appId).icon" size="20px" class="control-center__tab-icon" />
            <span class="control-center__tab-label">{{ tabLabel(tab) }}</span>
            <q-tooltip>{{ tabLabel(tab) }}</q-tooltip>
          </q-tab>
          <q-btn
            flat
            square
            dense
            icon="close"
            class="control-center__tab-close"
            :aria-label="`${tabLabel(tab)} schließen`"
            :disable="controlCenter.saving || !controlCenter.ready"
            @click.stop="closeTab(tab)"
            @keydown.stop
          >
            <q-tooltip>{{ tab.kind === 'file' ? 'Datei schließen' : 'App aus Session entfernen' }}</q-tooltip>
          </q-btn>
        </div>

        <q-btn
          v-if="launcherIntegrations.length"
          flat
          square
          dense
          icon="add"
          class="control-center__tab-add self-stretch"
          aria-label="App hinzufügen"
          :disable="controlCenter.saving || !controlCenter.ready"
        >
          <q-tooltip>App hinzufügen</q-tooltip>
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
      </q-tabs>

      <q-btn
        id="control-center-session-button"
        flat
        round
        dense
        class="col-auto"
        icon="tune"
        :color="!controlCenter.activeGlobalSectionId && controlCenter.activeAppId === 'session' ? 'primary' : undefined"
        :loading="!controlCenter.ready && controlCenter.requestedAppId === 'session'"
        aria-label="Session öffnen"
        @click="openContent('session')"
      >
        <q-tooltip>Session</q-tooltip>
      </q-btn>

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

      <q-btn
        flat
        round
        dense
        class="col-auto"
        icon="close"
        aria-label="Control Center schließen"
        @click="closePanel"
      >
        <q-tooltip>Control Center schließen</q-tooltip>
      </q-btn>
    </div>
    <q-separator />

    <div class="control-center__content col relative-position">
      <component
        :is="content(tab.appId).component"
        v-for="tab in controlCenter.tabs.filter((item) => item.kind === 'app' && !['files', 'computer-files'].includes(item.appId))"
        v-show="controlCenter.activeContentId === tab.tabId"
        :key="tab.tabId"
        class="fit"
      />
      <component
        :is="content(controlCenter.activeTab.appId).component"
        v-if="controlCenter.activeTab?.kind === 'app' && ['files', 'computer-files'].includes(controlCenter.activeTab.appId)"
        :key="controlCenter.activeTab.appId"
        class="fit"
      />
      <component
        :is="content(controlCenter.activeTab.appId).component"
        v-if="controlCenter.activeTab?.kind === 'file'"
        :key="`${controlCenter.activeTab.tabId}:${controlCenter.activeTab.fileTarget.path}`"
        :tab="controlCenter.activeTab"
        class="fit"
      />
      <component
        :is="item.component"
        v-for="item in controlCenterGlobalSections"
        v-show="controlCenter.activeContentId === item.id"
        :key="item.id"
        class="fit"
      />
      <div v-if="!controlCenter.activeContentId" class="fit column no-wrap">
        <div class="col column flex-center text-center q-pa-xl">
          <q-icon name="extension_off" size="52px" color="grey-6" />
          <div class="text-subtitle1 text-weight-medium q-mt-md">Keine App in dieser Session</div>
          <div class="text-body2 app-muted q-mt-xs q-mb-md">Füge eine App hinzu, um sie hier zu verwenden.</div>
          <q-btn
            unelevated
            no-caps
            color="primary"
            icon="add"
            label="App hinzufügen"
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
    <template v-if="activeFooter">
      <q-separator />
      <footer class="control-center__footer" aria-label="App-Informationen und Steuerelemente">
        <component :is="activeFooter" class="full-width" />
      </footer>
    </template>
  </section>
</template>

<script setup>
import { computed, nextTick, ref } from 'vue'
import { Notify } from 'quasar'
import { useApprovalStore } from 'stores/approvals'
import { useControlCenterStore } from 'stores/control-center'
import { useExecutionAgentStore } from 'stores/execution-agents'
import { useInAppNotificationStore } from 'stores/in-app-notifications'
import { useQuestionStore } from 'stores/questions'
import { useMailStore } from 'stores/mail'
import { useChatStore } from 'stores/chat'
import { useProjectStore } from 'stores/projects'
import { useTerminalStore } from 'stores/terminal'
import { useWorkspaceStore } from 'stores/workspace'
import { useConfirm } from 'src/composables/confirm'
import {
  controlCenterContentsById,
  controlCenterGlobalSections,
  controlCenterIntegrations
} from 'src/control-center/apps'
import {
  codingControlCenterAvailable,
  computerFilesControlCenterAvailable,
  mailControlCenterAvailable,
  terminalControlCenterState
} from 'src/control-center/manifest'
import { terminalTabLabel } from 'src/utils/terminal-output'

defineOptions({ name: 'ControlCenterPanel' })

const emit = defineEmits(['close'])
const { confirm } = useConfirm()
const controlCenter = useControlCenterStore()
const approvals = useApprovalStore()
const questions = useQuestionStore()
const notifications = useInAppNotificationStore()
const mail = useMailStore()
const chat = useChatStore()
const projects = useProjectStore()
const executionAgents = useExecutionAgentStore()
const terminal = useTerminalStore()
const workspace = useWorkspaceStore()
const draggedIntegrationId = ref(null)
const dropTarget = ref(null)
const flexibleTabs = computed(() => controlCenter.tabs.filter((tab) => tab.appId !== 'session'))
const activeFlexibleTabId = computed(() => (
  controlCenter.activeGlobalSectionId || controlCenter.activeAppId === 'session'
    ? null
    : controlCenter.activeAppId
))
const activeFooter = computed(() => (
  controlCenter.activeGlobalSectionId
    ? content(controlCenter.activeGlobalSectionId)?.footerComponent
    : content(controlCenter.activeTab?.appId)?.footerComponent
))
const closedIntegrations = computed(() => controlCenterIntegrations.filter(
  (item) => !item.fixed && !item.resource && !controlCenter.tabs.some((tab) => tab.appId === item.id)
))
const activeProject = computed(() => projects.items.find(
  (project) => project.id === (chat.current ? chat.current.projectId : chat.sessionsProjectId)
))
const launcherIntegrations = computed(() => closedIntegrations.value.filter((item) => (
  (item.id !== 'mail' || mailControlCenterAvailable(mail))
  && (item.id !== 'coding' || codingControlCenterAvailable(activeProject.value))
  && (item.id !== 'computer-files' || computerFilesControlCenterAvailable(chat.activeContext, executionAgents))
  && (item.id !== 'terminal' || terminalControlCenterState(chat.current, executionAgents).available)
)))
function content(contentId) {
  return controlCenterContentsById.get(contentId)
}

function tabLabel(tab) {
  if (tab.kind === 'file') return tab.fileTarget.name
    + (tab.fileTarget.revision != null ? ` · Version ${tab.fileTarget.revision}` : '')
  if (['terminal', 'computer-files'].includes(tab.appId)) {
    const agent = executionAgents.byId(chat.activeContext?.preferredExecutionAgentId)
    return agent?.alias ? `${content(tab.appId).label} · ${agent.alias}` : content(tab.appId).label
  }
  return content(tab.appId).label
}

function tabAriaLabel(tab) {
  const label = tabLabel(tab)
  return terminalTabLabel(label, tab.appId === 'terminal' && terminal.pendingProposal)
}

function badge(appId) {
  const count = appId === 'approvals'
    ? approvals.pending.length + questions.pending.length
    : appId === 'notifications' ? notifications.unreadCount : 0
  return count > 99 ? '99+' : count || null
}

async function closePanel() {
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
  if (appId === 'terminal' && terminal.removalBlocked) {
    Notify.create({
      type: 'warning',
      message: 'Das Terminal kann während eines wartenden oder laufenden Befehls nicht entfernt werden.'
    })
    return
  }
  const terminalHint = appId === 'terminal'
    ? ' Die laufende Shell und ihr sichtbarer Verlauf gehen verloren.'
    : ''
  const accepted = await confirm(
    `„${item.label}“ wird aus dieser Session entfernt.${terminalHint} Gespeicherte Daten werden nicht gelöscht. Die App kann später erneut hinzugefügt werden.`,
    'App aus Session entfernen?',
    { okLabel: 'Entfernen', okColor: 'negative' }
  )
  if (!accepted) return
  try {
    await controlCenter.removeIntegration(appId)
    if (appId === 'terminal') terminal.close()
  } catch {
    // The central API interceptor displays the error and the store restores server state.
  }
}

async function closeTab(tab) {
  if (tab.kind === 'file') {
    if (workspace.fileTabDirty(tab.tabId) && !await confirm(
      'Sollen die ungespeicherten Änderungen verworfen werden?',
      'Änderungen verwerfen?',
      { okLabel: 'Verwerfen' }
    )) return
    try {
      await controlCenter.closeFile(tab.tabId)
      workspace.discardFileTab(tab.tabId)
    } catch {
      // The central API interceptor displays the error and keeps the draft available.
      return
    }
  } else {
    await removeIntegration(tab.appId)
  }
  await nextTick()
  const activeId = controlCenter.activeAppId === 'session'
    ? 'control-center-session-button'
    : `control-center-tab-${controlCenter.activeAppId}`
  document.getElementById(activeId)?.focus()
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
