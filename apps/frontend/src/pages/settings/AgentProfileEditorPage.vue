<template>
  <section :aria-busy="agents.loading">
    <q-toolbar class="settings-detail-toolbar q-px-none q-mb-md">
      <q-btn
        flat
        round
        dense
        icon="arrow_back"
        aria-label="Zurück zu den Agentenprofilen"
        :disable="agents.saving"
        :to="{ name: 'settings-agents', query: { scope } }"
      />
      <q-avatar color="primary" text-color="white" icon="smart_toy" class="q-ml-sm" />
      <q-toolbar-title>
        <div class="row items-center q-gutter-sm">
          <div class="text-h6 text-weight-bold">
            {{ isCreate ? 'Profil erstellen' : (accessProtected ? `${editingAgent?.name || 'Profil'} ansehen` : (editingAgent?.name || 'Profil bearbeiten')) }}
          </div>
          <q-badge v-if="dirty" color="warning" text-color="black" label="Ungespeichert" />
        </div>
        <div class="text-body2 app-muted">
          Arbeitsweise, Modelle und Berechtigungen festlegen.
        </div>
      </q-toolbar-title>
      <div class="settings-scope-actions row items-center col-auto">
        <SettingsScopeToggle
          :model-value="scope"
          :is-admin="auth.isAdmin"
          :disable="agents.loading || agents.saving"
          @update:model-value="switchScope"
        />
        <q-btn
          v-if="!accessProtected"
          class="col-auto"
          unelevated
          no-caps
          no-wrap
          color="primary"
          icon="save"
          label="Speichern"
          aria-label="Agentenprofil speichern"
          :loading="agents.saving"
          :disable="blocked || agents.loading || loadFailed || !dirty"
          @click="saveAgent"
        />
      </div>
    </q-toolbar>

    <q-banner v-if="blocked" rounded class="app-surface-tonal q-mb-md">
      <template #avatar><q-icon name="block" color="warning" /></template>
      Persönliche Agenten wurden für diese Instanz deaktiviert.
    </q-banner>

    <q-banner v-if="isProtected" rounded class="app-surface-tonal q-mb-md">
      <template #avatar><q-icon name="visibility" color="primary" /></template>
      {{ accessProtected ? 'Dieses Profil ist schreibgeschützt.' : 'Die Profildefinition ist geschützt. Berechtigungen und Integrationen können angepasst werden.' }}
    </q-banner>

    <q-banner v-if="loadFailed && !blocked" rounded class="bg-negative text-white q-mb-md">
      <template #avatar><q-icon name="error_outline" /></template>
      {{ notFound ? 'Das Agentenprofil wurde nicht gefunden.' : 'Das Agentenprofil konnte nicht geladen werden.' }}
      <template #action>
        <q-btn v-if="!notFound" flat no-caps color="white" label="Erneut laden" @click="load" />
        <q-btn v-else flat no-caps color="white" label="Zurück" :to="{ name: 'settings-agents', query: { scope } }" />
      </template>
    </q-banner>

    <q-form
      v-if="!loadFailed && !blocked"
      ref="formRef"
      :class="{ disabled: blocked }"
      :inert="blocked ? '' : null"
      @submit.prevent="saveAgent"
    >
      <q-tabs
        v-model="section"
        dense
        no-caps
        align="left"
        active-color="primary"
        indicator-color="primary"
      >
        <q-tab name="profile" icon="badge" label="Profil" />
        <q-tab name="models" icon="stars" label="Modelle" />
        <q-tab name="tools" icon="build" label="Tools" />
        <q-tab name="integrations" icon="extension" label="Integrationen" />
      </q-tabs>
      <q-separator class="q-mb-lg" />

      <q-tab-panels
        v-model="section"
        animated
        transition-prev="fade"
        transition-next="fade"
        class="bg-transparent"
      >
        <q-tab-panel name="profile" class="q-pa-none">
          <q-card flat class="app-surface q-mb-md">
            <q-card-section>
              <div class="text-subtitle1 text-weight-bold">Profil</div>
              <div class="text-caption app-muted">Name und Zweck dieses Agentenprofils.</div>
            </q-card-section>
            <q-separator />
            <q-card-section>
              <q-input
                v-model="form.name"
                outlined
                autofocus
                maxlength="80"
                label="Name"
                :disable="isProtected"
                :rules="[requiredRule]"
                lazy-rules
              />
              <q-input
                v-model="form.description"
                class="q-mt-md"
                outlined
                maxlength="500"
                autogrow
                type="textarea"
                label="Beschreibung"
                :disable="isProtected"
              />
              <q-select
                v-if="!isProtected"
                v-model="form.baseProfileId"
                class="q-mt-md"
                outlined
                emit-value
                map-options
                label="Basisprofil"
                :options="baseProfileOptions"
                :disable="isProtected"
                @update:model-value="applyInheritedValues"
              />
              <div v-if="!isProtected" class="text-caption app-muted q-mt-xs">
                Nicht angepasste Einstellungen werden vom Basisprofil übernommen.
              </div>
              <div class="row items-center justify-between q-mt-md">
                <div class="text-weight-medium">Antwortdarstellung</div>
                <q-btn-toggle
                  v-if="!isProtected"
                  :model-value="ownership.answerDisplay ? 'own' : 'inherit'"
                  unelevated
                  no-caps
                  role="group"
                  toggle-color="primary"
                  :options="inheritOptions"
                  aria-label="Antwortdarstellung übernehmen oder anpassen"
                  @update:model-value="setOwnership('answerDisplay', $event)"
                />
              </div>
              <q-select
                v-model="form.answerDisplay"
                class="q-mt-md"
                outlined
                emit-value
                map-options
                label="Antwortdarstellung"
                :options="answerDisplayOptions"
                :hint="answerDisplayOptions.find((option) => option.value === form.answerDisplay)?.hint"
                persistent-hint
                :disable="isProtected || !ownership.answerDisplay"
              />
              <div class="row items-center justify-between q-mt-md">
                <div class="text-weight-medium">Session-Aufgaben</div>
                <q-btn-toggle
                  v-if="!isProtected"
                  :model-value="ownership.todoEnabled ? 'own' : 'inherit'"
                  unelevated
                  no-caps
                  role="group"
                  toggle-color="primary"
                  :options="inheritOptions"
                  aria-label="Session-Aufgaben übernehmen oder anpassen"
                  @update:model-value="setOwnership('todoEnabled', $event)"
                />
              </div>
              <q-toggle
                v-model="form.todoEnabled"
                class="q-mt-md"
                label="Session-Aufgaben verwenden"
                :disable="isProtected || !ownership.todoEnabled"
              />
              <div class="text-caption app-muted">
                Erlaubt dem Agenten, eine Aufgabenliste für längere Arbeiten zu führen.
              </div>
            </q-card-section>
          </q-card>

          <q-card flat class="app-surface q-mb-md">
            <q-card-section>
              <div class="row items-center justify-between">
                <div class="text-subtitle1 text-weight-bold">Tool-Aufrufe pro Lauf</div>
                <q-btn-toggle
                  v-if="!isProtected"
                  :model-value="ownership.maxToolCalls ? 'own' : 'inherit'"
                  unelevated
                  no-caps
                  role="group"
                  toggle-color="primary"
                  :options="inheritOptions"
                  aria-label="Tool-Limit übernehmen oder anpassen"
                  @update:model-value="setOwnership('maxToolCalls', $event)"
                />
              </div>
              <q-toggle
                :model-value="form.maxToolCalls === null"
                label="Unbegrenzt"
                :disable="isProtected || !ownership.maxToolCalls"
                @update:model-value="form.maxToolCalls = $event ? null : 25"
              />
              <q-input
                v-if="form.maxToolCalls !== null"
                v-model.number="form.maxToolCalls"
                outlined
                type="number"
                min="1"
                step="1"
                label="Maximale Tool-Aufrufe"
                :rules="[toolLimitRule]"
                lazy-rules
                :disable="isProtected || !ownership.maxToolCalls"
              />
              <q-banner rounded class="app-surface-tonal q-mt-sm">
                <template #avatar><q-icon name="warning_amber" color="warning" /></template>
                Hohe oder unbegrenzte Limits können lange Läufe und hohe Modell- und Dienstkosten verursachen.
                Ohne Limit gibt es keinen automatischen Stopp durch die Anzahl der Tool-Aufrufe.
              </q-banner>
            </q-card-section>
          </q-card>

          <q-card flat class="app-surface q-mb-md">
            <q-card-section>
              <div class="row items-center justify-between">
                <div class="text-subtitle1 text-weight-bold">Persona</div>
                <q-btn-toggle
                  v-if="!isProtected"
                  :model-value="ownership.personaId ? 'own' : 'inherit'"
                  unelevated
                  no-caps
                  role="group"
                  toggle-color="primary"
                  :options="inheritOptions"
                  aria-label="Persona übernehmen oder anpassen"
                  @update:model-value="setOwnership('personaId', $event)"
                />
              </div>
              <div class="text-caption app-muted">Wiederverwendbares Verhalten und Charakter dieses Agenten.</div>
            </q-card-section>
            <q-separator />
            <q-card-section>
              <q-select
                v-model="form.personaId"
                outlined
                emit-value
                map-options
                clearable
                label="Persona"
                :options="personaOptions"
                hint="Ohne Auswahl gilt die Standard-Persona dieses Bereichs."
                :disable="isProtected || !ownership.personaId"
              />
              <q-btn
                v-if="!isProtected"
                flat
                no-caps
                color="primary"
                icon="manage_accounts"
                label="Personas verwalten"
                class="q-mt-sm"
                :to="{ name: 'settings-personalization', query: { tab: 'assistant', scope } }"
              />
            </q-card-section>
          </q-card>

          <q-card flat class="app-surface">
            <q-card-section>
              <div class="row items-center justify-between">
                <div class="text-subtitle1 text-weight-bold">Aufgaben-Prompt</div>
                <q-btn-toggle
                  v-if="!isProtected"
                  :model-value="ownership.instructions ? 'own' : 'inherit'"
                  unelevated
                  no-caps
                  role="group"
                  toggle-color="primary"
                  :options="inheritOptions"
                  aria-label="Aufgaben-Prompt übernehmen oder anpassen"
                  @update:model-value="setOwnership('instructions', $event)"
                />
              </div>
              <div class="text-caption app-muted">Konkrete Aufgaben, Vorgehen und gewünschte Ergebnisse dieses Agenten.</div>
            </q-card-section>
            <q-separator />
            <q-card-section>
              <q-input
                v-model="form.instructions"
                outlined
                type="textarea"
                autogrow
                maxlength="16000"
                label="Aufgabenanweisungen"
                :rules="[requiredRule]"
                lazy-rules
                :disable="isProtected || !ownership.instructions"
              />
              <div class="row items-center justify-between q-mt-md">
                <div class="text-weight-medium">Laufzeit-Hinweis</div>
                <q-btn-toggle
                  v-if="!isProtected"
                  :model-value="ownership.runtimeInstructions ? 'own' : 'inherit'"
                  unelevated
                  no-caps
                  role="group"
                  toggle-color="primary"
                  :options="inheritOptions"
                  aria-label="Laufzeit-Hinweis übernehmen oder anpassen"
                  @update:model-value="setOwnership('runtimeInstructions', $event)"
                />
              </div>
              <q-input
                v-model="form.runtimeInstructions"
                class="q-mt-md"
                outlined
                type="textarea"
                autogrow
                maxlength="4000"
                label="Aktuelle Arbeitsgrenzen"
                hint="Wird bei jedem Modellaufruf an die aktuelle Anfrage angehängt, auch bei Unteragenten. Leer deaktiviert den Hinweis."
                :disable="isProtected || !ownership.runtimeInstructions"
              />
            </q-card-section>
          </q-card>

          <q-card flat class="app-surface q-mt-md">
            <q-card-section>
              <div class="text-subtitle1 text-weight-bold">Technische Ausführung</div>
              <div class="text-caption app-muted">Delegation und Schreibzugriff innerhalb der Session.</div>
            </q-card-section>
            <q-separator />
            <q-card-section>
              <div class="row items-center justify-between">
                <div class="text-weight-medium">Aufgaben delegieren</div>
                <q-btn-toggle
                  v-if="!isProtected"
                  :model-value="ownership.delegationEnabled ? 'own' : 'inherit'"
                  unelevated
                  no-caps
                  role="group"
                  toggle-color="primary"
                  :options="inheritOptions"
                  aria-label="Delegation übernehmen oder anpassen"
                  @update:model-value="setOwnership('delegationEnabled', $event)"
                />
              </div>
              <q-toggle
                v-model="form.delegationEnabled"
                label="Delegation an Unteragenten erlauben"
                :disable="isProtected || !ownership.delegationEnabled"
              />
              <div class="text-caption app-muted">Erlaubt diesem Agenten, selbstständige Unteraufgaben ausführen zu lassen.</div>

              <div class="row items-center justify-between q-mt-lg">
                <div class="text-weight-medium">Schreibbare Session-Pfade</div>
                <q-btn-toggle
                  v-if="!isProtected"
                  :model-value="ownership.sessionWritePaths ? 'own' : 'inherit'"
                  unelevated
                  no-caps
                  role="group"
                  toggle-color="primary"
                  :options="inheritOptions"
                  aria-label="Session-Schreibpfade übernehmen oder anpassen"
                  @update:model-value="setOwnership('sessionWritePaths', $event)"
                />
              </div>
              <q-select
                v-model="form.sessionWriteMode"
                class="q-mt-sm"
                outlined
                emit-value
                map-options
                label="Schreibzugriff"
                :options="sessionWriteModeOptions"
                :disable="isProtected || !ownership.sessionWritePaths"
              />
              <q-input
                v-if="form.sessionWriteMode === 'paths'"
                v-model="form.sessionWritePathsText"
                class="q-mt-md"
                outlined
                type="textarea"
                autogrow
                label="Relative Session-Pfade"
                hint="Ein exakter relativer POSIX-Pfad pro Zeile, zum Beispiel plan.md oder ausgabe/bericht.md."
                persistent-hint
                :disable="isProtected || !ownership.sessionWritePaths"
                :rules="[sessionWritePathsRule]"
                lazy-rules
              />
              <div class="text-caption app-muted q-mt-xs">
                Unbeschränkt erlaubt alle Session-Pfade. „Alles verweigern“ erlaubt keinen Schreibpfad.
              </div>
            </q-card-section>
          </q-card>
        </q-tab-panel>

        <q-tab-panel name="models" class="q-pa-none">
          <q-card flat class="app-surface q-mb-md">
            <q-card-section>
              <div class="text-subtitle1 text-weight-bold">Agentmodell</div>
              <div class="text-caption app-muted">Modell und Reasoning für die eigentlichen Agentenläufe.</div>
            </q-card-section>
            <q-separator />
            <q-card-section>
              <q-select
                :model-value="modelValue('defaultModelId')"
                outlined clearable emit-value map-options
                label="Agentmodell"
                :options="modelOptions({ field: 'defaultModelId', requiresTools: true })"
                :disable="accessProtected"
                :behavior="$q.screen.lt.sm ? 'dialog' : 'menu'"
                hint="Benötigt Tool-Unterstützung. Ohne Auswahl kann dieses Profil keine Agentenläufe starten."
                persistent-hint
                @update:model-value="setModelValue('defaultModelId', $event)"
              />
              <q-select
                class="q-mt-lg"
                :model-value="modelValue('reasoningEffort')"
                outlined emit-value map-options
                label="Reasoning-Stufe"
                :options="reasoningSelectionOptions"
                :disable="accessProtected"
                :behavior="$q.screen.lt.sm ? 'dialog' : 'menu'"
                :hint="reasoningOptions.length ? 'Gilt, solange die Session keine eigene Stufe festlegt.' : 'Das gewählte Modell bietet keine konfigurierbare Reasoning-Stufe.'"
                persistent-hint
                @update:model-value="setModelValue('reasoningEffort', $event)"
              />
            </q-card-section>
          </q-card>

          <q-expansion-item
            icon="tune"
            label="Weitere Modelle"
            caption="Titel, Komprimierung, Analyse, Medien und Hintergrundarbeit"
            class="app-surface"
          >
            <template v-for="setting in additionalModelSettings" :key="setting.field">
              <q-separator />
              <q-card-section>
                <div class="q-mb-sm">
                  <div class="text-weight-medium">{{ setting.title }}</div>
                  <div class="text-caption app-muted">{{ setting.description }}</div>
                </div>
                <q-select
                  :model-value="modelValue(setting.field)"
                  outlined clearable emit-value map-options
                  :label="setting.label"
                  :options="modelOptions(setting)"
                  :disable="accessProtected"
                  :behavior="$q.screen.lt.sm ? 'dialog' : 'menu'"
                  @update:model-value="setModelValue(setting.field, $event)"
                />
                <q-select
                  v-if="setting.field === 'titleGenerationModelId'"
                  :model-value="modelValue('titleVisionFallbackEnabled')"
                  class="q-mt-sm"
                  label="Bei Bildinhalten auf das Visionmodell ausweichen"
                  outlined emit-value map-options
                  :options="titleVisionFallbackOptions"
                  :disable="accessProtected"
                  @update:model-value="setModelValue('titleVisionFallbackEnabled', $event)"
                />
              </q-card-section>
            </template>
          </q-expansion-item>
        </q-tab-panel>

        <q-tab-panel name="tools" class="q-pa-none">
          <div class="text-h6 text-weight-bold">Tool-Rechte</div>
          <div class="text-body2 app-muted q-mb-md">
            Die technische Verfügbarkeit kommt aus den Tool- und Integrationseinstellungen. Dieses Profil legt fest, ob der Agent eine verfügbare Aktion ausführen darf.
          </div>

          <q-card flat class="app-surface q-mb-md">
            <q-card-section>
              <div class="row items-center justify-between">
                <div>
                  <div class="text-subtitle1 text-weight-bold">Shell-Präfixe</div>
                  <div class="text-caption app-muted">Dauerhafte Freigaben auf Befehlspräfixe begrenzen.</div>
                </div>
                <q-btn-toggle
                  :model-value="ownership.shellPrefixes ? 'own' : 'inherit'"
                  unelevated no-caps toggle-color="primary" :options="inheritOptions"
                  :disable="accessProtected"
                  @update:model-value="setOwnership('shellPrefixes', $event)"
                />
              </div>
            </q-card-section>
            <q-separator />
            <q-card-section class="row q-col-gutter-md">
              <q-input
                v-model="form.shellPrefixesRead"
                class="col-12 col-md-6"
                outlined type="textarea" autogrow
                label="Lesende Befehle"
                hint="Ein Präfix pro Zeile, zum Beispiel git status."
                :disable="accessProtected || !ownership.shellPrefixes"
                :rules="[shellPrefixRule]"
              />
              <q-input
                v-model="form.shellPrefixesWrite"
                class="col-12 col-md-6"
                outlined type="textarea" autogrow
                label="Schreibende Befehle"
                hint="Ein Präfix pro Zeile, zum Beispiel npm install."
                :disable="accessProtected || !ownership.shellPrefixes"
                :rules="[shellPrefixRule]"
              />
            </q-card-section>
          </q-card>

          <q-banner rounded class="app-surface-tonal q-mb-md">
            <template #avatar><q-icon name="privacy_tip" color="primary" /></template>
            „Erben“ übernimmt jede einzelne Berechtigung vom Basisprofil. Ohne Profilvorgabe gelten die wirksamen Tool- und Integrationsstandards.
          </q-banner>

          <q-card
            v-for="group in permissionGroups"
            :key="group.id"
            flat
            class="app-surface q-mb-md"
          >
            <q-card-section class="row items-center q-gutter-md">
              <q-avatar color="primary" text-color="white" :icon="group.icon" />
              <div>
                <div class="text-subtitle1 text-weight-bold">{{ group.label }}</div>
                <div class="text-caption app-muted">Optionale Abweichungen von den wirksamen Tool-Standards.</div>
              </div>
            </q-card-section>
            <template v-for="action in group.actions" :key="action.capability">
              <q-separator />
              <q-card-section>
                <div class="row items-start q-col-gutter-md">
                  <div class="col-12 col-md">
                    <div class="text-weight-medium">{{ action.label }}</div>
                    <div class="text-caption app-muted">{{ action.capability }}</div>
                    <q-chip
                      v-if="action.available === false"
                      dense
                      outline
                      color="warning"
                      label="Technisch deaktiviert"
                    />
                  </div>
                  <div v-for="audience in audiences" :key="audience.value" class="col-12 col-sm-6 col-md-3">
                    <q-select
                      :model-value="globalPolicy(audience.value, action.capability)"
                      outlined
                      dense
                      emit-value
                      map-options
                      :label="audience.label"
                      :options="policyOptions(baselinePolicy(audience.value, action.capability), action.allowedPolicies)"
                      :disable="accessProtected || action.available === false"
                      :hint="`Wirksam: ${policyLabel(effectiveGlobalPolicy(audience.value, action.capability))}`"
                      :behavior="$q.screen.lt.sm ? 'dialog' : 'menu'"
                      persistent-hint
                      @update:model-value="setGlobalPolicy(audience.value, action.capability, $event)"
                    />
                  </div>
                </div>
              </q-card-section>
            </template>
          </q-card>
        </q-tab-panel>

        <q-tab-panel name="integrations" class="q-pa-none">
          <q-card flat class="app-surface q-mb-md">
            <q-card-section>
              <q-select
                v-model="integrationDefault"
                outlined
                emit-value
                map-options
                label="Integrationen standardmäßig"
                :options="[
                  { label: `Erben (${baseProfile?.integrationsEnabledByDefault ? 'Aktiv' : 'Deaktiviert'})`, value: 'inherit' },
                  { label: 'Aktiv', value: true },
                  { label: 'Deaktiviert', value: false }
                ]"
                :disable="accessProtected"
                :behavior="$q.screen.lt.sm ? 'dialog' : 'menu'"
                hint="Gilt auch für neue Integrationen. Einzelne Einstellungen haben Vorrang."
                persistent-hint
              />
            </q-card-section>
          </q-card>
          <q-card flat class="app-surface">
            <q-card-section>
              <div class="row items-center justify-between">
                <div class="text-subtitle1 text-weight-bold">Integrationsauswahl</div>
              </div>
              <div class="text-caption app-muted">
                Optional von Integrations- und Ressourcenstandards abweichen. Ressourcen werden erst beim Öffnen einer Integration geladen.
              </div>
            </q-card-section>
            <q-separator />
            <q-banner v-if="!agents.integrations.length && !agents.loading" class="app-surface-tonal q-ma-md">
              Es sind keine aktivierbaren Integrationen konfiguriert.
            </q-banner>
            <q-list separator>
              <q-expansion-item
                v-for="integration in agents.integrations"
                :key="integration.id"
                group="agent-integrations"
                :icon="integration.enabled ? 'extension' : 'extension_off'"
                :label="integration.name"
                :caption="integrationHint(integration)"
                @show="loadIntegration(integration.id)"
              >
                <q-card-section>
                  <q-select
                    :model-value="form.integrationAvailability[integration.id] ?? 'inherit'"
                    outlined
                    emit-value
                    map-options
                    :label="`Verfügbarkeit: ${integration.name}`"
                    :options="integrationAvailabilityOptions(integration.id)"
                    :hint="integrationHint(integration)"
                    :behavior="$q.screen.lt.sm ? 'dialog' : 'menu'"
                    persistent-hint
                    :disable="accessProtected || !integration.enabled || integration.available === false"
                    @update:model-value="setIntegrationAvailability(integration.id, $event)"
                  />
                  <div v-if="!integration.supportsPolicies" class="text-caption app-muted q-mt-sm">
                    Diese Integration stellt Optionen oder Projektfunktionen bereit. Deren bestehende Tool-Rechte gelten weiterhin.
                  </div>
                </q-card-section>
                <q-card-section v-if="agents.integrationMetadataFailed[integration.id]">
                  <q-banner rounded class="app-surface-tonal">
                    Die Aktionen oder Ressourcen dieser Integration konnten nicht geladen werden.
                    <template #action>
                      <q-btn flat no-caps color="primary" label="Erneut laden" @click="loadIntegration(integration.id, true)" />
                    </template>
                  </q-banner>
                </q-card-section>
                <q-card-section v-else-if="metadata(integration.id)" class="q-pa-none">
                  <template v-for="action in displayMetadata(integration.id).actions" :key="action.capability">
                    <q-separator />
                    <q-card-section>
                      <div class="row items-start q-col-gutter-md">
                        <div class="col-12 col-md">
                          <div class="text-weight-medium">{{ action.label }}</div>
                          <div class="text-caption app-muted">Vererbt den Standard der jeweiligen Modellklasse.</div>
                        </div>
                        <div v-for="audience in audiences" :key="audience.value" class="col-12 col-sm-6 col-md-3">
                          <q-select
                            :model-value="integrationPolicy(audience.value, integration.id, action.capability)"
                            outlined
                            dense
                            emit-value
                            map-options
                            :label="audience.label"
                            :options="policyOptions(inheritedIntegrationPolicy(audience.value, integration.id, action), action.allowedPolicies)"
                            :disable="accessProtected || action.available === false || !integrationAvailable(integration)"
                            :hint="`Wirksam: ${policyLabel(effectiveIntegrationPolicy(audience.value, integration.id, action.capability))}`"
                            :behavior="$q.screen.lt.sm ? 'dialog' : 'menu'"
                            persistent-hint
                            @update:model-value="setIntegrationPolicy(audience.value, integration.id, action.capability, $event)"
                          />
                        </div>
                      </div>
                    </q-card-section>
                  </template>

                  <q-expansion-item
                    v-for="resource in displayMetadata(integration.id).resources"
                    :key="resource.key"
                    dense-toggle
                    icon="folder_open"
                    :label="resource.label"
                    header-class="app-surface-tonal"
                  >
                    <template v-for="action in resourceActions(integration.id, resource)" :key="action.capability">
                      <q-separator />
                      <q-card-section>
                        <div class="row items-start q-col-gutter-md">
                          <div class="col-12 col-md">
                            <div class="text-weight-medium">{{ action.label }}</div>
                            <div class="text-caption app-muted">{{ resource.key }}</div>
                            <q-chip
                              v-if="resource.hardUnavailableCapabilities.includes(action.capability)"
                              dense
                              outline
                              color="warning"
                              label="Vom Dienst nicht erlaubt"
                            />
                          </div>
                          <div v-for="audience in audiences" :key="audience.value" class="col-12 col-sm-6 col-md-3">
                            <q-select
                              :model-value="resourcePolicy(audience.value, integration.id, resource.key, action.capability)"
                              outlined
                              dense
                              emit-value
                              map-options
                              :label="audience.label"
                              :options="policyOptions(inheritedResourcePolicy(audience.value, integration.id, resource, action.capability), action.allowedPolicies)"
                              :disable="accessProtected || action.available === false || !integrationAvailable(integration) || resource.hardUnavailableCapabilities.includes(action.capability)"
                              :hint="resourcePolicyHint(audience.value, integration.id, resource, action.capability)"
                              :behavior="$q.screen.lt.sm ? 'dialog' : 'menu'"
                              persistent-hint
                              @update:model-value="setResourcePolicy(audience.value, integration.id, resource.key, action.capability, $event)"
                            />
                          </div>
                        </div>
                      </q-card-section>
                    </template>
                  </q-expansion-item>
                </q-card-section>
                <q-inner-loading :showing="agents.integrationMetadataLoading[integration.id]" />
              </q-expansion-item>
            </q-list>
          </q-card>
        </q-tab-panel>
      </q-tab-panels>

      <div class="row justify-end q-gutter-sm q-py-md">
        <q-btn flat no-caps label="Zurück" :disable="agents.saving" :to="{ name: 'settings-agents', query: { scope } }" />
        <q-btn
          v-if="!accessProtected"
          unelevated
          no-caps
          type="submit"
          color="primary"
          icon="save"
          label="Speichern"
          :loading="agents.saving"
          :disable="agents.loading || loadFailed || !dirty"
        />
      </div>
    </q-form>

    <q-inner-loading :showing="agents.loading" />
  </section>
