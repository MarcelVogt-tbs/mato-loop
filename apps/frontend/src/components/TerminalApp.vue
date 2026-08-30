<template>
  <section class="terminal-app column full-height no-wrap" aria-labelledby="terminal-app-title">
    <ControlCenterContentHeader
      icon="terminal"
      title="Terminal"
      title-id="terminal-app-title"
      integration-id="terminal"
      :can-move-left="integrationIndex > 0"
      :can-move-right="integrationIndex >= 0 && integrationIndex < controlCenter.tabs.length - 1"
      @close="emit('close')"
      @remove="emit('remove', 'terminal')"
      @move="emit('move', 'terminal', $event)"
    />

    <div v-if="!contextState.available" class="col column flex-center text-center q-pa-xl">
      <q-icon :name="unavailableIcon" size="52px" color="grey-6" />
      <div class="text-subtitle1 text-weight-medium q-mt-md">{{ unavailableTitle }}</div>
      <div class="text-body2 app-muted q-mt-xs">{{ unavailableMessage }}</div>
    </div>

    <template v-else>
      <div class="row items-center q-gutter-sm q-pa-sm">
        <q-chip dense square icon="computer" class="q-ma-none">
          {{ contextState.agent.alias || contextState.agent.computerName }}
        </q-chip>
        <q-chip dense square icon="folder_open" class="q-ma-none ellipsis">
          {{ activeDirectory }}
          <q-tooltip>{{ activeDirectory }}</q-tooltip>
        </q-chip>
        <q-space />
        <q-spinner-dots
          v-if="terminal.status === 'connecting' || ['attach', 'detach'].includes(terminal.submitting)"
          color="primary"
          size="24px"
        />
        <q-toggle
          :model-value="terminal.attached"
          label="Chat-Agent verbinden"
          color="primary"
          :disable="terminal.status !== 'open' || Boolean(terminal.submitting) || (terminal.attached && terminal.interactionLocked)"
          @update:model-value="toggleAttachment"
        />
      </div>
      <q-separator />

      <div v-if="disconnected" class="col column flex-center text-center q-pa-xl">
        <q-icon name="link_off" size="52px" color="grey-6" />
        <div class="text-subtitle1 text-weight-medium q-mt-md">Terminalverbindung getrennt</div>
        <div class="text-body2 app-muted q-mt-xs q-mb-md">
          Die Shell ist nicht mehr verbunden. Stelle eine neue Verbindung zum ausgewählten Computer her.
        </div>
        <q-btn unelevated no-caps color="primary" icon="refresh" label="Neu verbinden" @click="reconnect" />
      </div>

      <template v-else>
      <div v-if="terminal.attached" class="row items-center q-gutter-sm q-px-sm q-py-xs">
        <q-icon name="link" color="positive" />
        <span class="text-body2 text-weight-medium">Mit Chat-Agent verbunden</span>
        <q-space />
        <q-spinner-dots v-if="terminal.submitting === 'mode'" color="primary" size="20px" />
        <q-btn-toggle
          :model-value="terminal.mode"
          :options="modeOptions"
          dense
          no-caps
          unelevated
          toggle-color="primary"
          :disable="terminal.interactionLocked"
          @update:model-value="changeMode"
        />
      </div>

      <div
        v-if="terminal.attached && terminal.mode === 'auto'"
        class="row items-center q-gutter-xs q-px-sm q-py-xs text-caption app-muted"
      >
        <q-icon name="lock" />
        <span>Schreibgeschützt, Strg+C bricht ab</span>
      </div>

      <q-banner v-if="policyWaiting" dense class="bg-warning text-dark">
        <template #avatar><q-icon name="pending_actions" /></template>
        Der Chat-Agent wartet auf eine Freigabe.
        <template #action>
          <q-btn flat dense no-caps color="dark" label="Freigaben öffnen" @click="openApprovals" />
        </template>
      </q-banner>

      <div class="terminal-app__stage col relative-position">
        <div v-show="!terminal.attached" ref="personalHost" class="terminal-app__host fit" />
        <div v-show="terminal.attached" ref="agentHost" class="terminal-app__host fit" />

        <div
          v-if="terminal.proposal"
          class="terminal-app__proposal app-surface q-pa-sm"
          role="dialog"
          aria-modal="false"
          aria-labelledby="terminal-proposal-title"
          @keydown.esc="proposalEscape"
        >
          <div class="row items-center q-mb-xs">
            <q-icon name="security" color="warning" class="q-mr-xs" />
            <span id="terminal-proposal-title" class="text-body2 text-weight-medium">Befehl bestätigen</span>
          </div>
          <q-input
            v-model="proposalCommand"
            outlined
            dense
            autogrow
            autofocus
            type="textarea"
            input-class="terminal-app__command-input"
            maxlength="262144"
            aria-label="Vorgeschlagenen Befehl bearbeiten"
            @keydown="proposalKeydown"
          />
          <div class="row justify-end q-gutter-sm q-mt-sm">
            <q-btn
              flat dense no-caps color="negative" label="Ablehnen"
              :loading="terminal.submitting === 'deny'"
              :disable="!terminal.attached || Boolean(terminal.submitting)"
              @click="resolveProposal('deny')"
            />
            <q-btn
              unelevated
              dense
              no-caps
              color="primary"
              label="Ausführen"
              :loading="terminal.submitting === 'execute'"
              :disable="!terminal.attached || Boolean(terminal.submitting) || !proposalCommand.trim()"
              @click="resolveProposal('execute')"
            />
          </div>
        </div>
      </div>

      <div
        v-if="terminal.commandRunning || terminal.commandExit || (terminal.submitting === 'execute' && !terminal.proposal)"
        class="q-px-sm q-py-xs text-caption app-muted"
      >
        <template v-if="terminal.commandRunning">
          {{ terminal.mode === 'confirm' ? 'Bestätigter Befehl läuft. Eingaben werden direkt weitergeleitet.' : 'Chat-Agent-Befehl läuft. Strg+C bricht ab.' }}
        </template>
        <template v-else-if="terminal.submitting === 'execute'">Befehl wird gestartet.</template>
        <template v-else>{{ exitStatus }}</template>
      </div>
      </template>
    </template>
  </section>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'
