<template>
  <section class="browser-app column full-height no-wrap" aria-label="Browser" @keydown.capture="browserShortcut">
    <div class="row items-center no-wrap col-auto q-px-sm" aria-label="Webseiten">
      <q-tabs :model-value="browser.navigation?.activeTabId" class="col" dense no-caps inline-label align="left" :breakpoint="0" mobile-arrows indicator-color="transparent"
        @update:model-value="browser.navigate('select', { tabId: $event })">
        <AppClosableTab v-for="tab in browser.navigation?.tabs || []" :key="tab.id" :name="tab.id"
          :label="tab.title || tab.url || 'Neuer Tab'" :icon="tab.loading ? 'hourglass_top' : 'language'"
          :active="browser.navigation.activeTabId === tab.id" :disable="!browser.canNavigate"
          @close="browser.navigate('close', { tabId: tab.id })" />
      </q-tabs>
      <q-btn flat round dense icon="add" aria-label="Neuer Tab" :disable="!browser.canNavigate || browser.navigation.tabs.length >= 32" @click="browser.navigate('new')"><q-tooltip>Neuer Tab</q-tooltip></q-btn>
    </div>
    <q-toolbar class="q-px-sm no-wrap" aria-label="Browsersteuerung">
      <q-btn v-if="['closed', 'replaced'].includes(browser.status)" flat round dense icon="refresh" aria-label="Browser erneut verbinden" :disable="!available || !confirmed" @click="open">
        <q-tooltip>Browser erneut verbinden</q-tooltip>
      </q-btn>
      <q-btn flat round dense icon="arrow_back" aria-label="Zurück" :disable="!browser.canNavigate || !browser.navigation.canGoBack" @click="browser.navigate('back')"><q-tooltip>Zurück</q-tooltip></q-btn>
      <q-btn flat round dense icon="arrow_forward" aria-label="Vorwärts" :disable="!browser.canNavigate || !browser.navigation.canGoForward" @click="browser.navigate('forward')"><q-tooltip>Vorwärts</q-tooltip></q-btn>
      <q-btn flat round dense :icon="browser.navigation?.loading ? 'close' : 'refresh'" :aria-label="browser.navigation?.loading ? 'Laden stoppen' : 'Neu laden'"
        :disable="!browser.canNavigate || !browser.activeTab" @click="browser.navigate(browser.navigation.loading ? 'stop' : 'reload')"><q-tooltip>{{ browser.navigation?.loading ? 'Laden stoppen' : 'Neu laden' }}</q-tooltip></q-btn>
      <q-input ref="addressInput" v-model="addressDraft" class="col q-mx-xs" outlined dense aria-label="Webadresse" placeholder="Webadresse" inputmode="url" autocomplete="off" autocapitalize="off" :spellcheck="false"
        :readonly="!browser.canNavigate || !browser.activeTab" :loading="browser.navigationPending" @focus="editAddress" @blur="resetAddress" @update:model-value="editAddress"
        @keydown.enter.prevent="submitAddress" @keydown.esc.prevent="resetAddress" />
      <template v-if="!$q.screen.lt.sm">
        <q-btn v-for="action in fileActions" :key="action.label" flat round dense :icon="action.icon" :aria-label="action.label" :disable="!browser.manual" @click="action.run"><q-tooltip>{{ action.label }}</q-tooltip></q-btn>
      </template>
      <q-btn ref="controlsButton" flat round dense icon="more_vert" aria-label="Weitere Browseraktionen">
        <q-tooltip>Weitere Browseraktionen</q-tooltip>
        <q-menu class="app-action-menu" anchor="bottom right" self="top right" max-width="calc(100vw - 24px)">
          <q-list padding>
            <template v-if="$q.screen.lt.sm">
              <q-item v-for="action in fileActions" :key="action.label" clickable v-close-popup :disable="!browser.manual" @click="action.run"><q-item-section avatar><q-icon :name="action.icon" /></q-item-section><q-item-section>{{ action.label }}</q-item-section></q-item>
              <q-separator />
            </template>
            <q-item><q-item-section><q-toggle :model-value="browser.navigation?.nativeChrome === true" dense label="Browseroberfläche anzeigen" :disable="!browser.canNavigate"
              @update:model-value="browser.navigate('chrome', { nativeChrome: $event })" /></q-item-section></q-item>
            <q-item v-if="options.length > 1"><q-item-section>
              <q-select v-model="selectedId" outlined dense emit-value map-options label="Browser-Integration"
                :options="options" :disable="!['closed', 'replaced'].includes(browser.status)" :behavior="$q.screen.lt.sm ? 'dialog' : 'menu'" />
            </q-item-section></q-item>
            <q-item><q-item-section><q-item-label>{{ options.find((item) => item.value === selectedId)?.label || 'Browser' }}</q-item-label><q-item-label caption role="status">{{ browserStatusLabel(status) }}</q-item-label></q-item-section></q-item>
            <q-item clickable v-close-popup :disable="!available || checking" @click="checkStatus"><q-item-section avatar><q-icon name="refresh" /></q-item-section><q-item-section>Status prüfen</q-item-section></q-item>
            <q-item clickable v-close-popup :disable="!browser.ready" @click="retryResize"><q-item-section avatar><q-icon name="aspect_ratio" /></q-item-section><q-item-section>Auflösung an Fenster anpassen</q-item-section></q-item>
            <q-item clickable v-close-popup :disable="!browser.ready" @click="browser.toggleViewportScale()"><q-item-section avatar><q-icon :name="browser.scaleViewport ? 'zoom_in' : 'fit_screen'" /></q-item-section><q-item-section>{{ browser.scaleViewport ? 'Skalierung ausschalten (1:1)' : 'Ansicht einpassen' }}</q-item-section></q-item>
            <q-item clickable v-close-popup :to="{ name: 'settings-integrations', query: { scope: 'personal' } }"><q-item-section avatar><q-icon name="settings" /></q-item-section><q-item-section>Einrichtung</q-item-section></q-item>
            <q-item clickable v-close-popup :disable="browser.status !== 'open'" @click="browser.release({ close: true })"><q-item-section avatar><q-icon name="link_off" /></q-item-section><q-item-section>Browser schließen</q-item-section></q-item>
          </q-list>
        </q-menu>
      </q-btn>
    </q-toolbar>
    <q-banner v-if="['occupied', 'waiting', 'takingOver', 'replaced'].includes(browser.status)" class="app-surface-tonal col-auto" role="status" aria-live="polite">
      <template v-if="browser.status === 'occupied'">
        Der Browser wird bereits in {{ browser.offer?.ownerSession?.title || 'einer anderen Ansicht' }} verwendet.
        {{ browser.offer?.busy ? 'Eine laufende Aktion wird vor der Übernahme beendet.' : '' }}
      </template>
      <template v-else-if="browser.status === 'waiting'">Der Browser wird gerade geöffnet oder geschlossen. Bitte warten.</template>
      <template v-else-if="browser.status === 'takingOver'">Browser wird hier übernommen. Eine laufende Aktion wird zuerst beendet.</template>
      <template v-else>Der Browser wurde in einer anderen Ansicht übernommen. Du kannst ihn bei Bedarf erneut verbinden.</template>
      <template v-if="browser.status === 'occupied'" #action>
        <q-btn color="primary" no-caps label="Browser hier übernehmen" :disable="!browser.offer?.canTakeover" @click="browser.takeover()" />
      </template>
    </q-banner>
    <q-linear-progress v-if="browser.status === 'takingOver'" indeterminate aria-label="Browser wird hier übernommen" />
    <q-linear-progress v-else-if="browser.status === 'connecting' || (browser.status === 'open' && !browser.ready)" indeterminate aria-label="Browser wird verbunden" />
    <div ref="host" class="browser-app__view col overflow-hidden" @pointerdown.capture="focusViewer" @keydown.capture="viewerKey" @keyup.capture="viewerKey" @paste.capture="nativePaste" @focusout="leaveViewer" />

    <q-dialog v-model="clipboardOpen" :maximized="$q.screen.lt.sm">
      <q-card class="integration-dialog app-surface column no-wrap">
        <q-card-section class="text-h6">{{ clipboardMode === 'copy' ? 'Browsertext kopieren' : 'Text in den Browser übertragen' }}</q-card-section>
        <q-card-section class="col scroll">
          <p>{{ clipboardMode === 'copy' ? 'Browsertext lokal kopieren oder im Textfeld markieren und manuell kopieren.' : 'Text hier einfügen oder die lokale Zwischenablage ausdrücklich lesen. Der UTF-8-Text muss kleiner als 16 KiB sein.' }}</p>
          <q-input v-model="clipboardText" outlined type="textarea" label="Text" :readonly="clipboardMode === 'copy'" :loading="browser.transferring" :disable="browser.transferring" :error="!clipboardFits(clipboardText)" error-message="Der UTF-8-Text muss kleiner als 16 KiB sein." />
          <p v-if="clipboardMode === 'paste'" class="text-caption q-mt-sm">„Zwischenablage setzen“ überträgt den Text unverändert. Wähle anschließend im Kontextmenü des entfernten Browsers „Einfügen“. Strg+V würde stattdessen erneut deine lokale Zwischenablage übertragen.</p>
          <div v-if="clipboardNotice" role="status">{{ clipboardNotice }}</div>
        </q-card-section>
        <q-card-actions align="right">
          <q-btn flat no-caps label="Schließen" v-close-popup />
          <q-btn v-if="clipboardMode === 'copy'" flat no-caps label="Browsertext lesen" :disable="!browser.manual" @click="loadClipboard" />
          <q-btn v-if="clipboardMode === 'copy'" color="primary" no-caps label="Lokal kopieren" :disable="!clipboardReady || !browser.manual || !clipboardFits(clipboardText)" @click="copyLocal" />
          <template v-else>
            <q-btn flat no-caps label="Lokal lesen" :disable="!browser.manual" @click="readLocal" />
            <q-btn color="primary" no-caps label="Zwischenablage setzen" :loading="browser.transferring" :disable="!browser.manual || !clipboardFits(clipboardText)" @click="paste" />
          </template>
        </q-card-actions>
      </q-card>
    </q-dialog>
    <q-dialog v-model="filesOpen" :maximized="$q.screen.lt.sm">
      <q-card class="integration-dialog app-surface column no-wrap">
        <q-card-section class="text-h6">Browser-Dateien</q-card-section>
        <q-card-section class="col scroll">
          <p>Klicke zuerst im entfernten Browser auf das Upload-Feld der Webseite. Öffne danach „Datei hochladen“, wähle hier eine lokale Datei und klicke auf „Datei bereitstellen“. Die Datei wird ohne Agenten in das wartende Datei-Feld eingesetzt.</p>
          <q-file v-model="uploadFile" outlined label="Datei zum Hochladen" :disable="!browser.manual" />
          <q-btn class="q-mt-sm" color="primary" no-caps label="Datei bereitstellen" :loading="browser.transferring" :disable="!uploadFile || !browser.manual" @click="upload()" />
          <div v-if="browser.uploadId" role="status" class="q-mt-sm">
            Datei bereitgestellt, aber noch nicht eingesetzt. Schließe diesen Dialog, klicke auf das Upload-Feld der Webseite und wähle hier anschließend „Datei einsetzen“.
          </div>
          <q-btn v-if="browser.uploadId" class="q-mt-sm" color="primary" no-caps label="Datei einsetzen" :loading="browser.transferring" :disable="!browser.manual" @click="upload(true)" />
          <div class="row items-center q-mt-md"><div class="text-subtitle1 col">Downloads</div><q-btn flat round icon="refresh" aria-label="Downloads aktualisieren" :disable="!browser.manual" @click="refreshDownloads" /></div>
          <q-list separator>
            <q-item v-for="file in browser.downloads" :key="file.id">
              <q-item-section><q-item-label>{{ file.filename }}</q-item-label><q-item-label caption>{{ file.size }} Bytes</q-item-label></q-item-section>
              <q-item-section side><q-btn flat round icon="download" :aria-label="`${file.filename} herunterladen`" :disable="!browser.manual" @click="download(file)" /></q-item-section>
            </q-item>
            <q-item v-if="!browser.downloads.length"><q-item-section>Noch keine Downloads.</q-item-section></q-item>
          </q-list>
        </q-card-section>
        <q-card-actions align="right"><q-btn flat no-caps label="Schließen" v-close-popup /></q-card-actions>
      </q-card>
    </q-dialog>
  </section>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useQuasar } from 'quasar'