</template>

<script setup>
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { useQuasar } from 'quasar'
import { onBeforeRouteLeave, onBeforeRouteUpdate, useRoute, useRouter } from 'vue-router'
import SettingsScopeToggle from 'components/SettingsScopeToggle.vue'
import { useConfirm } from 'src/composables/confirm'
import { resolveSettingsScope } from 'src/features/settings/scope'
import { policyOverride, setPolicyOverride } from 'src/utils/agent-permissions'
import { modelReasoningEffortOptions } from 'src/utils/model-runtime'
import { useAgentProfileStore } from 'stores/agent-profiles'
import { useAuthStore } from 'stores/auth'
import { useProviderStore } from 'stores/providers'

defineOptions({ name: 'AgentProfileEditorPage' })

const $q = useQuasar()
const route = useRoute()
const router = useRouter()
const { confirm } = useConfirm()
const agents = useAgentProfileStore()
const auth = useAuthStore()
const providers = useProviderStore()
const formRef = ref(null)
const editingAgent = ref(null)
const loadFailed = ref(false)
const notFound = ref(false)
const savedPayload = ref('')
const section = ref('profile')

const emptyPermissions = () => ({ policies: {}, integrationPolicies: {}, resourcePolicies: {} })
const form = reactive({
  name: '',
  description: '',
  baseProfileId: null,
  personaId: null,
  instructions: '',
  runtimeInstructions: '',
  answerDisplay: 'compact',
  maxToolCalls: 25,
  todoEnabled: false,
  integrationsEnabledByDefault: true,
  integrationAvailability: {},
  publicPermissions: emptyPermissions(),
  privatePermissions: emptyPermissions(),
  defaultModelId: null,
  reasoningEffort: null,
  compactionModelId: null,
  titleGenerationModelId: null,
  titleVisionFallbackEnabled: true,
  skillCreationModelId: null,
  dreamingModelId: null,
  visionModelId: null,
  videoModelId: null,
  imageGenerationModelId: null,
  speechGenerationModelId: null,
  audioGenerationModelId: null,
  audioTranscriptionModelId: null,
  shellPrefixesRead: '',
  shellPrefixesWrite: '',
  delegationEnabled: false,
  sessionWriteMode: 'unrestricted',
  sessionWritePathsText: ''
})
const ownership = reactive({
  personaId: false,
  instructions: false,
  runtimeInstructions: false,
  answerDisplay: false,
  maxToolCalls: false,
  todoEnabled: false,
  integrationsEnabledByDefault: false,
  defaultModelId: false,
  reasoningEffort: false,
  compactionModelId: false,
  titleGenerationModelId: false,
  titleVisionFallbackEnabled: false,
  skillCreationModelId: false,
  dreamingModelId: false,
  visionModelId: false,
  videoModelId: false,
  imageGenerationModelId: false,
  speechGenerationModelId: false,
  audioGenerationModelId: false,
  audioTranscriptionModelId: false,
  shellPrefixes: false,
  delegationEnabled: false,
  sessionWritePaths: false
})
const audiences = [
  { label: 'Private Modelle', value: 'private' },
  { label: 'Öffentliche Modelle', value: 'public' }
]
const answerDisplayOptions = [
  { label: 'Ablauf', value: 'timeline', hint: 'Zeigt alle Arbeitsschritte direkt in zeitlicher Reihenfolge.' },
  { label: 'Kompakt', value: 'compact', hint: 'Zeigt die aktuelle Aktivität sofort und den bisherigen Verlauf auf Wunsch.' }
]
const inheritOptions = [
  { label: 'Erben', value: 'inherit' },
  { label: 'Eigener Wert', value: 'own' }
]
const INHERIT_MODEL_VALUE = '__inherit__'
const sessionWriteModeOptions = [
  { label: 'Unbeschränkt', value: 'unrestricted' },
  { label: 'Alles verweigern', value: 'denied' },
  { label: 'Nur angegebene Pfade', value: 'paths' }
]
const modelFields = [
  'defaultModelId', 'reasoningEffort', 'compactionModelId', 'titleGenerationModelId',
  'titleVisionFallbackEnabled', 'skillCreationModelId', 'dreamingModelId', 'visionModelId',
  'videoModelId', 'imageGenerationModelId', 'speechGenerationModelId',
  'audioGenerationModelId', 'audioTranscriptionModelId'
]
const additionalModelSettings = [
  { field: 'compactionModelId', title: 'Kontextkomprimierung', description: 'Verdichtet ältere Gesprächsinhalte.', label: 'Komprimierungsmodell' },
  { field: 'titleGenerationModelId', title: 'Titelgenerierung', description: 'Erstellt einen kurzen Sessiontitel.', label: 'Titelmodell' },
  { field: 'skillCreationModelId', title: 'Skill-Erstellung', description: 'Erstellt prüfbare Skill-Entwürfe.', label: 'Modell für Skill-Erstellung', requiresTools: true },
  { field: 'dreamingModelId', title: 'Dreaming', description: 'Verarbeitet Gespräche zu langfristigen Erinnerungen.', label: 'Dreaming-Modell', requiresTools: true },
  { field: 'visionModelId', title: 'Bildanalyse', description: 'Liest Bilder und bildbasierte Dokumentseiten.', label: 'Visionmodell', options: 'vision' },
  { field: 'videoModelId', title: 'Videoanalyse', description: 'Analysiert Videos nativ oder über Einzelbilder.', label: 'Videomodell', options: 'video' },
  { field: 'imageGenerationModelId', title: 'Bildgenerierung', description: 'Erzeugt Bilder aus Text.', label: 'Bildmodell', capability: 'image_generation' },
  { field: 'speechGenerationModelId', title: 'Sprachausgabe', description: 'Wandelt Text in Sprache um.', label: 'Sprachmodell', capability: 'speech_generation' },
  { field: 'audioGenerationModelId', title: 'Audio und Musik', description: 'Erzeugt Audio oder Musik.', label: 'Audiomodell', capability: 'audio_generation' },
  { field: 'audioTranscriptionModelId', title: 'Transkription', description: 'Wandelt Audioaufnahmen in Text um.', label: 'Transkriptionsmodell', capability: 'audio_transcription' }
]
const scope = computed(() => resolveSettingsScope(route.query.scope, auth.isAdmin))
const baseProfiles = computed(() => agents.agents.filter((agent) => (
  agent.protected && agent.scope !== 'integration'
)))
const baseProfileOptions = computed(() => baseProfiles.value.map((agent) => ({
  label: agent.name,
  value: agent.id
})))
const baseProfile = computed(() => editingAgent.value?.accessBaseline || baseProfiles.value.find((agent) => agent.id === form.baseProfileId) || null)
const selectedAgentModel = computed(() => providers.availableModels?.find((model) => model.id === form.defaultModelId)
  || providers.models.find((model) => model.id === form.defaultModelId))
