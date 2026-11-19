<template>
  <div v-if="enabled && tabs.items.length" class="session-tabs row no-wrap items-center col-auto" :class="{ 'session-tabs--top': position === 'top' }" aria-label="Offene Sessions">
    <q-resize-observer :debounce="0" @resize="barWidth = $event.width || Infinity" />
    <q-tabs :model-value="activeKey" align="left" :breakpoint="0" no-caps dense class="col" aria-label="Session-Tabs" @update:model-value="select">
      <q-tab v-for="tab in visibleTabs" :key="tab.key" :name="tab.key" class="session-tabs__tab" :class="{ 'session-tabs__preview': tab.preview, 'session-tabs__dragging': draggedId === tab.key, 'session-tabs__drop-before': dropTarget?.id === tab.key && !dropTarget.after, 'session-tabs__drop-after': dropTarget?.id === tab.key && dropTarget.after }" :aria-label="`${tab.label}${tab.preview ? ', Vorschau' : ''}`" :title="tab.label" draggable="true" @dragstart="startDrag($event, tab.key)" @dragover="updateDropTarget($event, tab.key)" @drop="drop" @dragend="endDrag" @keydown.alt.left.prevent.stop="tabs.move(tab.key, -1)" @keydown.alt.right.prevent.stop="tabs.move(tab.key, 1)" @dblclick="tabs.keep(tab.key)">
        <div class="session-tabs__content row no-wrap items-center">
          <span v-if="tab.status" class="session-tabs__status">
            <SessionStatusIndicator :status="tab.status" />
          </span>
          <span class="session-tabs__title">{{ tab.title }}</span>
          <span v-if="tab.projectName" class="session-tabs__separator"> · </span>
          <span v-if="tab.projectName" class="session-tabs__project">{{ tab.projectName }}</span>
          <q-btn flat round dense size="xs" icon="close" class="session-tabs__close q-ml-xs" :aria-label="`Tab ${tab.label} schließen`" @click.stop="close(tab)" @keydown.stop />
        </div>
        <q-menu touch-position context-menu>
          <q-list dense>
            <q-item clickable v-close-popup :disable="tab.index === 0" @click="tabs.move(tab.key, -1)"><q-item-section>Nach links verschieben</q-item-section></q-item>
            <q-item clickable v-close-popup :disable="tab.index === tabs.items.length - 1" @click="tabs.move(tab.key, 1)"><q-item-section>Nach rechts verschieben</q-item-section></q-item>
          </q-list>
        </q-menu>
      </q-tab>
    </q-tabs>
    <q-btn v-if="hiddenTabs.length" flat dense no-caps :label="`+${hiddenTabs.length}`" :aria-label="`${hiddenTabs.length} weitere Session-Tabs`" :style="{ flex: `0 0 ${overflowWidth}px` }">
      <q-menu :anchor="position === 'top' ? 'bottom right' : 'top right'" :self="position === 'top' ? 'top right' : 'bottom right'">
        <q-list dense class="session-tabs__overflow-list">
          <div v-for="tab in hiddenTabs" :key="tab.key" class="row no-wrap items-center">
            <q-item clickable v-close-popup class="col" @click="select(tab.key)">
              <q-item-section v-if="tab.status" side><SessionStatusIndicator :status="tab.status" /></q-item-section>
              <q-item-section><span>{{ tab.label }}</span></q-item-section>
            </q-item>
            <q-btn flat round dense size="xs" icon="close" class="session-tabs__close q-mr-sm" :aria-label="`Tab ${tab.label} schließen`" @click="close(tab)" />
          </div>
        </q-list>
      </q-menu>
    </q-btn>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { ClosePopup, QBtn, QTab, QTabs, QMenu, QList, QItem, QItemSection, QResizeObserver } from 'quasar'
import SessionStatusIndicator from './SessionStatusIndicator.vue'
import { useTabReorder } from 'src/shared/ui/useTabReorder'
import { useRouter } from 'vue-router'
import { useApprovalStore } from 'stores/approvals'
import { useQuestionStore } from 'stores/questions'
import { useProjectStore } from 'stores/projects'
import { useChatStore } from '../store'
import { sessionSidebarStatus } from '../session-groups'
import { sessionTabTarget } from '../session-tabs-store'