import { useRoute } from 'vue-router'
import AppClosableTab from 'components/AppClosableTab.vue'
import { useBrowserStore } from 'stores/browser'
import { useChatStore } from 'stores/chat'
import { useControlCenterStore } from 'stores/control-center'
import { useExecutionAgentStore } from 'stores/execution-agents'
import { useIntegrationStore } from 'stores/integrations'
import { browserNavigationUrl, browserStatusLabel, clipboardFits } from 'src/utils/browser'
import { notifyError } from 'src/utils/notifications'

const browser = useBrowserStore()
const $q = useQuasar()
const chat = useChatStore()
const controlCenter = useControlCenterStore()
const route = useRoute()
const agents = useExecutionAgentStore()
const integrations = useIntegrationStore()
const selectedId = ref(browser.integrationId)
const host = ref(null)
const controlsButton = ref(null)
const addressInput = ref(null)
const addressDraft = ref('')
let addressEditing = false
let addressTabId = null
const checking = ref(false)
const clipboardOpen = ref(false)
const clipboardMode = ref('copy')
const clipboardText = ref('')
const clipboardNotice = ref('')
const clipboardReady = ref(false)
let clipboardDialog = 0
const filesOpen = ref(false)
const uploadFile = ref(null)
const fileActions = [
  { label: 'Text kopieren', icon: 'content_copy', run: () => openClipboard('copy') },
  { label: 'Text einfügen', icon: 'content_paste', run: () => openClipboard('paste') },
  { label: 'Datei hochladen', icon: 'upload_file', run: () => { filesOpen.value = true } },
  { label: 'Downloads', icon: 'download', run: showDownloads }
]
const options = computed(() => integrations.browser.instances.filter((item) => item.enabled && item.executionAgentId === chat.current?.preferredExecutionAgentId).map((item) => ({ label: item.name, value: item.id })))
const available = computed(() => Boolean(chat.current?.id && integrations.adapterAvailable('browser', 'personal') && agents.byId(chat.current.preferredExecutionAgentId)?.connected && options.value.some((item) => item.value === selectedId.value)))
const status = computed(() => integrations.browserStatuses[selectedId.value])
const visible = computed(() => route.name === 'chat' && route.query.session === chat.current?.id
  && controlCenter.sessionId === chat.current?.id && controlCenter.ready
  && controlCenter.open && controlCenter.activeContentId === 'browser')