const reasoningOptions = computed(() => modelReasoningEffortOptions(selectedAgentModel.value))
const reasoningSelectionOptions = computed(() => [
  { label: `Erben: ${reasoningLabel(baseProfile.value?.reasoningEffort)}`, value: INHERIT_MODEL_VALUE },
  ...reasoningOptions.value
])
const titleVisionFallbackOptions = computed(() => [
  {
    label: `Erben: ${baseProfile.value?.titleVisionFallbackEnabled === false ? 'Deaktiviert' : 'Aktiviert'}`,
    value: INHERIT_MODEL_VALUE
  },
  { label: 'Aktiviert', value: true },
  { label: 'Deaktiviert', value: false }
])
const integrationDefault = computed({
  get: () => ownership.integrationsEnabledByDefault ? form.integrationsEnabledByDefault : 'inherit',
  set(value) {
    ownership.integrationsEnabledByDefault = value !== 'inherit'
    form.integrationsEnabledByDefault = value === 'inherit'
      ? baseProfile.value?.integrationsEnabledByDefault ?? true
      : value
  }
})
const personaOptions = computed(() => [
  { label: 'Standard-Persona', value: null },
  ...agents.personas
    .filter((persona) => scope.value === 'personal' || persona.scope === 'instance')
    .map((persona) => ({
      label: `${persona.name} (${persona.scope === 'instance' ? 'Instanz' : 'Persönlich'})`,
      value: persona.id,
      disable: persona.effective === false
    }))
])
const policyLabels = { disabled: 'Deaktiviert', ask: 'Nachfragen', auto: 'Automatisch' }
const clone = (value) => JSON.parse(JSON.stringify(value))
const isCreate = computed(() => route.name === 'settings-agent-create')
const isProtected = computed(() => editingAgent.value?.protected === true)
const accessProtected = computed(() => isProtected.value && !editingAgent.value?.accessEditable)
const blocked = computed(() => scope.value === 'personal' && !agents.personalAgentsEnabled)
const dirty = computed(() => !accessProtected.value && Boolean(savedPayload.value) && JSON.stringify(requestPayload()) !== savedPayload.value)
const integrationAvailabilityOptions = (id) => [
  { label: `Erben (${(baseProfile.value?.integrationAvailability?.[id] ?? form.integrationsEnabledByDefault) ? 'Aktiv' : 'Deaktiviert'})`, value: 'inherit' },
  { label: 'Aktiv', value: true },
  { label: 'Deaktiviert', value: false }
]
const baselineActions = computed(() => Object.fromEntries(audiences.map(({ value }) => [
  value,
  Object.fromEntries((agents.groupsByTrust[value] || []).flatMap((group) => (
    (group.tools || []).flatMap((tool) => (
      (tool.actions || []).map((action) => [action.capability, {
        ...action, available: tool.available !== false && action.available !== false
      }])
    ))
  )))
])))
const permissionGroups = computed(() => {
  const seen = new Set()
  return agents.groups.map((group) => ({
    id: group.id,
    label: group.label,
    icon: group.icon,
    actions: (group.tools || []).flatMap((tool) => (tool.actions || []).flatMap((action) => {
      if (!action.configurable || seen.has(action.capability)) return []
      seen.add(action.capability)
      return [{
        ...action,
        available: tool.available !== false && action.available !== false,
        label: action.capability === 'working_directory.external'
          ? action.label
          : (tool.actions || []).filter((item) => item.configurable).length > 1
          ? `${tool.label}: ${action.label}`
          : tool.label
      }]
    }))
  })).filter((group) => group.actions.length)
})