import ControlCenterContentHeader from 'components/ControlCenterContentHeader.vue'
import { terminalControlCenterState } from 'src/control-center/manifest'
import {
  handleAutoTerminalData,
  terminalExitStatus,
  terminalProposalCommand
} from 'src/utils/terminal-output'
import { useChatStore } from 'stores/chat'
import { useControlCenterStore } from 'stores/control-center'
import { useExecutionAgentStore } from 'stores/execution-agents'
import { useTerminalStore } from 'stores/terminal'

defineOptions({ name: 'TerminalApp' })

const emit = defineEmits(['close', 'remove', 'move'])
const chat = useChatStore()
const controlCenter = useControlCenterStore()
const executionAgents = useExecutionAgentStore()
const terminal = useTerminalStore()
const personalHost = ref(null)
const agentHost = ref(null)
const proposalCommand = ref(terminalProposalCommand(terminal.proposal))
const integrationIndex = computed(() => controlCenter.tabs.findIndex((tab) => tab.appId === 'terminal'))
const contextState = computed(() => terminalControlCenterState(chat.current, executionAgents))
const activeDirectory = computed(() => terminal.attached
  ? terminal.currentDirectory || chat.current?.currentDirectory || contextState.value.agent?.homeDirectory || '/'
  : contextState.value.agent?.homeDirectory || terminal.currentDirectory || '/')
const policyWaiting = computed(() => terminal.attached && chat.activity.phase === 'waiting_approval')
const disconnected = computed(() => Boolean(
  terminal.status === 'closed'
  && terminal.sessionId === chat.current?.id
  && terminal.agentId === chat.current?.preferredExecutionAgentId
))
const modeOptions = [
  { label: 'Auto', value: 'auto' },
  { label: 'Bestätigen', value: 'confirm' }
]
const unavailableTitle = computed(() => ({
  missing: chat.current ? 'Kein Computer ausgewählt' : 'Keine aktive Session',
  offline: 'Computer offline',
  unsupported: 'Terminal nicht unterstützt'
})[contextState.value.reason])
const unavailableMessage = computed(() => ({
  missing: chat.current
    ? 'Wähle für diese Session einen verbundenen Computer aus.'
    : 'Öffne eine gespeicherte Session mit ausgewähltem Computer.',
  offline: 'Die Terminal-Sitzung wurde geschlossen. Verbinde den ausgewählten Computer erneut.',
  unsupported: 'Der ausgewählte Computer bietet die benötigten Terminalfunktionen nicht an.'
})[contextState.value.reason])
const unavailableIcon = computed(() => contextState.value.reason === 'offline' ? 'cloud_off' : 'terminal_off')
const exitStatus = computed(() => terminalExitStatus(terminal.commandExit))

let personalXterm
let agentXterm
let personalFit
let agentFit
let resizeObserver

function createXterm(host, onData) {
  const instance = new Terminal({
    convertEol: false,
    cursorBlink: false,
    fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
    fontSize: 14,
    scrollback: 5000,
    theme: { background: '#111318', foreground: '#f1f3f5', cursor: '#f1f3f5' }
  })
  const fit = new FitAddon()
  instance.loadAddon(fit)
  instance.open(host)
  instance.onData(onData)
  return [instance, fit]
}

function fitVisibleTerminal(force = false) {
  const view = terminal.attached ? 'agent' : 'personal'
  const instance = terminal.attached ? agentXterm : personalXterm
  const fit = terminal.attached ? agentFit : personalFit
  if (!instance || !fit) return
  try {
    fit.fit()
    terminal.resize(view, instance.cols, instance.rows, force === true)
  } catch {
    // The next observed layout change retries after the host becomes measurable.
  }
}