const confirmed = computed(() => controlCenter.confirmedState.open && controlCenter.confirmedState.activeAppId === 'browser'
  && controlCenter.confirmedState.tabs.some((tab) => tab.appId === 'browser'))
let observer
let contentSize = { width: 0, height: 0 }
let pasteTimer
let inputGeneration = 0
let copying = false
const heldControls = new Set()

watch(options, (value) => { if (!value.some((item) => item.value === selectedId.value)) selectedId.value = value[0]?.value ?? null }, { immediate: true })
watch(() => [visible.value, confirmed.value, selectedId.value, available.value, chat.current?.id, chat.current?.preferredExecutionAgentId], activate, { flush: 'post' })
watch(() => [visible.value, browser.browserId, browser.viewId, browser.ready, host.value], refreshView, { flush: 'post' })
watch(() => browser.generation, () => {
  resetAddress()
  clipboardOpen.value = false
  filesOpen.value = false
  clipboardText.value = ''
  uploadFile.value = null
})
watch(clipboardOpen, () => { clipboardDialog += 1 }, { flush: 'sync' })
watch(() => [visible.value, browser.viewGeneration, browser.attached, browser.busy, browser.submitting, browser.navigationPending], invalidateInput, { flush: 'sync' })
watch(() => browser.activeTab, () => { if (!addressEditing) resetAddress() }, { immediate: true })
watch(() => browser.attached, (attached) => { if (attached) resetAddress() })