const requiredRule = (value) => Boolean(String(value || '').trim()) || 'Pflichtfeld'
const toolLimitRule = (value) => (Number.isSafeInteger(value) && value > 0) || 'Eine positive ganze Zahl eingeben.'
const sessionWritePaths = () => form.sessionWritePathsText.split('\n').map((path) => path.trim()).filter(Boolean)
const sessionWritePathsRule = () => form.sessionWriteMode !== 'paths' || (
  sessionWritePaths().length > 0 && sessionWritePaths().every((path) => (
    !path.startsWith('/') && !path.endsWith('/') && !path.includes('\\') && !path.includes('//')
    && !path.split('/').some((part) => part === '.' || part === '..') && !/[*?[\]{}]/.test(path)
  ))
) || 'Mindestens einen normalisierten relativen POSIX-Pfad eingeben.'
const parseShellPrefixes = (value) => [...new Map(
  value.split('\n')
    .map((line) => line.trim().split(/\s+/).filter(Boolean))
    .filter((prefix) => prefix.length)
    .map((prefix) => [JSON.stringify(prefix), prefix])
).values()]
const shellPrefixRule = (value) => {
  const prefixes = parseShellPrefixes(value)
  return (prefixes.length <= 100 && prefixes.every((prefix) => (
    prefix.length <= 8
    && prefix.every((token) => token.length <= 256 && /^[\x21-\x7e]+$/.test(token))
  ))) || 'Maximal 100 Präfixe mit je 1 bis 8 sichtbaren Tokens eingeben.'
}
const permissionField = (audience) => `${audience}Permissions`
const permissions = (audience) => form[permissionField(audience)]
const policyLabel = (policy) => policyLabels[policy] || policy
const metadata = (integrationId) => agents.integrationMetadata[integrationId] || null
const displayMetadata = (integrationId) => {
  const value = metadata(integrationId)
  return value?.private || value
}

