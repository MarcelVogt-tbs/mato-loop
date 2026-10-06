<template>
  <section class="browser-app column full-height no-wrap" aria-label="Browser" @keydown.capture="browserShortcut">
    <div v-if="!browserStateMessage" class="row items-center no-wrap col-auto q-px-sm" aria-label="Webseiten">
      <q-tabs :model-value="navigation?.activeTabId" class="col" dense no-caps inline-label align="left" :breakpoint="0" mobile-arrows indicator-color="transparent"
        @update:model-value="browser.navigate('select', { tabId: $event })">
        <AppClosableTab v-for="tab in navigation?.tabs || []" :key="tab.id" :name="tab.id"
          :label="tab.title || tab.url || 'Neuer Tab'" :icon="tab.loading ? 'hourglass_top' : 'language'"
          :active="navigation.activeTabId === tab.id" :disable="!browser.canNavigate"
          @close="browser.navigate('close', { tabId: tab.id })" />
      </q-tabs>
      <q-btn flat round dense icon="add" aria-label="Neuer Tab" :disable="!browser.canNavigate || navigation.tabs.length >= 32" @click="browser.navigate('new')"><q-tooltip>Neuer Tab</q-tooltip></q-btn>
    </div>
    <q-toolbar v-if="!browserStateMessage" class="q-px-sm no-wrap" aria-label="Browsersteuerung">
      <q-btn v-if="['closed', 'replaced'].includes(browser.displayStatus)" flat round dense icon="refresh" aria-label="Browser erneut verbinden" :disable="!available || !confirmed" @click="open">
        <q-tooltip>Browser erneut verbinden</q-tooltip>
      </q-btn>
      <q-btn flat round dense icon="arrow_back" aria-label="Zurück" :disable="!browser.canNavigate || !navigation.canGoBack" @click="browser.navigate('back')"><q-tooltip>Zurück</q-tooltip></q-btn>
      <q-btn flat round dense icon="arrow_forward" aria-label="Vorwärts" :disable="!browser.canNavigate || !navigation.canGoForward" @click="browser.navigate('forward')"><q-tooltip>Vorwärts</q-tooltip></q-btn>
      <q-btn flat round dense :icon="navigation?.loading ? 'close' : 'refresh'" :aria-label="navigation?.loading ? 'Laden stoppen' : 'Neu laden'"
        :disable="!browser.canNavigate || !browser.activeTab" @click="browser.navigate(navigation.loading ? 'stop' : 'reload')"><q-tooltip>{{ navigation?.loading ? 'Laden stoppen' : 'Neu laden' }}</q-tooltip></q-btn>
      <q-input ref="addressInput" v-model="addressDraft" class="col q-mx-xs" outlined dense aria-label="Webadresse" placeholder="Webadresse" inputmode="url" autocomplete="off" autocapitalize="off" :spellcheck="false"
        :readonly="!browser.canNavigate || !browser.activeTab" :loading="browser.ownsDisplay && browser.navigationPending" @focus="editAddress" @blur="resetAddress" @update:model-value="editAddress"
        @keydown.enter.prevent="submitAddress" @keydown.esc.prevent="resetAddress" />
      <template v-if="!$q.screen.lt.sm">
        <q-btn v-for="action in fileActions" :key="action.label" flat round dense :icon="action.icon" :aria-label="action.label" :disable="!browser.manual" @click="action.run"><q-tooltip>{{ action.label }}</q-tooltip></q-btn>
      </template>
      <q-btn ref="controlsButton" flat round dense icon="more_vert" aria-label="Weitere Browseraktionen">
        <q-tooltip>Weitere Browseraktionen</q-tooltip>
        <AppMenu anchor="bottom right" self="top right" max-width="calc(100vw - 24px)">
          <q-list padding>
            <template v-if="$q.screen.lt.sm">
              <q-item v-for="action in fileActions" :key="action.label" clickable v-close-popup :disable="!browser.manual" @click="action.run"><q-item-section avatar><q-icon :name="action.icon" /></q-item-section><q-item-section>{{ action.label }}</q-item-section></q-item>
              <q-separator />
            </template>
            <q-item><q-item-section><q-toggle :model-value="navigation?.nativeChrome === true" dense label="Browseroberfläche anzeigen" :disable="!browser.canNavigate"
              @update:model-value="browser.navigate('chrome', { nativeChrome: $event })" /></q-item-section></q-item>
            <q-item v-if="options.length > 1"><q-item-section>
              <AppSelect v-model="selectedId" outlined dense emit-value map-options label="Browser-Integration"
                :options="options" :disable="browser.ownsDisplay && !['closed', 'replaced'].includes(browser.status)" />
            </q-item-section></q-item>
            <q-item><q-item-section><q-item-label>{{ options.find((item) => item.value === selectedId)?.label || 'Browser' }}</q-item-label><q-item-label caption role="status">{{ browserStatusLabel(status) }}</q-item-label></q-item-section></q-item>
            <q-item clickable v-close-popup :disable="!available || checking" @click="checkStatus"><q-item-section avatar><q-icon name="refresh" /></q-item-section><q-item-section>Status prüfen</q-item-section></q-item>
            <q-item clickable v-close-popup :disable="!browser.ready" @click="retryResize"><q-item-section avatar><q-icon name="aspect_ratio" /></q-item-section><q-item-section>Auflösung an Fenster anpassen</q-item-section></q-item>
            <q-item clickable v-close-popup :disable="!browser.ready" @click="browser.toggleViewportScale()"><q-item-section avatar><q-icon :name="browser.scaleViewport ? 'zoom_in' : 'fit_screen'" /></q-item-section><q-item-section>{{ browser.scaleViewport ? 'Skalierung ausschalten (1:1)' : 'Ansicht einpassen' }}</q-item-section></q-item>
            <q-item clickable v-close-popup :to="{ name: 'settings-integrations', query: { scope: 'personal' } }"><q-item-section avatar><q-icon name="settings" /></q-item-section><q-item-section>Einrichtung</q-item-section></q-item>
            <q-item clickable v-close-popup :disable="!browser.ownsDisplay || browser.status !== 'open'" @click="browser.release({ close: true })"><q-item-section avatar><q-icon name="link_off" /></q-item-section><q-item-section>Browser schließen</q-item-section></q-item>
          </q-list>
        </AppMenu>
      </q-btn>
    </q-toolbar>
    <AppStateMessage
      v-if="browserStateMessage"
      class="col q-pa-xl"
      role="status"
      icon="language"
      :title="browserStateMessage.title"
      :description="browserStateMessage.description"
    >
      <template v-if="['occupied', 'replaced'].includes(browser.displayStatus)" #actions>
        <q-btn v-if="browser.displayStatus === 'occupied'" color="primary" no-caps label="Browser hier übernehmen" :disable="!available || !confirmed || (Boolean(browser.displayOffer) && !browser.displayOffer.canTakeover)" @click="takeover" />
        <q-btn v-else unelevated no-caps color="primary" icon="refresh" label="Neu verbinden" :disable="!available || !confirmed" @click="open" />
      </template>
    </AppStateMessage>
    <q-linear-progress v-if="browser.displayStatus === 'takingOver'" indeterminate aria-label="Browser wird hier übernommen" />
    <q-linear-progress v-else-if="browser.displayStatus === 'connecting' || (browser.displayStatus === 'open' && !browser.ready)" indeterminate aria-label="Browser wird verbunden" />
    <div v-show="!browserStateMessage" ref="host" class="browser-app__view col overflow-hidden" @pointerdown.capture="focusViewer" @keydown.capture="viewerKey" @keyup.capture="viewerKey" @paste.capture="nativePaste" @focusout="leaveViewer" />

    <q-dialog v-model="clipboardOpen" :maximized="$q.screen.lt.sm">
      <q-card class="integration-dialog app-surface column no-wrap">
        <q-card-section class="text-h6">{{ clipboardMode === 'copy' ? 'Browsertext kopieren' : 'Text in den Browser übertragen' }}</q-card-section>
        <q-card-section class="col scroll">
          <p>{{ clipboardMode === 'copy' ? 'Browsertext lokal kopieren oder im Textfeld markieren und manuell kopieren.' : 'Text hier einfügen oder die lokale Zwischenablage ausdrücklich lesen. Der UTF-8-Text muss kleiner als 16 KiB sein.' }}</p>
          <q-input dense v-model="clipboardText" outlined type="textarea" label="Text" :readonly="clipboardMode === 'copy'" :loading="browser.transferring" :disable="browser.transferring" :error="!clipboardFits(clipboardText)" error-message="Der UTF-8-Text muss kleiner als 16 KiB sein." />
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
      <q-card class="integration-dialog app-surface column no-wrap" aria-labelledby="browser-files-title">
        <q-card-section>
          <div id="browser-files-title" class="text-h6">Browser-Dateien</div>
          <div class="text-body2 app-muted q-mt-xs">Wähle eine vorhandene Datei aus oder füge eine lokale Datei im aktuellen Ordner hinzu.</div>
        </q-card-section>
        <q-tabs :model-value="browser.pickerScope" dense no-caps align="left" active-color="primary" :disable="!browser.manual" @update:model-value="browser.setPickerScope($event)">
          <q-tab name="session" icon="folder_open" label="Ablage" />
          <q-tab name="filesystem" icon="computer" label="Computer-Dateien" :disable="!pickerContext.filesystemList" />
        </q-tabs>
        <q-separator />
        <q-toolbar class="q-px-sm" aria-label="Dateiordner durchsuchen">
          <q-btn flat round dense icon="arrow_upward" aria-label="Zum übergeordneten Ordner" :disable="pickerAtRoot || browser.pickerLoading || !browser.manual" @click="openPickerDirectory(browser.pickerParent())"><q-tooltip>Zum übergeordneten Ordner</q-tooltip></q-btn>
          <div class="col ellipsis q-px-sm" :title="pickerDirectory">{{ pickerDirectory }}</div>
          <q-btn flat round dense icon="refresh" aria-label="Dateien aktualisieren" :loading="browser.pickerLoading" :disable="!browser.manual" @click="browser.loadPicker()"><q-tooltip>Dateien aktualisieren</q-tooltip></q-btn>
          <q-btn flat round dense color="primary" icon="upload_file" aria-label="Lokale Datei hinzufügen" :loading="browser.transferring" :disable="!pickerCanUpload || !browser.manual" @click="chooseLocalFile"><q-tooltip>Lokale Datei hinzufügen</q-tooltip></q-btn>
          <input ref="uploadInput" type="file" class="hidden" aria-label="Lokale Datei auswählen" @change="uploadLocalFile">
        </q-toolbar>
        <q-linear-progress v-if="browser.pickerLoading" indeterminate aria-label="Dateien werden geladen" />
        <q-card-section class="browser-file-picker col scroll q-pa-sm">
          <div v-if="browser.pickerError" class="file-load-state q-pa-md text-center" role="alert">
            <div>{{ pickerError }}</div>
            <q-btn flat no-caps color="primary" label="Erneut versuchen" class="q-mt-sm" @click="browser.loadPicker()" />
          </div>
          <q-list v-else separator role="group" aria-label="Dateien und Ordner">
            <q-item v-for="entry in browser.pickerEntries" :key="entry.path" clickable v-ripple
              :active="browser.pickerSelected?.path === entry.path" role="button"
              :aria-pressed="entry.kind === 'file' ? browser.pickerSelected?.path === entry.path : undefined"
              @click="activatePickerEntry(entry)" @dblclick="entry.kind === 'file' && useSelectedFile()">
              <q-item-section avatar><q-icon :name="entry.kind === 'directory' ? 'folder' : entry.kind === 'file' ? 'description' : 'link'" :color="entry.kind === 'symlink' ? 'warning' : undefined" /></q-item-section>
              <q-item-section><q-item-label>{{ entry.name }}</q-item-label><q-item-label v-if="entry.kind === 'file' && Number.isFinite(entry.size)" caption>{{ entry.size }} Bytes</q-item-label></q-item-section>
              <q-item-section v-if="browser.pickerSelected?.path === entry.path" side><q-icon name="check" color="primary" aria-label="Ausgewählt" /></q-item-section>
            </q-item>
            <q-item v-if="!browser.pickerLoading && !browser.pickerEntries.length"><q-item-section class="app-muted">Dieser Ordner ist leer.</q-item-section></q-item>
          </q-list>
          <q-btn v-if="browser.pickerHasMore" flat dense no-caps color="primary" label="Weitere laden" class="q-mt-sm" :disable="browser.pickerLoading" @click="browser.loadPicker(true)" />
          <div v-if="browser.uploadId" role="status" class="q-mt-sm">
            Datei bereitgestellt, aber noch nicht eingesetzt. Schließe diesen Dialog, klicke auf das Upload-Feld der Webseite und wähle hier anschließend „Datei einsetzen“.
          </div>
          <q-btn v-if="browser.uploadId" class="q-mt-sm" color="primary" no-caps label="Datei einsetzen" :loading="browser.transferring" :disable="!browser.manual" @click="retryChoose" />
          <q-separator spaced />
          <div class="row items-center"><div class="text-subtitle1 col">Downloads</div><q-btn flat round dense icon="refresh" aria-label="Downloads aktualisieren" :disable="!browser.manual" @click="refreshDownloads" /></div>
          <q-list separator>
            <q-item v-for="file in browser.downloads" :key="file.id">
              <q-item-section avatar><q-icon :name="downloadIcon(file)" :color="downloadColor(file)" /></q-item-section>
              <q-item-section><q-item-label>{{ file.filename }}</q-item-label><q-item-label caption>{{ downloadLabel(file) }}</q-item-label><q-item-label v-if="downloadPath(file)" caption class="ellipsis" :title="downloadPath(file)">{{ downloadPath(file) }}</q-item-label></q-item-section>
              <q-item-section v-if="downloadCanFetch(file)" side><q-btn flat round icon="download" :aria-label="`${file.filename} herunterladen`" :disable="!browser.manual" @click="download(file)" /></q-item-section>
            </q-item>
            <q-item v-if="!browser.downloads.length"><q-item-section>Noch keine Downloads.</q-item-section></q-item>
          </q-list>
        </q-card-section>
        <q-card-actions align="right">
          <q-btn v-if="downloadsDirectory" flat no-caps icon="folder_open" label="Downloads öffnen" :disable="controlCenter.saving" @click="openDownloads" />
          <q-space />
          <q-btn flat no-caps label="Schließen" v-close-popup />
          <q-btn color="primary" no-caps label="Datei verwenden" :loading="browser.transferring" :disable="!browser.pickerSelected || !browser.manual" @click="useSelectedFile" />
        </q-card-actions>
      </q-card>
    </q-dialog>
  </section>
