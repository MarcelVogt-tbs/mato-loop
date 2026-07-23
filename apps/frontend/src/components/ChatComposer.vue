<template>
  <div class="chat-composer q-pa-md">
    <q-card flat class="app-surface chat-composer__card">
      <q-form @submit.prevent="submit">
        <q-input
          ref="input"
          :model-value="draft"
          borderless
          autogrow
          type="textarea"
          rows="1"
          class="chat-composer__input"
          placeholder="Eine Nachricht an MatoLoop senden"
          :disable="interactionDisabled"
          @update:model-value="$emit('update:draft', $event)"
          @keydown.enter.exact.prevent="submit"
        />
        <div class="chat-composer__footer row items-center q-gutter-xs">
          <q-select
            :model-value="session?.workspaceAccess"
            :options="agentModes"
            emit-value
            map-options
            dense
            borderless
            options-dense
            label="Agent-Modus"
            class="composer-agent-mode"
            popup-content-class="app-menu-popup"
            :disable="interactionDisabled"
            @update:model-value="update({ workspace_access: $event })"
          >
            <template #prepend><q-icon :name="agentModeIcon" size="18px" /></template>
            <template #option="scope">
              <q-item v-bind="scope.itemProps">
                <q-item-section avatar><q-icon :name="scope.opt.icon" /></q-item-section>
                <q-item-section>
                  <q-item-label>{{ scope.opt.label }}</q-item-label>
                  <q-item-label caption>{{ scope.opt.description }}</q-item-label>
                </q-item-section>
              </q-item>
            </template>
            <q-tooltip v-if="!session?.workingDirectory">
              Ohne Workdir erfordert jeder Dateizugriff eine Freigabe.
            </q-tooltip>
          </q-select>
          <q-select
            :model-value="effectiveModelId"
            :options="modelSelectionOptions"
            emit-value
            map-options
            dense
            borderless
            options-dense
            label="LLM"
            class="composer-model"
            popup-content-class="app-menu-popup"
            :disable="interactionDisabled"
            @update:model-value="update({ model_id: $event })"
          >
            <template #prepend><q-icon :name="selectedModelTrustIcon" size="18px" /></template>
            <template #option="scope">
              <q-item v-bind="scope.itemProps">
                <q-item-section avatar><q-icon :name="scope.opt.trustIcon" /></q-item-section>
                <q-item-section><q-item-label>{{ scope.opt.label }}</q-item-label></q-item-section>
              </q-item>
            </template>
          </q-select>
          <span v-if="modelMessage" class="composer-model-warning text-caption">
            <q-icon name="warning_amber" size="16px" />
            {{ modelMessage }}
            <q-tooltip>{{ modelMessage }}</q-tooltip>
          </span>
          <q-btn
            flat
            dense
            no-caps
            type="button"
            icon="tune"
            label="Sessioneinstellungen"
            class="composer-control composer-session-settings"
            aria-label="Sessioneinstellungen öffnen"
            :disable="interactionDisabled"
          >
            <q-icon
              v-if="sessionSettingsWarning"
              name="warning_amber"
              color="warning"
              size="17px"
              class="q-ml-xs"
            />
            <q-tooltip v-if="sessionSettingsWarning">{{ sessionSettingsWarning }}</q-tooltip>
            <q-popup-proxy
              v-model="settingsOpen"
              :breakpoint="600"
              :maximized="$q.screen.lt.sm"
              anchor="top right"
              self="bottom right"
              :offset="[0, 8]"
            >
              <q-card class="chat-session-settings-panel column no-wrap">
                <q-toolbar class="chat-session-settings-panel__toolbar">
                  <q-icon name="tune" size="20px" class="q-mr-sm" />
                  <q-toolbar-title class="text-subtitle2 text-weight-medium">
                    Sessioneinstellungen
                  </q-toolbar-title>
                  <q-btn
                    flat
                    round
                    :dense="$q.screen.gt.xs"
                    icon="close"
                    aria-label="Sessioneinstellungen schließen"
                    v-close-popup
                  />
                </q-toolbar>

                <q-separator />

                <q-card-section class="chat-session-settings-panel__content scroll">
                  <div class="chat-session-settings-panel__fields column">
                    <q-select
                      :model-value="session?.memoryMode"
                      :options="memoryModeOptions"
                      emit-value
                      map-options
                      outlined
                      :dense="$q.screen.gt.xs"
                      options-dense
                      label="Memory"
                      :hint="memoryMessage"
                      persistent-hint
                      popup-content-class="app-menu-popup"
                      :disable="interactionDisabled"
                      @update:model-value="update({ memory_mode: $event })"
                    >
                      <template #prepend><q-icon :name="memoryModeIcon" /></template>
                      <template #option="scope">
                        <q-item v-bind="scope.itemProps">
                          <q-item-section avatar><q-icon :name="scope.opt.icon" /></q-item-section>
                          <q-item-section>
                            <q-item-label>{{ scope.opt.label }}</q-item-label>
                            <q-item-label caption>{{ scope.opt.description }}</q-item-label>
                          </q-item-section>
                        </q-item>
                      </template>
                    </q-select>

                    <q-select
                      :model-value="session?.webSearchPolicy"
                      :options="webSearchOptions"
                      emit-value
                      map-options
                      outlined
                      :dense="$q.screen.gt.xs"
                      options-dense
                      label="Websuche"
                      :hint="webSearchMessage"
                      persistent-hint
                      popup-content-class="app-menu-popup"
                      :disable="interactionDisabled"
                      @update:model-value="update({ web_search_policy: $event })"
                    >
                      <template #prepend><q-icon :name="webSearchIcon" /></template>
                      <template #option="scope">
                        <q-item v-bind="scope.itemProps">
                          <q-item-section avatar><q-icon :name="scope.opt.icon" /></q-item-section>
                          <q-item-section>
                            <q-item-label>{{ scope.opt.label }}</q-item-label>
                            <q-item-label caption>{{ scope.opt.description }}</q-item-label>
                          </q-item-section>
                        </q-item>
                      </template>
                    </q-select>
                  </div>
                </q-card-section>
              </q-card>
            </q-popup-proxy>
          </q-btn>
          <q-chip v-if="pendingCount" dense square color="warning" text-color="dark" icon="pending_actions">
            {{ pendingCount }} offen
          </q-chip>
          <q-space />
          <q-btn
            v-if="running"
            round
            unelevated
            color="negative"
            icon="stop"
            aria-label="Agent-Anfrage stoppen"
            type="button"
            :loading="stopping"
            :disable="stopping"
            @click="$emit('stop')"
          />
          <q-btn
            v-else
            round
            unelevated
            color="primary"
            icon="arrow_upward"
            aria-label="Nachricht senden"
            type="submit"
            :disable="sendDisabled || !draft.trim()"
          />
        </div>

      </q-form>
    </q-card>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useIntegrationStore } from 'stores/integrations'