function audienceMetadata(integrationId, audience) {
  const value = metadata(integrationId)
  return value?.[audience] || value
}

function audienceAction(audience, integrationId, capability) {
  return audienceMetadata(integrationId, audience)?.actions?.find((item) => (
    item.capability === capability
  ))
}

function audienceResource(audience, integrationId, resourceKey) {
  return audienceMetadata(integrationId, audience)?.resources?.find((item) => (
    item.key === resourceKey
  ))
}

function integrationAvailable(integration) {
  return Boolean(integration?.enabled && integration.available !== false
    && (form.integrationAvailability[integration.id] ?? baseProfile.value?.integrationAvailability?.[integration.id] ?? form.integrationsEnabledByDefault))
}

function integrationHint(integration) {
  if (!integration.enabled || integration.available === false) return 'Technisch nicht verfügbar; die Profilauswahl bleibt gespeichert.'
  return integrationAvailable(integration)
    ? 'Für dieses Profil verfügbar; Tool-Rechte gelten zusätzlich.'
    : 'Für dieses Profil deaktiviert; gespeicherte Tool-Rechte bleiben erhalten.'
}

function setIntegrationAvailability(integrationId, value) {
  if (value === 'inherit') delete form.integrationAvailability[integrationId]
  else form.integrationAvailability[integrationId] = value
}