</template>

<script setup>
import AppMenu from 'src/shared/ui/AppMenu.vue'
import AppSelect from 'src/shared/ui/AppSelect.vue'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useQuasar } from 'quasar'
import { useRoute } from 'vue-router'
import AppClosableTab from 'src/shared/ui/AppClosableTab.vue'
import AppStateMessage from 'src/shared/ui/AppStateMessage.vue'
import { useBrowserStore } from 'stores/browser'
import { useChatStore } from 'stores/chat'
import { useControlCenterStore } from 'stores/control-center'
import { useExecutionAgentStore } from 'stores/execution-agents'
import { useIntegrationStore } from 'stores/integrations'
import { browserNavigationUrl, browserStatusLabel, clipboardFits } from 'src/utils/browser'
import { apiErrorMessage } from 'src/utils/api-errors'
import { notifyError } from 'src/utils/notifications'
import { useInstanceLimitsStore } from 'stores/instance-limits'

const browser = useBrowserStore()
const $q = useQuasar()
const chat = useChatStore()
const controlCenter = useControlCenterStore()
const route = useRoute()
const agents = useExecutionAgentStore()
const integrations = useIntegrationStore()
const instanceLimits = useInstanceLimitsStore()
const navigation = computed(() => browser.displayNavigation)
const selectedAgent = computed(() => agents.byId(chat.current?.preferredExecutionAgentId))
const viewAvailable = computed(() => selectedAgent.value?.operations?.includes('integration.browser.view') !== false)
const headlessBrowser = computed(() => selectedAgent.value?.connected === true
  && selectedAgent.value?.operations?.includes('integration.browser.execute') === true && !viewAvailable.value)
