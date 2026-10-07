<template>
  <section class="terminal-app column full-height no-wrap" :aria-label="terminal.mode === 'agent' ? 'Agent-Ausgabe' : 'Terminal'">
    <AppStateMessage
      v-if="!contextState.available"
      class="col q-pa-xl"
      icon="terminal"
      title="Terminal nicht verfügbar"
      :description="unavailableMessage"
    />
    <AppStateMessage
      v-else-if="disconnected"
      class="col q-pa-xl"
      role="status"
      icon="link_off"
      :title="disconnectedTitle"
      :description="terminal.status === 'replaced'
          ? 'Diese Ansicht steuert das Terminal nicht mehr. Verwende die andere Ansicht oder lade diese Seite bewusst neu.'
          : 'Der verfügbare Terminalzustand wird nach der Verbindung geladen.'"
    >
      <template v-if="terminal.status !== 'replaced'" #actions>
        <q-btn unelevated no-caps color="primary" icon="refresh" label="Jetzt verbinden" @click="reconnect()" />
      </template>
    </AppStateMessage>
    <template v-else>
      <q-banner v-if="!terminal.ready" dense role="status">
        Terminalzustand wird geladen. Eingaben sind noch gesperrt.
      </q-banner>
      <q-banner v-else-if="terminal.suspended" dense class="bg-warning text-dark" role="status">
        Die Terminalverbindung ist vorübergehend ausgesetzt. Eingaben sind gesperrt, bis der Server sie wieder freigibt.
      </q-banner>
      <div class="terminal-app__stage col relative-position">
        <div ref="terminalHost" class="terminal-app__host fit" />
      </div>
      <div v-if="terminal.runtimeState === 'closed'" class="q-px-sm q-py-xs text-caption app-muted" role="status" aria-live="polite" aria-atomic="true">
        Die Shell wurde beendet.
      </div>
    </template>
  </section>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import AppStateMessage from 'src/shared/ui/AppStateMessage.vue'
import '@xterm/xterm/css/xterm.css'
import { terminalControlCenterState } from 'src/app/control-center/manifest'
import { useChatStore } from 'stores/chat'
import { useControlCenterStore } from 'stores/control-center'
import { useExecutionAgentStore } from 'stores/execution-agents'
import { useTerminalStore } from 'stores/terminal'

defineOptions({ name: 'TerminalApp' })

const chat = useChatStore()
const controlCenter = useControlCenterStore()
const executionAgents = useExecutionAgentStore()
const terminal = useTerminalStore()
const agentOutput = computed(() => terminal.mode === 'agent')
const visibleBuffer = computed(() => agentOutput.value ? terminal.agentBuffer : terminal.buffer)
const terminalHost = ref(null)
const contextState = computed(() => terminalControlCenterState(chat.current, executionAgents))
const connectionAvailable = computed(() => contextState.value.available
  && controlCenter.ready
  && controlCenter.sessionId === chat.current?.id
  && controlCenter.tabs.some((tab) => tab.appId === 'terminal')
  && controlCenter.confirmedState.tabs.some((tab) => tab.appId === 'terminal'))
const disconnected = computed(() => Boolean(
  ['closed', 'replaced', 'resyncRequired'].includes(terminal.status)
  && terminal.sessionId === chat.current?.id
  && terminal.agentId === chat.current?.preferredExecutionAgentId
))
const disconnectedTitle = computed(() => terminal.status === 'replaced'
  ? 'In einer anderen Ansicht geöffnet' : 'Terminalverbindung wird wiederhergestellt')
const unavailableMessage = computed(() => {
  if (!chat.current) return 'Es ist keine Session geöffnet.'
  if (!chat.current.preferredExecutionAgentId) return 'Für diese Session ist kein Agent ausgewählt.'
  return ({
    missing: 'Der ausgewählte Agent ist nicht mehr verfügbar.',
    offline: 'Der ausgewählte Agent ist nicht erreichbar.',
    unsupported: 'Der ausgewählte Agent unterstützt das Terminal nicht.'
  })[contextState.value.reason]
})
let xterm
let fitAddon
let resizeObserver
let viewerSocket
let pendingReplays = 0
let reconnectTimer
let reconnectAttempts = 0

function initializeTerminal() {
  if (xterm || !terminalHost.value) return
  Terminal.strings.promptLabel = 'Terminaleingabe'
  Terminal.strings.tooMuchOutput = 'Zu viel Ausgabe für die vollständige Sprachausgabe. Navigiere zeilenweise, um alles zu lesen.'
  xterm = new Terminal({
    convertEol: false,
    cursorBlink: terminal.inputEnabled,
    disableStdin: !terminal.inputEnabled,
    fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
    fontSize: 14,
    minimumContrastRatio: 4.5,
    screenReaderMode: true,
    scrollback: 50000,
    theme: { background: '#111318', foreground: '#f1f3f5', cursor: '#f1f3f5' }
  })
  fitAddon = new FitAddon()
  xterm.loadAddon(fitAddon)
  xterm.open(terminalHost.value)
  xterm.onData((data) => {
    // Historical terminal queries must not send fresh input into the live shell.
    if (!pendingReplays) terminal.input(data)
  })
  fitVisibleTerminal()
  replayBuffer(visibleBuffer.value)
  resizeObserver = new ResizeObserver(() => fitVisibleTerminal())
  resizeObserver.observe(terminalHost.value)
}