function requestPayload() {
  const overrides = {}
  for (const field of ['personaId', 'answerDisplay', 'maxToolCalls', 'todoEnabled', 'delegationEnabled']) {
    if (ownership[field]) overrides[field] = form[field]
  }
  if (ownership.instructions) overrides.instructions = form.instructions.trim()
  if (ownership.runtimeInstructions) overrides.runtimeInstructions = form.runtimeInstructions.trim()
  for (const field of modelFields) {
    if (ownership[field]) overrides[field] = form[field]
  }
  if (ownership.integrationsEnabledByDefault) overrides.integrationsEnabledByDefault = form.integrationsEnabledByDefault
  if (Object.keys(form.integrationAvailability).length) overrides.integrationAvailability = clone(form.integrationAvailability)
  for (const field of ['publicPermissions', 'privatePermissions']) {
    if (Object.values(form[field]).some((entries) => Object.keys(entries).length)) overrides[field] = clone(form[field])
  }
  if (ownership.shellPrefixes) {
    overrides.shellPrefixes = {
      read: parseShellPrefixes(form.shellPrefixesRead),
      write: parseShellPrefixes(form.shellPrefixesWrite)
    }
  }
  if (ownership.sessionWritePaths) {
    overrides.sessionWritePaths = form.sessionWriteMode === 'unrestricted'
      ? null
      : form.sessionWriteMode === 'denied' ? [] : sessionWritePaths()
  }
  return {
    name: form.name.trim(),
    description: form.description.trim(),
    baseProfileId: form.baseProfileId,
    overrides
  }
}