import { useProviderStore } from 'stores/providers'
import { apiErrorMessage } from 'src/utils/api-errors'

defineOptions({ name: 'ChatComposer' })

const props = defineProps({
  session: { type: Object, default: null },
  draft: { type: String, default: '' },
  running: Boolean,
  stopping: Boolean,
  pendingCount: { type: Number, default: 0 }
})
const emit = defineEmits(['send', 'stop', 'update', 'update:draft'])
const integrations = useIntegrationStore()
const providers = useProviderStore()
const input = ref(null)
const settingsOpen = ref(false)
const agentModes = [
  {
    label: 'Lesen',
    value: 'read-only',
    icon: 'visibility',
    description: 'Nur lesende Dateitools anbieten'
  },
  {
    label: 'Schreiben',
    value: 'read-write',
    icon: 'edit',
    description: 'Lesende und schreibende Dateitools anbieten'
  }
]
const effectiveModelId = computed(() => props.session?.modelId || providers.defaultModelId)
const selectedModel = computed(() => providers.selectedModelState(effectiveModelId.value))
const modelSelectionOptions = computed(() => providers.selectionOptions(effectiveModelId.value))
const modelMessage = computed(() => selectedModel.value.available ? '' : selectedModel.value.message)
const selectedModelConfig = computed(() => providers.models.find((model) => (
  model.id === effectiveModelId.value
)))
const selectedModelTrustIcon = computed(() => selectedModelConfig.value?.effectiveTrustLevel === 'private' ? 'lock' : 'public')
const modelSupportsTools = computed(() => selectedModelConfig.value?.supportsTools !== false)
const memoryModeOptions = [
  { label: 'Voll', value: 'full', icon: 'memory', description: 'Lesen, Schreiben und Dreaming' },
  { label: 'Nur lesen', value: 'read_only', icon: 'visibility', description: 'Lesen ohne Schreiben und Dreaming' },
  { label: 'Deaktiviert', value: 'disabled', icon: 'memory_alt', description: 'Kein Memory-Zugriff und kein Dreaming' }
]
const effectiveMemoryMode = computed(() => props.session?.effectiveMemoryMode || props.session?.memoryMode || 'disabled')
const memoryMessage = computed(() => {
  if (props.session?.memoryUnavailableReason) {
    return apiErrorMessage({ error: { code: props.session.memoryUnavailableReason } })
  }
  if (!props.session?.memoryToolsAvailable && effectiveMemoryMode.value !== 'disabled') {
    return 'Automatischer Memory-Kontext ist aktiv; das ausgewählte Modell unterstützt keine Memory-Tools.'
  }
  return {
    full: 'Memory lesen und schreiben; Dreaming ist für diese Session erlaubt.',
    read_only: 'Memory nur lesen; Schreiben und Dreaming sind deaktiviert.',
    disabled: 'Memory und Dreaming sind für diese Session deaktiviert.'
  }[effectiveMemoryMode.value]
})
const memoryModeIcon = computed(() => ({ full: 'memory', read_only: 'visibility', disabled: 'memory_alt' })[props.session?.memoryMode] || 'memory')
const interactionDisabled = computed(() => !props.session || props.running || props.pendingCount > 0)
const requiredSearchUnavailable = computed(() => (
  props.session?.webSearchPolicy === 'required'
  && (!searchAvailable.value || !modelSupportsTools.value)
))
const sendDisabled = computed(() => (
  interactionDisabled.value || !selectedModel.value.available || requiredSearchUnavailable.value
))
const agentModeIcon = computed(() => props.session?.workspaceAccess === 'read-write' ? 'edit' : 'visibility')
const searchAvailable = computed(() => integrations.search.effectiveEnabled)
const webSearchOptions = computed(() => [
  {
    label: searchAvailable.value && modelSupportsTools.value
      ? 'Erzwingen'
      : 'Erzwingen (nicht verfügbar)',
    value: 'required',
    icon: 'travel_explore',
    disable: !searchAvailable.value || !modelSupportsTools.value,
    description: 'Vor jeder Antwort erfolgreich im Web suchen'
  },
  {
    label: searchAvailable.value && modelSupportsTools.value
      ? 'Verfügbar'
      : 'Nicht verfügbar',
    value: 'available',
    icon: 'public',
    description: 'Das Modell entscheidet über die Websuche'
  },
  {
    label: 'Deaktiviert',
    value: 'disabled',
    icon: 'public_off',
    description: 'Kein Websuch-Tool anbieten'
  }
])
const webSearchIcon = computed(() => ({
  required: 'travel_explore',
  available: 'public',
  disabled: 'public_off'
})[props.session?.webSearchPolicy] || 'public')
const webSearchTooltip = computed(() => {
  if (!modelSupportsTools.value) return 'Das ausgewählte Modell unterstützt keine Tools.'
  if (!searchAvailable.value) {
    return integrations.search.unavailableReason === 'integration.disabled'
      ? 'Websuche ist deaktiviert'
      : 'Websuche ist nicht eingerichtet'
  }
  return ''
})
const webSearchMessage = computed(() => (
  webSearchTooltip.value
  || webSearchOptions.value.find((option) => option.value === props.session?.webSearchPolicy)?.description
  || 'Websuche für diese Session konfigurieren.'
))
const sessionSettingsWarning = computed(() => {
  const warnings = []
  if (
    props.session?.memoryMode !== 'disabled'
    && (
      props.session?.memoryUnavailableReason
      || (!props.session?.memoryToolsAvailable && effectiveMemoryMode.value !== 'disabled')
    )
  ) {
    warnings.push(memoryMessage.value)
  }
  if (requiredSearchUnavailable.value) {
    warnings.push(webSearchTooltip.value || 'Die erforderliche Websuche ist nicht verfügbar.')
  }
  return warnings.join(' ')
})

function update(patch) {
  if (props.session) emit('update', patch)
}

function submit() {
  const content = props.draft.trim()
  if (!content || sendDisabled.value) return
  emit('send', content)
}

defineExpose({
  focus: () => input.value?.focus()
})
</script>