onMounted(() => {
  observer = new ResizeObserver(([entry]) => {
    contentSize = entry.contentRect
    activate()
    if (visible.value && browser.viewParent === host.value) browser.resize(contentSize.width, contentSize.height)
  })
  observer.observe(host.value, { box: 'content-box' })
  document.addEventListener('visibilitychange', visibilityChanged)
  window.addEventListener('blur', invalidateInput)
  activate()
  void refreshView()
})
onBeforeUnmount(() => {
  observer?.disconnect()
  document.removeEventListener('visibilitychange', visibilityChanged)
  window.removeEventListener('blur', invalidateInput)
  invalidateInput()
  if (browser.viewParent === host.value) browser.viewVisible = false
})

function measurable() {
  const box = host.value?.getClientRects()[0]
  return visible.value && !document.hidden && box?.width > 0 && box?.height > 0
}
function activate() {
  const context = { integrationId: selectedId.value, sessionId: chat.current?.id, agentId: chat.current?.preferredExecutionAgentId }
  browser.activate(visible.value ? JSON.stringify(context) : null, context, confirmed.value && available.value && measurable())
}
async function refreshView() {
  await nextTick()
  if (measurable() && browser.browserId) await browser.mountView(host.value)
  if (browser.viewParent === host.value) {
    browser.viewVisible = measurable()
    if (browser.viewVisible) browser.resize(contentSize.width, contentSize.height)
  }
}
function visibilityChanged() { invalidateInput(); activate(); void refreshView() }
function retryResize() { browser.resizeLast = null; void refreshView() }

