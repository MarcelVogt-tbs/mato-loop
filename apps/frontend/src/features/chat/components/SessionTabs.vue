<template>
  <div v-if="enabled && tabs.items.length" class="session-tabs row no-wrap items-center col-auto" :class="{ 'session-tabs--top': position === 'top' }" aria-label="Offene Sessions">
    <q-tabs :model-value="activeKey" content-class="app-tab-scroll" align="left" :breakpoint="0" no-caps dense outside-arrows mobile-arrows :switch-indicator="position === 'bottom'" indicator-color="primary" class="col" aria-label="Session-Tabs" @update:model-value="select">
      <q-tab v-for="(tab, index) in displayTabs" :key="tab.key" :name="tab.key" class="session-tabs__tab" :class="{ 'session-tabs__preview': tab.preview, 'session-tabs__dragging': draggedId === tab.key, 'session-tabs__drop-before': dropTarget?.id === tab.key && !dropTarget.after, 'session-tabs__drop-after': dropTarget?.id === tab.key && dropTarget.after }" :aria-label="`${tab.label}${tab.preview ? ', Vorschau' : ''}`" draggable="true" @dragstart="startDrag($event, tab.key)" @dragover="updateDropTarget($event, tab.key)" @drop="drop" @dragend="endDrag" @keydown.alt.left.prevent.stop="tabs.move(tab.key, -1)" @keydown.alt.right.prevent.stop="tabs.move(tab.key, 1)" @mouseenter="prepareTitleScroll" @focusin="prepareTitleScroll" @dblclick="tabs.keep(tab.key)">
        <div class="session-tabs__content row no-wrap items-center">
          <SessionStatusIndicator v-if="tab.status" :status="tab.status" class="session-tabs__status" />
          <span class="session-tabs__scroll"><span class="session-tabs__title">{{ tab.title }}</span></span>
          <span v-if="tab.projectName" class="session-tabs__separator"> · </span>
          <span v-if="tab.projectName" class="session-tabs__project">{{ tab.projectName }}</span>
          <q-btn flat round dense size="xs" icon="close" class="session-tabs__close q-ml-xs" :aria-label="`Tab ${tab.label} schließen`" @click.stop="close(tab)" @keydown.stop />
        </div>
        <q-menu touch-position context-menu>
          <q-list dense>
            <q-item clickable v-close-popup :disable="index === 0" @click="tabs.move(tab.key, -1)"><q-item-section>Nach links verschieben</q-item-section></q-item>
            <q-item clickable v-close-popup :disable="index === tabs.items.length - 1" @click="tabs.move(tab.key, 1)"><q-item-section>Nach rechts verschieben</q-item-section></q-item>
          </q-list>
        </q-menu>
      </q-tab>
    </q-tabs>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { ClosePopup, QBtn, QTab, QTabs, QMenu, QList, QItem, QItemSection } from 'quasar'
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
const displayTabs = computed(() => props.tabs.items.map(tab => {
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
  return { ...tab, title, projectName, label: projectName ? `${title} · ${projectName}` : title, status }
}))
function prepareTitleScroll(event) {
  for (const region of event.currentTarget.querySelectorAll('.session-tabs__scroll')) {
    const text = region.querySelector('.session-tabs__title')
    const distance = Math.max(0, text.scrollWidth - region.clientWidth)
    region.style.setProperty('--session-title-scroll-distance', `${-distance}px`)
    region.style.setProperty('--session-title-scroll-duration', `${distance / 35}s`)
  }
}
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
  border-top: 1px solid var(--app-surface-border);
  background: var(--app-shell-bg);
  color: var(--app-text);
}

.session-tabs--top {
  order: -1;
  border-top: 0;
  border-bottom: 1px solid var(--app-surface-border);
}

.session-tabs .session-tabs__tab {
  width: 220px;
  min-width: 220px;
  max-width: 220px;
  flex: 0 0 220px;
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
  background: var(--app-active);
  color: var(--app-text);
}

.session-tabs__tab.q-tab--active .session-tabs__title { font-weight: 600; }
.session-tabs__content { min-width: 0; width: 100%; text-align: left; font-size: 13px; }
.session-tabs__status { margin-right: 8px; }
.session-tabs :deep(.q-tab__content) { min-width: 0; width: 100%; padding: 0; }
.session-tabs :deep(.q-tab__indicator) { height: 2px; }
.session-tabs :deep(.q-tabs__arrow) { width: 24px; }
.session-tabs__scroll { min-width: 0; flex: 1 1 0; overflow: hidden; white-space: nowrap; }
.session-tabs__title { display: block; overflow: hidden; text-overflow: ellipsis; }
.session-tabs__project { min-width: 0; flex: 0 0 auto; max-width: 72px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.session-tabs__separator { flex: 0 0 auto; white-space: pre; }
.session-tabs__preview .session-tabs__title { font-style: italic; }

.session-tabs__close { min-width: 24px; min-height: 24px; flex: 0 0 24px; color: var(--app-muted); }
.session-tabs__close:hover { color: var(--app-text); }

@media (hover: hover) and (pointer: fine) and (prefers-reduced-motion: no-preference) {
  .session-tabs__tab:is(:hover, :focus-visible, :has(:focus-visible)) .session-tabs__title {
    width: max-content;
    overflow: visible;
    animation: session-title-scroll var(--session-title-scroll-duration, 0s) linear 700ms forwards;
  }
}
</style>
