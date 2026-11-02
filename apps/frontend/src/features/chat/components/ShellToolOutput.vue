<template>
  <div>
    <div class="shell-tool-output q-pa-sm rounded-borders overflow-hidden" role="region" aria-label="Shell-Befehl und Ausgabe">
      <div ref="host" class="fit" />
    </div>
    <div v-if="output.truncated" class="text-caption app-muted q-mt-xs">Ausgabe gekürzt.</div>
  </div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'

const props = defineProps({
  command: { type: String, required: true },
  output: { type: Object, required: true }
})
const host = ref(null)
let terminal
let observer
let previous = ['', '', '']

function updateOutput() {
  if (!terminal) return
  const next = [props.command, props.output.stdout || '', props.output.stderr || '']
  if (next[0] !== previous[0] || next.slice(1).some((text, index) => !text.startsWith(previous[index + 1]))) {
    terminal.write(`\x1bc\x1b[?25l$ ${next[0]}\r\n`)
    previous = [next[0], '', '']
  }
  // shortcut: snapshots group stdout before stderr; replay chronologically when stored results include ordered chunks.
  for (let index = 1; index < next.length; index++) {
    const delta = next[index].slice(previous[index].length)
    if (delta) terminal.write(delta)
  }
  previous = next
}

onMounted(() => {
  terminal = new Terminal({
    convertEol: true,
    disableStdin: true,
    cursorInactiveStyle: 'none',
    fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
    fontSize: 14,
    minimumContrastRatio: 4.5,
    screenReaderMode: true,
    scrollback: 50000,
    theme: { background: '#111318', foreground: '#f1f3f5', cursor: '#f1f3f5' }
  })
  const fitAddon = new FitAddon()
  terminal.loadAddon(fitAddon)
  terminal.open(host.value)
  terminal.textarea?.setAttribute('aria-label', 'Shell-Ausgabe (schreibgeschützt)')
  fitAddon.fit()
  updateOutput()
  observer = new ResizeObserver(() => fitAddon.fit())
  observer.observe(host.value)
})
watch(() => [props.command, props.output.stdout, props.output.stderr], updateOutput)
onBeforeUnmount(() => {
  observer?.disconnect()
  terminal?.dispose()
})
</script>

<style scoped>
.shell-tool-output {
  height: min(20rem, 50vh);
  min-width: 0;
  background: #111318;
}

:deep(.xterm) {
  height: 100%;
}
</style>