function editAddress() {
  if (!browser.canNavigate || addressEditing) return
  addressEditing = true
  addressTabId = browser.activeTab?.id ?? null
}
function resetAddress() {
  addressEditing = false
  addressTabId = browser.activeTab?.id ?? null
  addressDraft.value = browser.activeTab?.url ?? ''
}
function submitAddress(event) {
  if (event.isComposing || !browser.canNavigate || !addressTabId) return
  const url = browserNavigationUrl(addressDraft.value)
  if (url) browser.navigate('navigate', { tabId: addressTabId, url })
  else notifyError({ error: { code: 'browser.invalidTarget' } })
  resetAddress()
}
function browserShortcut(event) {
  const key = event.key.toLowerCase()
  if (!event.ctrlKey || event.altKey || event.shiftKey || event.metaKey || !['l', 't', 'w'].includes(key)
    || !measurable() || clipboardOpen.value || filesOpen.value) return
  const target = event.target
  const address = target === addressInput.value?.nativeEl
  const canvas = target.tagName === 'CANVAS' && target === document.activeElement
    && browser.viewParent === host.value && host.value.contains(target)
  if (!address && (key === 'l' ? target.closest?.('input, textarea, select, [contenteditable]')
    : !canvas)) return
  // A held shortcut must not escape to the local browser while its request is pending.
  if (!browser.canNavigate && !(event.repeat && browser.navigationPending)) return
  if (key !== 't' && !browser.activeTab) return
  event.preventDefault()
  event.stopImmediatePropagation()
  if (event.repeat) return
  invalidateInput()
  if (key === 'l') {
    addressInput.value?.focus()
    addressInput.value?.select()
  } else {
    resetAddress()
    browser.navigate(key === 't' ? 'new' : 'close')
  }
}

