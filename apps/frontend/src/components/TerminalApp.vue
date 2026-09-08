<template>
  <section class="terminal-app column full-height no-wrap" aria-label="Gemeinsames Bash-Terminal">
    <div v-if="!contextState.available" class="col column flex-center text-center q-pa-xl">
      <q-icon :name="unavailableIcon" size="52px" color="grey-6" />
      <div class="text-subtitle1 text-weight-medium q-mt-md">{{ unavailableTitle }}</div>
      <div class="text-body2 app-muted q-mt-xs">{{ unavailableMessage }}</div>
    </div>
    <div v-else-if="disconnected" class="col column flex-center text-center q-pa-xl">
      <q-icon name="link_off" size="52px" color="grey-6" />
      <div class="text-subtitle1 text-weight-medium q-mt-md" role="status">{{ disconnectedTitle }}</div>
      <div class="text-body2 app-muted q-mt-xs q-mb-md">
        {{ terminal.status === 'replaced'
          ? 'Diese Ansicht steuert das Terminal nicht mehr. Verwende die andere Ansicht oder lade diese Seite bewusst neu.'
          : 'Die Ansicht ist getrennt. Beim erneuten Verbinden wird der verfügbare Terminalzustand geladen.' }}
      </div>
      <q-btn v-if="terminal.status !== 'replaced'" unelevated no-caps color="primary" icon="refresh" label="Neu verbinden" @click="reconnect" />
    </div>
    <template v-else>
      <q-banner v-if="!terminal.ready" dense role="status">
        Terminalzustand wird geladen. Eingaben sind noch gesperrt.
      </q-banner>
      <q-banner v-else-if="terminal.suspended" dense class="bg-warning text-dark" role="status">
        Die Terminalverbindung ist vorübergehend ausgesetzt. Eingaben sind gesperrt, bis der Server sie wieder freigibt.
      </q-banner>
      <q-banner v-else-if="terminal.restored === false" dense role="status">
        Neue Shell gestartet. Ein früherer Shell-Zustand wurde nicht wiederhergestellt.
      </q-banner>
      <q-banner v-if="terminal.truncated" dense class="bg-warning text-dark" role="status">
        Der Terminalverlauf wurde gekürzt. Frühere Ausgaben fehlen.
      </q-banner>
      <div class="terminal-app__stage col relative-position">
        <div ref="terminalHost" class="terminal-app__host fit" />
      </div>
      <div class="q-px-sm q-py-xs text-caption app-muted" role="status" aria-live="polite" aria-atomic="true">
        {{ runtimeStatus }}
      </div>
    </template>
  </section>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'
import { terminalControlCenterState } from 'src/control-center/manifest'
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
const disconnectedTitle = computed(() => ({
  replaced: 'In einer anderen Ansicht geöffnet',
  resyncRequired: 'Terminalverlauf muss neu geladen werden'
})[terminal.status] || 'Terminalverbindung getrennt')
const unavailableTitle = computed(() => ({
  missing: chat.current ? 'Kein Computer ausgewählt' : 'Keine aktive Session',
  offline: 'Computer offline',
  unsupported: 'Gemeinsames Bash-Terminal nicht unterstützt'
})[contextState.value.reason])
const unavailableMessage = computed(() => ({
  missing: chat.current
    ? 'Wähle für diese Session einen verbundenen Computer aus.'
    : 'Öffne eine gespeicherte Session mit ausgewähltem Computer.',
  offline: 'Der Computer ist nicht erreichbar. Nach dem Wiederverbinden wird der verfügbare Terminalzustand geladen.',
  unsupported: 'Der ausgewählte Computer bietet die benötigten Funktionen für das gemeinsame Bash-Terminal nicht an.'
})[contextState.value.reason])
const unavailableIcon = computed(() => contextState.value.reason === 'offline' ? 'cloud_off' : 'terminal_off')
const runtimeStatus = computed(() => {
  if (!terminal.ready) return 'Terminal wird verbunden.'
  if (terminal.suspended) return 'Terminal vorübergehend ausgesetzt.'
  return ({
    idle: terminal.busy ? 'Die Shell ist beschäftigt.' : 'Shell bereit.',
    busy: 'Die Shell ist beschäftigt.',
    editing: 'Agent-Befehl direkt im Terminal bearbeiten. Enter erlaubt ihn einmalig, Strg+C verwirft die Anfrage.',
    checking: 'Befehl wird geprüft. Strg+C verwirft die Anfrage.',
    running: 'Befehl läuft. Strg+C unterbricht den Vordergrundprozess.',
    closed: 'Die Shell wurde beendet.'
  })[terminal.runtimeState] || 'Terminalzustand wird geladen.'
})

let xterm
let fitAddon
let resizeObserver
let viewerSocket
let pendingReplays = 0

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
    scrollback: 5000,
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
  replayBuffer(terminal.buffer)
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

function reconnect() {
  if (!connectionAvailable.value || !chat.current?.id || terminal.status === 'replaced') return
  terminal.connect({ sessionId: chat.current.id, agentId: chat.current.preferredExecutionAgentId })
  viewerSocket = terminal.socket
}

watch(
  () => [terminal.buffer, terminal.bufferRevision],
  ([value, revision], [previous, previousRevision]) => {
    if (!xterm) return
    if (revision === previousRevision && value.startsWith(previous)) xterm.write(value.slice(previous.length))
    else {
      xterm.reset()
      replayBuffer(value)
    }
  },
  { flush: 'post' }
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
watch(
  () => [chat.current?.id, chat.current?.preferredExecutionAgentId, connectionAvailable.value],
  () => {
    if (connectionAvailable.value && chat.current?.id) {
      if (terminal.sessionId !== chat.current.id || terminal.agentId !== chat.current.preferredExecutionAgentId) {
        terminal.connect({ sessionId: chat.current.id, agentId: chat.current.preferredExecutionAgentId })
      }
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
  window.visualViewport?.addEventListener('resize', fitVisibleTerminal)
  void nextTick(fitVisibleTerminal)
})
onBeforeUnmount(() => {
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