const browserStateMessage = computed(() => headlessBrowser.value ? {
  title: 'Browser läuft im Hintergrund',
  description: 'Der Agent steuert diesen Browser ohne grafische Ansicht.'
} : ({
  occupied: {
    title: 'In einer anderen Ansicht geöffnet',
    description: `Der Browser wird bereits in ${browser.displayOffer?.ownerSession?.title || 'einer anderen Ansicht'} verwendet.${browser.displayOffer?.busy ? ' Eine laufende Aktion wird vor der Übernahme beendet.' : ''}`
  },
  waiting: { title: 'Browser wird vorbereitet', description: 'Der Browser wird gerade geöffnet oder geschlossen. Bitte warten.' },
  takingOver: { title: 'Browser wird hier übernommen', description: 'Eine laufende Aktion wird zuerst beendet.' },
  replaced: { title: 'In einer anderen Ansicht geöffnet', description: 'Der Browser wurde in einer anderen Ansicht übernommen. Du kannst ihn bei Bedarf erneut verbinden.' }
})[browser.displayStatus] || null)
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
const uploadInput = ref(null)
let uploadTrigger = null
const fileActions = [
  { label: 'Text kopieren', icon: 'content_copy', run: () => openClipboard('copy') },
  { label: 'Text einfügen', icon: 'content_paste', run: () => openClipboard('paste') },
  { label: 'Datei hochladen', icon: 'upload_file', run: () => openFiles() },
  { label: 'Downloads', icon: 'download', run: showDownloads }
]
const options = computed(() => integrations.browser.instances.filter((item) => item.enabled && item.executionAgentId === chat.current?.preferredExecutionAgentId).map((item) => ({ label: item.name, value: item.id })))
const available = computed(() => Boolean(chat.current?.id && integrations.adapterAvailable('browser', 'personal') && agents.byId(chat.current.preferredExecutionAgentId)?.connected && options.value.some((item) => item.value === selectedId.value)))
const pickerContext = computed(() => ({
  sessionId: chat.current?.id,
  rootIdentity: chat.current?.id,
  executionAgentId: selectedAgent.value?.id || chat.current?.preferredExecutionAgentId,
  currentDirectory: chat.current?.currentDirectory || selectedAgent.value?.homeDirectory || '/',
  homeDirectory: selectedAgent.value?.homeDirectory,
  filesystemList: selectedAgent.value?.connected === true && selectedAgent.value?.operations?.includes('filesystem.list') === true,
  filesystemImport: selectedAgent.value?.connected === true && selectedAgent.value?.operations?.includes('filesystem.import') === true
}))
const pickerDirectory = computed(() => browser.pickerDirectories[browser.pickerScope])
const pickerAtRoot = computed(() => pickerDirectory.value === (browser.pickerScope === 'session' ? '.' : '/'))
const pickerCanUpload = computed(() => browser.pickerScope === 'session' || pickerContext.value.filesystemImport)
const pickerError = computed(() => apiErrorMessage(browser.pickerError, 'Die Dateien konnten nicht geladen werden.'))
const downloadsDirectory = computed(() => pickerContext.value.filesystemList && selectedAgent.value?.homeDirectory
  ? `${selectedAgent.value.homeDirectory.replace(/\/$/u, '')}/browser/downloads`
  : null)
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
watch(() => [browser.authorizedRequest, browser.ownsDisplay, browser.integrationId], () => {
  if (browser.authorizedRequest?.sessionId === chat.current?.id) selectedId.value = browser.authorizedRequest.integrationId
  else if (browser.ownsDisplay) selectedId.value = browser.integrationId
})
watch(() => route.name === 'chat' && route.query.session === chat.current?.id ? chat.current?.id : null,
  (id) => browser.setDisplayedSession(id), { immediate: true, flush: 'sync' })