const props = defineProps({ enabled: Boolean, tabs: { type: Object, required: true }, activeKey: { type: String, required: true }, position: { type: String, default: 'bottom' } })
const vClosePopup = ClosePopup
const { draggedId, dropTarget, startDrag, updateDropTarget, drop, endDrag } = useTabReorder(
  (source, target, after) => props.tabs.reorder(source, target, after)
)
const router = useRouter()
const chat = useChatStore()
const approvals = useApprovalStore()
const questions = useQuestionStore()
const projects = useProjectStore()
const displayTabs = computed(() => props.tabs.items.map((tab, index) => {
  const current = chat.current?.id === tab.sessionId ? chat.current : null
  const summary = chat.findCachedSession(tab.sessionId) || props.tabs.summaries?.[tab.sessionId]
  const session = current || summary
  const title = session?.title || tab.title
  const projectName = projects.items.find(project => project.id === (session?.projectId ?? tab.projectId))?.name || ''
  const status = sessionSidebarStatus(
    summary || { activeRunStatus: current?.activeRun?.status },
    approvals.forSession(tab.sessionId).length, questions.forSession(tab.sessionId).length,
    chat.sessionHasPendingSend(tab.sessionId, tab.projectId)
  ) || ((summary || current)?.hasUnreadMessages ? { kind: 'unread', label: 'Ungelesene Antwort' } : null)
  return { ...tab, index, title, projectName, label: projectName ? `${title} · ${projectName}` : title, status }
}))
const barWidth = ref(Infinity)
const minTabWidth = 150
const overflowWidth = 52
const visibleTabs = computed(() => {
  const all = displayTabs.value
  if (all.length * minTabWidth <= barWidth.value) return all
  const count = Math.max(1, Math.floor((barWidth.value - overflowWidth) / minTabWidth))
  const visible = all.slice(0, count)
  const active = all.find(tab => tab.key === props.activeKey)
  if (active && active.index >= count) visible[count - 1] = active
  return visible
})
const hiddenTabs = computed(() => {
  const visible = new Set(visibleTabs.value.map(tab => tab.key))
  return displayTabs.value.filter(tab => !visible.has(tab.key))
})
async function select(key) {
  const tab = props.tabs.items.find(tab => tab.key === key)
  if (tab) await router.push(sessionTabTarget(tab))
}
async function close(tab) {
  if (tab.key === props.activeKey) {
    const index = props.tabs.items.findIndex(item => item.key === tab.key)
    const next = props.tabs.items[index + 1] || props.tabs.items[index - 1]
    const failure = await router.push(next ? sessionTabTarget(next) : { name: 'chat' })
    if (failure) return
  }
  props.tabs.close(tab.key)
}
</script>

<style scoped>
.session-tabs {
  min-width: 0;
  box-shadow: inset 0 1px var(--app-surface-border);
  background: var(--app-shell-bg);
  color: var(--app-text);
}

.session-tabs--top {
  order: -1;
  box-shadow: inset 0 -1px var(--app-surface-border);
}

.session-tabs .session-tabs__tab {
  min-width: 0;
  flex: 0 1 260px;
  min-height: 32px;
  padding: 0 6px 0 10px;
  border-right: 1px solid var(--app-surface-border);
  color: var(--app-text);
  opacity: 1;
}

.session-tabs__tab:hover { background: var(--app-hover); }
.session-tabs__dragging { opacity: .45 !important; }
.session-tabs__drop-before { box-shadow: inset 2px 0 var(--q-primary); }
.session-tabs__drop-after { box-shadow: inset -2px 0 var(--q-primary); }

.session-tabs__tab.q-tab--active {
  background: var(--app-page-bg);
  border: 1px solid var(--app-surface-border);
  border-top: 0;
  border-radius: 0 0 9px 9px;
  padding-left: 9px;
}

.session-tabs--top .session-tabs__tab.q-tab--active {
  border-top: 1px solid var(--app-surface-border);
  border-bottom: 0;
  border-radius: 9px 9px 0 0;
}

.session-tabs__content { min-width: 0; width: 100%; text-align: left; font-size: 13px; }
.session-tabs__status { flex: 0 0 16px; margin-right: 6px; }
.session-tabs :deep(.q-tabs__content) { overflow: hidden; }
.session-tabs :deep(.q-tab__content) { min-width: 0; width: 100%; padding: 0; }
.session-tabs :deep(.q-tab__indicator) { display: none; }
.session-tabs :deep(.q-tabs__arrow) { display: none; }
.session-tabs__title, .session-tabs__project { overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.session-tabs__title { min-width: 0; flex: 0 1 auto; }
.session-tabs__project { flex: 0 0 auto; max-width: min(45%, 100px); color: var(--app-muted); }
.session-tabs__separator { flex: 0 0 auto; white-space: pre; }
.session-tabs__preview .session-tabs__title { font-style: italic; }

.session-tabs__close { min-width: 24px; min-height: 24px; flex: 0 0 24px; color: var(--app-muted); }
.session-tabs__content > .session-tabs__close { margin-left: auto; }
.session-tabs__close:hover { color: var(--app-text); }
.session-tabs__overflow-list { width: min(360px, calc(100vw - 24px)); overflow-wrap: anywhere; }
</style>