function assignValues(agent) {
  const writePaths = agent?.sessionWritePaths
  Object.assign(form, {
    personaId: agent?.personaId || null,
    instructions: agent?.instructions || '',
    runtimeInstructions: agent?.runtimeInstructions || '',
    answerDisplay: agent?.answerDisplay === 'timeline' ? 'timeline' : 'compact',
    maxToolCalls: agent?.maxToolCalls === null ? null : (agent?.maxToolCalls ?? 25),
    todoEnabled: agent?.todoEnabled ?? false,
    integrationsEnabledByDefault: agent?.integrationsEnabledByDefault ?? true,
    defaultModelId: agent?.defaultModelId || null,
    reasoningEffort: agent?.reasoningEffort || null,
    compactionModelId: agent?.compactionModelId || null,
    titleGenerationModelId: agent?.titleGenerationModelId || null,
    titleVisionFallbackEnabled: agent?.titleVisionFallbackEnabled !== false,
    skillCreationModelId: agent?.skillCreationModelId || null,
    dreamingModelId: agent?.dreamingModelId || null,
    visionModelId: agent?.visionModelId || null,
    videoModelId: agent?.videoModelId || null,
    imageGenerationModelId: agent?.imageGenerationModelId || null,
    speechGenerationModelId: agent?.speechGenerationModelId || null,
    audioGenerationModelId: agent?.audioGenerationModelId || null,
    audioTranscriptionModelId: agent?.audioTranscriptionModelId || null,
    shellPrefixesRead: (agent?.shellPrefixes?.read || []).map((prefix) => prefix.join(' ')).join('\n'),
    shellPrefixesWrite: (agent?.shellPrefixes?.write || []).map((prefix) => prefix.join(' ')).join('\n'),
    delegationEnabled: agent?.delegationEnabled ?? false,
    sessionWriteMode: writePaths === null || writePaths === undefined
      ? 'unrestricted'
      : writePaths.length ? 'paths' : 'denied',
    sessionWritePathsText: Array.isArray(writePaths) ? writePaths.join('\n') : ''
  })
}

function applyInheritedValues() {
  const base = baseProfile.value
  if (!base) return
  const current = Object.fromEntries(Object.keys(ownership).filter((field) => ownership[field]).map((field) => [field, clone(
    field === 'sessionWritePaths'
        ? { sessionWriteMode: form.sessionWriteMode, sessionWritePathsText: form.sessionWritePathsText }
        : field === 'shellPrefixes'
          ? { shellPrefixesRead: form.shellPrefixesRead, shellPrefixesWrite: form.shellPrefixesWrite }
        : form[field]
  )]))
  assignValues(base)
  for (const [field, value] of Object.entries(current)) {
    if (field === 'sessionWritePaths') Object.assign(form, value)
    else if (field === 'shellPrefixes') Object.assign(form, value)
    else form[field] = value
  }
}

function setOwnership(field, value) {
  ownership[field] = value === 'own'
  if (!ownership[field]) applyInheritedValues()
}

function modelValue(field) {
  return ownership[field] ? form[field] : INHERIT_MODEL_VALUE
}

function setModelValue(field, value) {
  if (value === INHERIT_MODEL_VALUE) {
    ownership[field] = false
    applyInheritedValues()
    return
  }
  ownership[field] = true
  form[field] = value
  if (field === 'defaultModelId' && ownership.reasoningEffort) {
    const supported = new Set(selectedAgentModel.value?.reasoningEfforts || [])
    if (form.reasoningEffort !== null && !supported.has(form.reasoningEffort)) {
      form.reasoningEffort = null
    }
  }
}

function reasoningLabel(value) {
  return reasoningOptions.value.find((option) => option.value === value)?.label || 'Providerstandard'
}

function modelOptions(setting) {
  const selected = form[setting.field]
  const options = setting.options === 'vision'
    ? providers.visionModelOptions
    : setting.options === 'video'
      ? providers.videoModelOptions
      : providers.selectionOptions(
          selected,
          setting.requiresTools === true,
          setting.capability || 'chat'
        )
  const inherited = baseProfile.value?.[setting.field]
  const inheritedLabel = options.find((option) => option.value === inherited)?.label
    || (inherited ? providers.selectedModelState(inherited, setting.capability || 'chat').label : 'Kein Modell')
  return [
    { label: `Erben: ${inheritedLabel}`, value: INHERIT_MODEL_VALUE },
    ...options
  ]
}

function assignForm(agent = null) {
  const baseId = agent?.protected ? null : (agent?.baseProfileId || agents.defaultAgentProfileId)
  Object.assign(ownership, Object.fromEntries(Object.keys(ownership).map((field) => [field, false])))
  const overrides = agent?.overrides || {}
  for (const field of Object.keys(ownership)) {
    ownership[field] = Object.hasOwn(overrides, field)
  }
  Object.assign(form, {
    name: agent?.protected ? agent.name : (agent?.name || ''),
    description: agent?.protected ? agent.description : (agent?.description || ''),
    integrationAvailability: clone(overrides.integrationAvailability || {}),
    publicPermissions: { ...emptyPermissions(), ...clone(overrides.publicPermissions || {}) },
    privatePermissions: { ...emptyPermissions(), ...clone(overrides.privatePermissions || {}) },
    baseProfileId: baseId
  })
  assignValues(agent || baseProfiles.value.find((profile) => profile.id === baseId))
  savedPayload.value = JSON.stringify(requestPayload())
}

function globalPolicy(audience, capability) {
  return policyOverride(permissions(audience).policies, [capability])
}

function integrationPolicy(audience, integrationId, capability) {
  return policyOverride(permissions(audience).integrationPolicies, [integrationId, capability])
}

function resourcePolicy(audience, integrationId, resourceKey, capability) {
  return policyOverride(permissions(audience).resourcePolicies, [integrationId, resourceKey, capability])
}

function baselinePolicy(audience, capability) {
  return baseProfile.value?.[permissionField(audience)]?.policies?.[capability]
    || baselineActions.value[audience]?.[capability]?.policy || 'disabled'
}

function effectiveGlobalPolicy(audience, capability) {
  if (baselineActions.value[audience]?.[capability]?.available === false) return 'disabled'
  const configured = globalPolicy(audience, capability)
  return configured === 'inherit' ? baselinePolicy(audience, capability) : configured
}