function initializeXterms() {
  if (personalXterm || !personalHost.value || !agentHost.value) return
  ;[personalXterm, personalFit] = createXterm(personalHost.value, (data) => terminal.input(data))
  ;[agentXterm, agentFit] = createXterm(agentHost.value, (data) => {
    if (terminal.mode === 'auto') handleAutoTerminalData(data, chat.stop)
    else terminal.input(data)
  })
  personalXterm.write(terminal.personalBuffer)
  agentXterm.write(terminal.agentBuffer)
  resizeObserver = new ResizeObserver(() => fitVisibleTerminal())
  resizeObserver.observe(personalHost.value)
  resizeObserver.observe(agentHost.value)
  updateCursorState()
  fitVisibleTerminal()
}

function updateCursorState() {
  if (personalXterm) personalXterm.options.cursorBlink = !terminal.attached
  if (agentXterm) {
    agentXterm.options.cursorBlink = terminal.attached
      && terminal.mode === 'confirm'
      && terminal.commandRunning
  }
}

function disposeXterms() {
  resizeObserver?.disconnect()
  resizeObserver = null
  personalXterm?.dispose()
  agentXterm?.dispose()
  personalXterm = null
  agentXterm = null
  personalFit = null
  agentFit = null
}

function toggleAttachment(attached) {
  if (attached) {
    terminal.attach(chat.current?.messages, chat.processOutputs)
  } else {
    terminal.detach()
  }
  void nextTick(fitVisibleTerminal)
}

function changeMode(mode) {
  terminal.setMode(mode)
}

function resolveProposal(decision) {
  terminal.resolveProposal(decision, proposalCommand.value)
  void nextTick(fitVisibleTerminal)
}

function proposalKeydown(event) {
  if (event.key !== 'Enter' || event.shiftKey) return
  event.preventDefault()
  if (proposalCommand.value.trim()) resolveProposal('execute')
}

function proposalEscape(event) {
  event.preventDefault()
  event.stopPropagation()
  if (!terminal.submitting && !terminal.commandRunning) resolveProposal('deny')
}

function reconnect() {
  terminal.connect({ sessionId: chat.current.id, agentId: chat.current.preferredExecutionAgentId })
}

function openApprovals() {
  void controlCenter.openApp('approvals').catch(() => {})
}

watch(() => terminal.proposal, (proposal) => {
  proposalCommand.value = terminalProposalCommand(proposal)
}, { immediate: true })
watch(() => terminal.personalBuffer, (value, previous = '') => {
  if (!personalXterm) return
  if (value.startsWith(previous)) personalXterm.write(value.slice(previous.length))
  else {
    personalXterm.reset()
    personalXterm.write(value)
  }
})
watch(() => terminal.agentBuffer, (value, previous = '') => {
  if (!agentXterm) return
  if (value.startsWith(previous)) agentXterm.write(value.slice(previous.length))
  else {
    agentXterm.reset()
    agentXterm.write(value)
  }
})
watch(
  () => [terminal.attached, terminal.mode, terminal.commandRunning],
  () => {
    updateCursorState()
    if (terminal.attached) terminal.syncAgentOutput(chat.current?.messages, chat.processOutputs)
    void nextTick(fitVisibleTerminal)
  }
)
watch(() => terminal.resizeRevision, () => void nextTick(() => fitVisibleTerminal(true)))
watch(disconnected, (value) => {
  if (value) disposeXterms()
  else void nextTick(initializeXterms)
})
watch(
  () => [chat.current?.id, chat.current?.preferredExecutionAgentId, contextState.value.available],
  () => {
    if (contextState.value.available && chat.current?.id) {
      void nextTick(() => {
        initializeXterms()
        if (!terminal.sessionId) terminal.connect({
          sessionId: chat.current.id,
          agentId: chat.current.preferredExecutionAgentId
        })
      })
    } else disposeXterms()
  }
)

onMounted(() => {
  if (!disconnected.value) initializeXterms()
  if (contextState.value.available && chat.current?.id && !terminal.sessionId) {
    terminal.connect({ sessionId: chat.current.id, agentId: chat.current.preferredExecutionAgentId })
  }
  window.visualViewport?.addEventListener('resize', fitVisibleTerminal)
  void nextTick(fitVisibleTerminal)
})

onBeforeUnmount(() => {
  window.visualViewport?.removeEventListener('resize', fitVisibleTerminal)
  disposeXterms()
})
</script>

<style scoped>
.terminal-app,
.terminal-app__stage,
.terminal-app__host {
  min-height: 0;
  min-width: 0;
}

.terminal-app__stage {
  overflow: hidden;
  background: #111318;
}

.terminal-app__host {
  padding: 8px;
}

.terminal-app__proposal {
  position: absolute;
  z-index: 2;
  right: 8px;
  bottom: 8px;
  left: 8px;
  max-height: calc(100% - 16px);
  overflow: auto;
  border-radius: 4px;
  box-shadow: 0 4px 24px rgb(0 0 0 / 45%);
}

:deep(.terminal-app__command-input) {
  max-height: 30vh;
  overflow: auto;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
}

:deep(.xterm) {
  height: 100%;
}
</style>
