<template>
  <section class="terminal-app column full-height no-wrap" aria-label="Terminal">
    <div v-if="!contextState.available" class="col column flex-center text-center q-pa-xl">
      <q-icon :name="unavailableIcon" size="52px" color="grey-6" />
      <div class="text-subtitle1 text-weight-medium q-mt-md">{{ unavailableTitle }}</div>
      <div class="text-body2 app-muted q-mt-xs">{{ unavailableMessage }}</div>
    </div>

    <template v-else>
      <div v-if="disconnected" class="col column flex-center text-center q-pa-xl">
        <q-icon name="link_off" size="52px" color="grey-6" />
        <div class="text-subtitle1 text-weight-medium q-mt-md">Terminalverbindung getrennt</div>
        <div class="text-body2 app-muted q-mt-xs q-mb-md">
          Die Shell ist nicht mehr verbunden. Stelle eine neue Verbindung zum ausgewählten Computer her.
        </div>
        <q-btn unelevated no-caps color="primary" icon="refresh" label="Neu verbinden" @click="reconnect" />
      </div>

      <template v-else>
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
          <q-btn flat dense no-caps color="dark" label="Benachrichtigungen öffnen" @click="openNotifications" />
        </template>
      </q-banner>

      <div class="terminal-app__stage col relative-position">
        <div ref="terminalHost" class="terminal-app__host fit" />

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

      <span class="app-sr-only" role="status" aria-live="polite" aria-atomic="true">
        {{ accessibilityStatus }}
      </span>

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

const chat = useChatStore()
const controlCenter = useControlCenterStore()
const executionAgents = useExecutionAgentStore()
const terminal = useTerminalStore()
const terminalHost = ref(null)
const proposalCommand = ref(terminalProposalCommand(terminal.proposal))
const contextState = computed(() => terminalControlCenterState(chat.current, executionAgents))
const policyWaiting = computed(() => terminal.attached && chat.activity.phase === 'waiting_approval')
const disconnected = computed(() => Boolean(
  terminal.status === 'closed'
  && terminal.sessionId === chat.current?.id
  && terminal.agentId === chat.current?.preferredExecutionAgentId
))
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
const activeView = computed(() => terminal.attached ? 'agent' : 'personal')
const activeBuffer = computed(() => terminal.attached ? terminal.agentBuffer : terminal.personalBuffer)
const accessibilityStatus = computed(() => {
  if (terminal.status === 'connecting') return 'Terminal wird verbunden.'
  if (disconnected.value) return 'Terminalverbindung getrennt.'
  if (terminal.submitting === 'attach') return 'Chat-Agent wird verbunden.'
  if (terminal.submitting === 'detach') return 'Persönliches Terminal wird geöffnet.'
  if (terminal.submitting === 'mode') return 'Agent-Modus wird gewechselt.'
  if (!terminal.attached) return 'Persönliches Terminal aktiv.'
  if (terminal.proposal) return 'Agent-Befehl wartet auf Bestätigung.'
  if (terminal.commandRunning) return 'Agent-Befehl läuft.'
  return terminal.mode === 'confirm' ? 'Agent-Modus Bestätigen aktiv.' : 'Agent-Modus Auto aktiv.'
})

let xterm
let fitAddon
let resizeObserver
let switchingView = false

function createXterm(host) {
  Terminal.strings.promptLabel = 'Terminaleingabe'
  Terminal.strings.tooMuchOutput = 'Zu viel Ausgabe für die vollständige Sprachausgabe. Navigiere zeilenweise, um alles zu lesen.'
  const instance = new Terminal({
    convertEol: terminal.attached,
    cursorBlink: false,
    fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
    fontSize: 14,
    minimumContrastRatio: 4.5,
    screenReaderMode: true,
    scrollback: 5000,
    theme: { background: '#111318', foreground: '#f1f3f5', cursor: '#f1f3f5' }
  })
  const fit = new FitAddon()
  instance.loadAddon(fit)
  instance.open(host)
  instance.onData((data) => {
    if (terminal.attached && terminal.mode === 'auto') handleAutoTerminalData(data, chat.stop)
    else terminal.input(data)
  })
  return [instance, fit]
}

function fitVisibleTerminal(force = false) {
  if (!xterm || !fitAddon) return
  try {
    fitAddon.fit()
    terminal.resize(activeView.value, xterm.cols, xterm.rows, force === true)
  } catch {
    // The next observed layout change retries after the host becomes measurable.
  }
}

function initializeXterms() {
  if (xterm || !terminalHost.value) return
  ;[xterm, fitAddon] = createXterm(terminalHost.value)
  updateCursorState()
  fitVisibleTerminal()
  xterm.write(activeBuffer.value)
  resizeObserver = new ResizeObserver(() => fitVisibleTerminal())
  resizeObserver.observe(terminalHost.value)
}

function updateCursorState() {
  if (!xterm) return
  xterm.options.cursorBlink = !terminal.attached
    || (terminal.mode === 'confirm' && terminal.commandRunning)
}

function redrawTerminal() {
  if (!xterm) return
  xterm.reset()
  xterm.options.convertEol = terminal.attached
  xterm.write(activeBuffer.value)
  updateCursorState()
}

function disposeXterms() {
  resizeObserver?.disconnect()
  resizeObserver = null
  xterm?.dispose()
  xterm = null
  fitAddon = null
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

function openNotifications() {
  void controlCenter.openApp('notifications').catch(() => {})
}

watch(() => terminal.proposal, (proposal, previous) => {
  proposalCommand.value = terminalProposalCommand(proposal)
  if (!proposal && previous) void nextTick(() => xterm?.focus())
}, { immediate: true })
watch(() => terminal.personalBuffer, (value, previous = '') => {
  if (!xterm || switchingView || activeView.value !== 'personal') return
  if (value.startsWith(previous)) xterm.write(value.slice(previous.length))
  else redrawTerminal()
}, { flush: 'post' })
watch(() => terminal.agentBuffer, (value, previous = '') => {
  if (!xterm || switchingView || activeView.value !== 'agent') return
  if (value.startsWith(previous)) xterm.write(value.slice(previous.length))
  else redrawTerminal()
}, { flush: 'post' })
watch(() => terminal.attached, (attached) => {
  switchingView = true
  if (attached) terminal.syncAgentOutput(chat.current?.messages, chat.processOutputs)
  void nextTick(() => {
    fitVisibleTerminal(true)
    redrawTerminal()
    switchingView = false
    xterm?.focus()
  })
})
watch(
  () => [terminal.mode, terminal.commandRunning],
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