function effectiveIntegrationPolicy(audience, integrationId, capability) {
  if (!integrationAvailable(agents.integrations.find((item) => item.id === integrationId))) return 'disabled'
  const action = audienceAction(audience, integrationId, capability)
  const configured = integrationPolicy(audience, integrationId, capability)
  if (configured !== 'inherit') return configured
  const inherited = baseProfile.value?.[permissionField(audience)]?.integrationPolicies?.[integrationId]?.[capability]
  if (inherited) return inherited
  const global = globalPolicy(audience, capability)
  if (global !== 'inherit') return global
  const baseGlobal = baseProfile.value?.[permissionField(audience)]?.policies?.[capability]
  if (baseGlobal) return baseGlobal
  return action?.effectiveIntegrationPolicy
    || baselinePolicy(audience, capability)
}

function inheritedIntegrationPolicy(audience, integrationId, action) {
  const branchAction = audienceAction(audience, integrationId, action.capability) || action
  const base = baseProfile.value?.[permissionField(audience)]
  const inherited = base?.integrationPolicies?.[integrationId]?.[action.capability]
  if (inherited) return inherited
  const global = globalPolicy(audience, action.capability)
  return global === 'inherit'
    ? base?.policies?.[action.capability] || branchAction.effectiveIntegrationPolicy
      || baselinePolicy(audience, action.capability)
    : global
}

function inheritedResourcePolicy(audience, integrationId, resource, capability) {
  const action = audienceAction(audience, integrationId, capability)
  const branchResource = audienceResource(audience, integrationId, resource.key) || resource
  if (resource.hardUnavailableCapabilities.includes(capability)) return 'disabled'
  const base = baseProfile.value?.[permissionField(audience)]
  const inherited = base?.resourcePolicies?.[integrationId]?.[resource.key]?.[capability]
  if (inherited) return inherited
  const integration = integrationPolicy(audience, integrationId, capability)
  if (integration !== 'inherit') return integration
  if (base?.integrationPolicies?.[integrationId]?.[capability]) return base.integrationPolicies[integrationId][capability]
  const global = globalPolicy(audience, capability)
  if (global !== 'inherit') return global
  if (base?.policies?.[capability]) return base.policies[capability]
  return branchResource.effectivePolicies?.[capability]
    || action?.effectiveIntegrationPolicy
    || baselinePolicy(audience, capability)
}

function policyOptions(inherited, allowedPolicies = ['disabled', 'ask', 'auto']) {
  return [
    { label: `Erben: ${policyLabel(inherited)}`, value: 'inherit' },
    ...allowedPolicies.map((value) => ({ label: policyLabel(value), value }))
  ]
}

function setGlobalPolicy(audience, capability, policy) {
  setPolicyOverride(permissions(audience).policies, [capability], policy)
}

function setIntegrationPolicy(audience, integrationId, capability, policy) {
  setPolicyOverride(
    permissions(audience).integrationPolicies,
    [integrationId, capability],
    policy
  )
}

function setResourcePolicy(audience, integrationId, resourceKey, capability, policy) {
  setPolicyOverride(
    permissions(audience).resourcePolicies,
    [integrationId, resourceKey, capability],
    policy
  )
}

function resourcePolicyHint(audience, integrationId, resource, capability) {
  if (!integrationAvailable(agents.integrations.find((item) => item.id === integrationId))) return 'Wirksam: Deaktiviert'
  if (resource.hardUnavailableCapabilities.includes(capability)) return 'Wirksam: Deaktiviert'
  const configured = resourcePolicy(audience, integrationId, resource.key, capability)
  const effective = configured === 'inherit'
    ? inheritedResourcePolicy(audience, integrationId, resource, capability)
    : configured
  return `Wirksam: ${policyLabel(effective)}`
}

function resourceActions(integrationId, resource) {
  return (displayMetadata(integrationId)?.actions || []).filter((action) => (
    resource.applicableCapabilities.includes(action.capability)
  ))
}

async function loadIntegration(integrationId, force = false) {
  if (!agents.integrations.find((item) => item.id === integrationId)?.supportsPolicies) return
  if (!force && (metadata(integrationId) || agents.integrationMetadataLoading[integrationId])) return
  try {
    await agents.fetchIntegrationMetadata(integrationId)
  } catch {
    // The central Axios interceptor displays the error.
  }
}

async function load() {
  editingAgent.value = null
  savedPayload.value = ''
  loadFailed.value = false
  notFound.value = false
  agents.selectScope(scope.value)
  providers.selectScope(scope.value)
  agents.resetIntegrationMetadata()
  try {
    await Promise.all([agents.fetchAgents(scope.value), providers.fetchAll()])
    if (blocked.value) return
    if (isCreate.value) {
      editingAgent.value = null
      if (!baseProfiles.value.some((profile) => profile.id === agents.defaultAgentProfileId)) {
        loadFailed.value = true
        return
      }
      assignForm()
      return
    }
    const agent = agents.byId(String(route.params.agentId || ''))
    if (!agent || agent.scope !== scope.value) {
      notFound.value = true
      loadFailed.value = true
      return
    }
    editingAgent.value = agent
    assignForm(agent)
  } catch {
    loadFailed.value = true
  }
}

async function saveAgent() {
  if (accessProtected.value || agents.loading || agents.saving || loadFailed.value || !dirty.value) return
  const invalidLimit = ownership.maxToolCalls && form.maxToolCalls !== null && toolLimitRule(form.maxToolCalls) !== true
  const invalidPaths = ownership.sessionWritePaths && sessionWritePathsRule() !== true
  if (!form.name.trim() || (ownership.instructions && !form.instructions.trim()) || invalidLimit || invalidPaths) {
    section.value = 'profile'
    await nextTick()
  }
  if (!await formRef.value?.validate()) return
  if (invalidLimit || invalidPaths) return
  const creating = isCreate.value
  try {
    const saved = editingAgent.value
      ? await agents.updateAgent(editingAgent.value.id, requestPayload())
      : await agents.createAgent(requestPayload())
    editingAgent.value = saved
    assignForm(saved)
    if (creating) {
      await router.replace({
        name: 'settings-agent-edit',
        params: { agentId: saved.id },
        query: { scope: scope.value }
      })
    }
    $q.notify({ type: 'positive', message: creating ? 'Agent erstellt' : 'Agent gespeichert' })
  } catch {
    // The central Axios interceptor displays the error.
  }
}

async function switchScope(value) {
  const failure = await router.push({ name: 'settings-agents', query: { scope: value } })
  if (failure) return
  agents.selectScope(value)
  await agents.fetchAgents(value).catch(() => {})
}

async function confirmDiscardedChanges() {
  if (agents.saving) return false
  if (!dirty.value) return true
  return confirm(
    'Sollen die ungespeicherten Änderungen verworfen werden?',
    'Änderungen verwerfen?',
    { okLabel: 'Verwerfen' }
  )
}

onMounted(load)
watch(
  () => [route.name, route.params.agentId, route.query.scope],
  () => void load()
)
onBeforeRouteUpdate(confirmDiscardedChanges)
onBeforeRouteLeave(confirmDiscardedChanges)
</script>