watch(() => [visible.value, confirmed.value, selectedId.value, available.value, chat.current?.id, chat.current?.preferredExecutionAgentId,
  browser.authorizedRequest, browser.requestOpening, browser.requestRefresh, controlCenter.saving, viewAvailable.value], activate, { flush: 'post' })
watch(() => [visible.value, browser.ownsDisplay, browser.browserId, browser.viewId, browser.ready, host.value], refreshView, { flush: 'post' })
watch(() => [browser.generation, browser.displayGeneration], () => {
  resetAddress()
  clipboardOpen.value = false
  filesOpen.value = false
  clipboardText.value = ''
}, { flush: 'sync' })
watch(clipboardOpen, () => { clipboardDialog += 1 }, { flush: 'sync' })
watch(filesOpen, (open) => { if (!open) browser.resetPicker() }, { flush: 'sync' })
watch(() => [visible.value, browser.viewGeneration, browser.attached, browser.busy, browser.submitting, browser.navigationPending], invalidateInput, { flush: 'sync' })
watch(() => browser.activeTab, () => { if (!addressEditing) resetAddress() }, { immediate: true })
watch(() => browser.attached, (attached) => { if (attached) resetAddress() })

onMounted(() => {
  observer = new ResizeObserver(([entry]) => {
    contentSize = entry.contentRect
    activate()
    void refreshView()
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
  if (browser.viewParent === host.value) browser.parkView()
})

function measurable() {
  const box = host.value?.getClientRects()[0]
  return visible.value && !document.hidden && box?.width > 0 && box?.height > 0
}
function activate() {
  const context = { integrationId: selectedId.value, sessionId: chat.current?.id, agentId: chat.current?.preferredExecutionAgentId }
  browser.activate(visible.value ? JSON.stringify(context) : null, context, confirmed.value && available.value && viewAvailable.value && measurable())
}
async function refreshView() {
  await nextTick()
  if (viewAvailable.value && measurable() && browser.ownsDisplay && browser.browserId) await browser.mountView(host.value)
  if (browser.viewParent === host.value) {
    browser.viewVisible = measurable() && browser.ownsDisplay
    browser.updateViewOnly()
    if (browser.viewVisible) browser.resize(contentSize.width, contentSize.height)
  }
}
function visibilityChanged() { invalidateInput(); activate(); void refreshView() }
function retryResize() { if (browser.ownsDisplay) { browser.resizeLast = null; void refreshView() } }

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
  if (available.value && confirmed.value && visible.value) browser.connect({ integrationId: selectedId.value, sessionId: chat.current.id, agentId: chat.current.preferredExecutionAgentId })
}
function takeover() {
  if (!available.value || !confirmed.value || !visible.value) return
  if (browser.displayOffer) browser.takeover()
  else browser.connect({ integrationId: selectedId.value, sessionId: chat.current.id, agentId: chat.current.preferredExecutionAgentId }, { takeover: true })
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
  browser.updateViewOnly()
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
function openFiles(downloads = false) {
  if (!browser.openPicker(pickerContext.value)) return
  filesOpen.value = true
  if (downloads) void refreshDownloads()
}
function showDownloads() { openFiles(true) }
function openPickerDirectory(path) { browser.openPickerDirectory(path) }
function activatePickerEntry(entry) {
  if (entry.kind === 'directory') openPickerDirectory(entry.path)
  else browser.pickerSelected = entry.kind === 'file' ? entry : null
}
async function transferSource(action) {
  const generation = browser.displayGeneration
  try {
    const result = await action()
    if (!result || generation !== browser.displayGeneration) return
    if (result.chosen || result.ok) {
      $q.notify({ type: 'positive', message: 'Datei in das Upload-Feld eingesetzt.' })
      filesOpen.value = false
    }
  } catch { /* Central API toast. */ }
}
function useSelectedFile() {
  const entry = browser.pickerSelected
  if (entry?.kind === 'file') void transferSource(() => browser.chooseSource({ scope: browser.pickerScope, path: entry.path }))
}
function retryChoose() { void transferSource(() => browser.transfer('choose')) }
function chooseLocalFile(event) {
  if (!uploadInput.value) return
  uploadTrigger = event.currentTarget
  uploadInput.value.click()
}
async function uploadLocalFile(event) {
  const file = event.target.files?.[0]
  event.target.value = ''
  try {
    if (!file) return
    if (file.size > instanceLimits.maxUploadBytes) {
      $q.notify({ type: 'negative', message: `„${file.name}“ ist größer als ${instanceLimits.maxUploadMib} MiB.` })
      return
    }
    await transferSource(() => browser.uploadPickerFile(file))
  } finally {
    await nextTick()
    if (filesOpen.value) uploadTrigger?.focus()
  }
}
function downloadPath(file) { return file.path || null }
function downloadState(file) { return file.status || 'ready' }
function downloadLabel(file) {
  const labels = {
    saved: 'Automatisch gespeichert', ready: `${file.size} Bytes`, pending: 'Download läuft ...',
    permission_required: 'Berechtigung zum Speichern erforderlich', failed: 'Speichern fehlgeschlagen'
  }
  return labels[downloadState(file)] || `${file.size} Bytes`
}
function downloadIcon(file) { return ({ saved: 'check_circle', permission_required: 'lock', failed: 'error' })[downloadState(file)] || 'download' }
function downloadColor(file) { return ({ saved: 'positive', permission_required: 'warning', failed: 'negative' })[downloadState(file)] }
function downloadCanFetch(file) { return Boolean(file.id) && ['ready', 'permission_required'].includes(downloadState(file)) }
async function openDownloads() {
  if (!downloadsDirectory.value) return
  try {
    await controlCenter.setFileView({
      scope: 'filesystem', selectedPath: downloadsDirectory.value, selectedKind: 'directory', viewedFilePath: null,
      executionAgentId: selectedAgent.value.id, currentDirectory: selectedAgent.value.homeDirectory
    })
    await controlCenter.openApp('computer-files')
    filesOpen.value = false
  } catch { /* Central API toast. */ }
}
async function download(file) { try { await browser.download(file) } catch { /* Central API toast. */ } }
</script>

<style scoped>
.browser-app, .browser-app__view { min-height: 0; min-width: 0; }
.browser-app__view:focus-within { outline: 2px solid var(--q-primary); outline-offset: -2px; }
.browser-file-picker { min-height: 14rem; }
</style>