function open() {
  if (available.value && confirmed.value && measurable()) browser.connect({ integrationId: selectedId.value, sessionId: chat.current.id, agentId: chat.current.preferredExecutionAgentId })
}
function focusedViewer() {
  return measurable() && browser.manual && browser.viewParent === host.value && host.value.contains(document.activeElement)
}
function focusViewer(event) {
  if (event.target.tagName !== 'CANVAS' || !browser.manual || !measurable()) return
  resetAddress()
  addressInput.value?.blur()
  browser.rfb?.focus()
}
function invalidateInput() {
  inputGeneration += 1
  if (!visible.value) browser.activate(null, null, false)
  if (!visible.value || document.hidden) browser.viewVisible = false
  heldControls.clear()
  browser.releaseControlKeys()
  clearTimeout(pasteTimer)
}
function leaveViewer(event) { if (!host.value?.contains(event.relatedTarget)) invalidateInput() }
function viewerKey(event) {
  if (event.key === 'F12' && event.target.tagName === 'CANVAS' && event.target === document.activeElement && focusedViewer()
    && !event.ctrlKey && !event.shiftKey && !event.altKey && !event.metaKey) {
    event.preventDefault()
    event.stopImmediatePropagation()
    if (event.type === 'keydown' && !event.repeat) browser.devToolsChord()
    return
  }
  if (event.key === 'Control' && (focusedViewer() || heldControls.has(event.code))) {
    // noVNC's Windows AltGr timer must not forward a delayed Ctrl during a clipboard transfer.
    event.stopImmediatePropagation()
    if (event.type === 'keydown') heldControls.add(event.code)
    else heldControls.delete(event.code)
    browser.controlKey(event.code, event.type === 'keydown')
    return
  }
  if (!focusedViewer()) return
  if (event.key === 'AltGraph') {
    // Windows may precede AltGr with a synthetic Ctrl; leave AltGraph itself to noVNC.
    heldControls.clear()
    browser.releaseControlKeys()
  }
  if (event.ctrlKey && event.altKey && event.key === 'Escape' && event.type === 'keydown') {
    event.preventDefault()
    event.stopImmediatePropagation()
    browser.rfb.sendKey(0xffe9, 'AltLeft', false)
    browser.rfb.sendKey(0xffea, 'AltRight', false)
    invalidateInput()
    controlsButton.value?.$el.focus()
    return
  }
  const key = event.key.toLowerCase()
  if (event.ctrlKey && event.type === 'keydown' && !['c', 'v'].includes(key)) {
    for (const code of heldControls) if (!browser.controlKeys.includes(code)) browser.controlKey(code, true)
  }
  if (!event.ctrlKey || event.altKey || event.shiftKey || !['c', 'v'].includes(key)) return
  event.stopImmediatePropagation()
  if (key === 'c') event.preventDefault()
  if (event.type !== 'keydown' || event.repeat) return
  browser.releaseControlKeys()
  if (key === 'c') void shortcutCopy()
  else {
    // Keep the native default: only the trusted paste event may supply local text.
    clearTimeout(pasteTimer)
    pasteTimer = setTimeout(() => { if (focusedViewer()) void openClipboard('paste') }, 1000)
  }
}
async function nativePaste(event) {
  if (!event.isTrusted || !focusedViewer()) return
  event.preventDefault()
  event.stopImmediatePropagation()
  clearTimeout(pasteTimer)
  const data = event.clipboardData
  const text = data?.getData('text/plain')
  if (data?.files.length || !Array.from(data?.types || []).includes('text/plain') || !clipboardFits(text)) {
    await openClipboard('paste')
    clipboardNotice.value = 'Nur reiner Text kleiner als 16 KiB ist möglich. Dateien bitte über „Datei hochladen“ übertragen.'
    return
  }
  const generation = inputGeneration
  try {
    const result = await browser.transferClipboard(text)
    if (result && generation === inputGeneration && focusedViewer()) {
      browser.clipboardChord('v')
      for (const code of heldControls) browser.controlKey(code, true)
    }
  } catch { /* Central API toast. */ }
}
async function shortcutCopy() {
  if (copying || !focusedViewer()) return
  copying = true
  const generation = inputGeneration
  let text = null
  const selection = browser.copyClipboard().then((result) => {
    if (!result || generation !== inputGeneration || !focusedViewer()) throw new Error('Browser clipboard is unavailable.')
    text = result.text
    return new Blob([text], { type: 'text/plain' })
  })
  // Attach a rejection handler even if constructing ClipboardItem throws synchronously.
  void selection.catch(() => {})
  try {
    if (!window.isSecureContext) throw new Error('Clipboard writing is unavailable.')
    if (globalThis.ClipboardItem && navigator.clipboard?.write) {
      await navigator.clipboard.write([new ClipboardItem({ 'text/plain': selection })])
    } else {
      // Best effort: writeText may reject after the request consumes user activation.
      await selection
      if (generation !== inputGeneration || !focusedViewer()) return
      await navigator.clipboard.writeText(text)
    }
  } catch {
    await selection.catch(() => {})
    if (generation === inputGeneration && focusedViewer()) {
      void openClipboard('copy', false)
      if (text !== null) {
        clipboardText.value = text
        clipboardReady.value = true
        clipboardNotice.value = 'Lokaler Zugriff nicht möglich. Bitte „Lokal kopieren“ wählen oder den Text manuell kopieren.'
      } else clipboardNotice.value = 'Kopieren nicht möglich. Du kannst die Browser-Zwischenablage mit „Browsertext lesen“ abrufen.'
    }
  } finally {
    copying = false
    if (generation === inputGeneration && focusedViewer()) {
      for (const code of heldControls) browser.controlKey(code, true)
    }
  }
}
async function checkStatus() {
  checking.value = true
  try { await integrations.browserStatus(selectedId.value) } catch { /* Central API toast. */ }
  finally { checking.value = false }
}
async function openClipboard(mode, read = true) {
  if (!browser.manual) return
  clipboardMode.value = mode
  clipboardText.value = ''
  clipboardNotice.value = ''
  clipboardReady.value = false
  clipboardOpen.value = true
  if (mode === 'copy' && read) await loadClipboard()
}
async function loadClipboard() {
  if (!browser.manual) return
  clipboardReady.value = false
  const dialog = clipboardDialog
  try {
    const result = await browser.transferClipboard()
    if (dialog !== clipboardDialog || !clipboardOpen.value) return
    if (result) {
      clipboardText.value = result.text
      clipboardReady.value = true
    } else clipboardNotice.value = 'Browsertext nicht verfügbar. Bitte den Dialog erneut öffnen.'
  } catch {
    if (dialog === clipboardDialog) clipboardNotice.value = 'Browsertext konnte nicht geladen werden. Bitte den Dialog erneut öffnen.'
  }
}
async function readLocal() {
  if (!browser.manual) return
  const generation = browser.generation
  const dialog = clipboardDialog
  try {
    const text = await navigator.clipboard.readText()
    if (generation === browser.generation && dialog === clipboardDialog && clipboardOpen.value) clipboardText.value = text
  } catch {
    if (dialog === clipboardDialog) clipboardNotice.value = 'Zugriff nicht möglich. Bitte den Text manuell in das Feld einfügen.'
  }
}
async function copyLocal() {
  if (!clipboardReady.value || !browser.manual || !clipboardFits(clipboardText.value)) return
  const dialog = clipboardDialog
  try {
    await navigator.clipboard.writeText(clipboardText.value)
    if (dialog === clipboardDialog) clipboardNotice.value = 'Text wurde lokal kopiert.'
  } catch {
    if (dialog === clipboardDialog) clipboardNotice.value = 'Zugriff nicht möglich. Bitte den Text im Feld markieren und manuell kopieren.'
  }
}
async function paste() {
  const dialog = clipboardDialog
  try {
    const result = await browser.transferClipboard(clipboardText.value)
    if (result && dialog === clipboardDialog && clipboardOpen.value) {
      $q.notify({ type: 'positive', message: 'Text in die Browser-Zwischenablage übertragen.' })
      clipboardOpen.value = false
    }
  } catch { /* Central API toast. */ }
}
async function refreshDownloads() { try { await browser.transfer('downloads') } catch { /* Central API toast. */ } }
function showDownloads() { filesOpen.value = true; void refreshDownloads() }
async function upload(choose = false) {
  try {
    const result = await browser.transfer(choose ? 'choose' : 'upload', uploadFile.value)
    if (!result) return
    uploadFile.value = null
    if (result.chosen || result.ok) {
      $q.notify({ type: 'positive', message: 'Datei in das Upload-Feld eingesetzt.' })
      filesOpen.value = false
    }
  } catch { /* Central API toast. */ }
}
async function download(file) { try { await browser.download(file) } catch { /* Central API toast. */ } }
</script>

<style scoped>
.browser-app, .browser-app__view { min-height: 0; min-width: 0; }
.browser-app__view:focus-within { outline: 2px solid var(--q-primary); outline-offset: -2px; }
</style>