function replayBuffer(value) {
  const instance = xterm
  pendingReplays += 1
  instance.write(value, () => {
    if (xterm === instance) pendingReplays -= 1
  })
}

function fitVisibleTerminal() {
  if (!xterm || !fitAddon) return
  try {
    fitAddon.fit()
    terminal.resize(xterm.cols, xterm.rows)
  } catch {
    // The next observed layout change retries after the host becomes measurable.
  }
}

function disposeTerminal() {
  resizeObserver?.disconnect()
  resizeObserver = null
  xterm?.dispose()
  xterm = null
  fitAddon = null
  pendingReplays = 0
}

function reconnect(automatic = false) {
  if (!connectionAvailable.value || !chat.current?.id || terminal.status === 'replaced') return
  clearTimeout(reconnectTimer)
  reconnectTimer = null
  terminal.connect({ sessionId: chat.current.id, agentId: chat.current.preferredExecutionAgentId, automatic })
  viewerSocket = terminal.socket
}

function scheduleReconnect() {
  clearTimeout(reconnectTimer)
  reconnectTimer = null
  if (!connectionAvailable.value || terminal.socket || !['closed', 'resyncRequired'].includes(terminal.status)
    || terminal.sessionId !== chat.current?.id || terminal.agentId !== chat.current?.preferredExecutionAgentId
    || document.visibilityState === 'hidden') return
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null
    reconnect(true)
  }, Math.min(1000 * 2 ** reconnectAttempts++, 30000))
}

function reconnectWhenVisible() {
  if (document.visibilityState === 'visible' && !terminal.socket) reconnect(true)
  else if (document.visibilityState === 'hidden') clearTimeout(reconnectTimer)
}

watch(
  () => [terminal.mode,
    agentOutput.value ? terminal.agentBufferRevision : terminal.bufferRevision,
    agentOutput.value ? terminal.agentOutputRevision : terminal.outputRevision],
  ([mode, bufferRevision, outputRevision], [previousMode, previousBufferRevision, previousOutputRevision]) => {
    if (!xterm) return
    if (mode !== previousMode || bufferRevision !== previousBufferRevision) {
      xterm.reset()
      replayBuffer(visibleBuffer.value)
    } else if (outputRevision !== previousOutputRevision) xterm.write(agentOutput.value ? terminal.agentOutput : terminal.output)
  },
  { flush: 'sync' }
)
watch(() => terminal.inputEnabled, (enabled) => {
  if (xterm) {
    xterm.options.disableStdin = !enabled
    xterm.options.cursorBlink = enabled
  }
  void nextTick(fitVisibleTerminal)
})
watch(disconnected, (value) => {
  if (value) disposeTerminal()
  else void nextTick(initializeTerminal)
})
watch(() => [terminal.status, terminal.socket, connectionAvailable.value], () => {
  if (terminal.ready || !connectionAvailable.value) reconnectAttempts = 0
  scheduleReconnect()
}, { immediate: true })
watch(
  () => [chat.current?.id, chat.current?.preferredExecutionAgentId, connectionAvailable.value],
  () => {
    if (connectionAvailable.value && chat.current?.id) {
      if (terminal.sessionId !== chat.current.id || terminal.agentId !== chat.current.preferredExecutionAgentId
        || (!terminal.socket && terminal.status !== 'replaced')) reconnect()
      viewerSocket = terminal.socket
      void nextTick(initializeTerminal)
    } else {
      terminal.release(viewerSocket)
      disposeTerminal()
    }
  },
  { immediate: true }
)

onMounted(() => {
  if (!disconnected.value) initializeTerminal()
  document.addEventListener('visibilitychange', reconnectWhenVisible)
  window.addEventListener('online', reconnectWhenVisible)
  window.visualViewport?.addEventListener('resize', fitVisibleTerminal)
  void nextTick(fitVisibleTerminal)
})
onBeforeUnmount(() => {
  clearTimeout(reconnectTimer)
  document.removeEventListener('visibilitychange', reconnectWhenVisible)
  window.removeEventListener('online', reconnectWhenVisible)
  terminal.release(viewerSocket)
  window.visualViewport?.removeEventListener('resize', fitVisibleTerminal)
  disposeTerminal()
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

:deep(.xterm) {
  height: 100%;
}
</style>
