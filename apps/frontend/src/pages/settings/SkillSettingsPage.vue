<template>
  <section>
    <header class="settings-section-header">
      <div>
        <h2 class="text-h6 text-weight-bold q-my-none">Skills</h2>
        <p class="text-body2 app-muted q-mb-none q-mt-xs">
          Wiederverwendbare Anweisungen für spezialisierte Aufgaben verwalten.
        </p>
      </div>
      <div class="settings-scope-actions row items-center col-auto">
        <SettingsScopeToggle
          :model-value="skills.scope"
          :is-admin="auth.isAdmin"
          :disable="skills.loading || skills.saving"
          @update:model-value="changeScope"
        />
        <q-btn
          unelevated
          no-caps
          no-wrap
          color="primary"
          icon="add"
          label="Skill erstellen"
          :disable="skills.loading"
          @click="openCreateDialog"
        />
      </div>
    </header>

    <q-banner v-if="loadFailed" rounded class="bg-negative text-white q-mb-md">
      <template #avatar><q-icon name="error_outline" /></template>
      Die Skills konnten nicht geladen werden.
      <template #action>
        <q-btn flat no-caps color="white" label="Erneut laden" @click="load" />
      </template>
    </q-banner>

    <q-card flat class="app-surface relative-position">
      <q-card-section v-if="skills.loading && !loaded" class="text-center q-pa-xl">
        <q-spinner color="primary" size="36px" />
        <div class="app-muted q-mt-sm">Skills werden geladen ...</div>
      </q-card-section>
      <q-card-section v-else-if="loaded && !skills.scopedItems.length" class="q-pa-xl">
        <AppStateMessage
          icon="school"
          title="Noch keine Skills"
          :description="skills.scope === 'instance'
            ? 'Erstelle einen Skill, der allen Usern zur Verfügung steht.'
            : 'Erstelle einen persönlichen Skill mit wiederverwendbaren Anweisungen.'"
        />
      </q-card-section>

      <q-list v-else separator>
        <q-item v-for="skill in skills.scopedItems" :key="skill.id" class="q-py-md">
          <q-item-section avatar top>
            <q-avatar rounded color="primary" text-color="white" icon="school" />
          </q-item-section>
          <q-item-section>
            <q-item-label class="text-subtitle1 text-weight-medium">
              {{ skill.name }}
              <q-badge v-if="skill.isDefault" outline color="primary" label="Standard" class="q-ml-xs" />
            </q-item-label>
            <q-item-label caption class="text-body2 q-mt-xs">{{ skill.description }}</q-item-label>
          </q-item-section>
          <q-item-section side top>
            <div class="row no-wrap q-gutter-xs">
              <q-toggle
                :model-value="skill.enabled"
                :aria-label="`${skill.name} ${skill.enabled ? 'deaktivieren' : 'aktivieren'}`"
                :disable="skills.saving"
                @update:model-value="setEnabled(skill, $event)"
              />
              <q-btn flat round dense icon="edit" :aria-label="`${skill.name} bearbeiten`" @click="openEditDialog(skill)">
                <q-tooltip>Bearbeiten</q-tooltip>
              </q-btn>
              <q-btn v-if="skill.isDefault && skill.modified" flat round dense icon="restart_alt" :aria-label="`${skill.name} auf Standard zurücksetzen`" @click="resetSkill(skill)">
                <q-tooltip>Auf Standard zurücksetzen</q-tooltip>
              </q-btn>
              <q-btn v-if="!skill.isDefault" flat round dense color="negative" icon="delete_outline" :aria-label="`${skill.name} löschen`" @click="removeSkill(skill)">
                <q-tooltip>Löschen</q-tooltip>
              </q-btn>
            </div>
          </q-item-section>
        </q-item>
      </q-list>

      <q-inner-loading :showing="skills.loading" />
    </q-card>

    <q-dialog v-model="dialogOpen" :maximized="$q.screen.lt.sm" :persistent="saving">
      <q-card class="skill-dialog app-surface column no-wrap">
        <q-form ref="formRef" class="column no-wrap full-height" @submit.prevent="saveSkill">
          <q-card-section class="row items-start no-wrap q-gutter-md">
            <q-avatar color="primary" text-color="white" :icon="editingSkill ? 'edit' : 'auto_awesome'" />
            <div class="col">
              <div class="text-h6">{{ editingSkill ? 'Skill bearbeiten' : 'Skill erstellen' }}</div>
              <div class="text-body2 app-muted">Name, Einsatzbeschreibung und Arbeitsanweisungen festlegen.</div>
            </div>
            <q-btn flat round dense icon="close" aria-label="Dialog schließen" :disable="saving" v-close-popup />
          </q-card-section>
          <q-separator />
          <q-card-section class="col scroll q-gutter-md">
            <q-input
              v-model="form.name"
              outlined
              autofocus
              label="Name"
              maxlength="64"
              counter
              lazy-rules
              :rules="[requiredRule]"
            />
            <q-input
              v-model="form.description"
              outlined
              autogrow
              type="textarea"
              label="Einsatzbeschreibung"
              maxlength="240"
              counter
              lazy-rules
              :rules="[requiredRule]"
              hint="Bestimmt die automatische Auswahl: Was macht der Skill und wann soll er verwendet werden?"
            />
            <q-input
              v-model="form.instructions"
              outlined
              type="textarea"
              rows="12"
              label="Anweisungen"
              maxlength="8000"
              counter
              lazy-rules
              :rules="[requiredRule]"
              hint="Beschreibe konkret, wie der Skill arbeiten soll."
            />
          </q-card-section>
          <q-separator />
          <q-card-actions align="right" class="q-pa-md">
            <q-btn flat no-caps label="Abbrechen" :disable="saving" v-close-popup />
            <q-btn unelevated no-caps color="primary" label="Speichern" type="submit" :loading="saving" />
          </q-card-actions>
        </q-form>
      </q-card>
    </q-dialog>
  </section>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { useQuasar } from 'quasar'
import { useRoute, useRouter } from 'vue-router'
import AppStateMessage from 'components/AppStateMessage.vue'
import SettingsScopeToggle from 'components/SettingsScopeToggle.vue'
import { useConfirm } from 'src/composables/confirm'
import { resolveSettingsScope } from 'src/features/settings/scope'
import { useSkillStore } from 'stores/skills'
import { useAuthStore } from 'stores/auth'

defineOptions({ name: 'SkillSettingsPage' })

const $q = useQuasar()
const route = useRoute()
const router = useRouter()
const { confirm } = useConfirm()
const skills = useSkillStore()
const auth = useAuthStore()
const loaded = ref(false)
const loadFailed = ref(false)
const dialogOpen = ref(false)
const formRef = ref(null)
const editingSkill = ref(null)
const saving = ref(false)
const form = reactive({ name: '', description: '', instructions: '' })

const requiredRule = (value) => Boolean(String(value || '').trim()) || 'Pflichtfeld'

async function load() {
  loadFailed.value = false
  try {
    await skills.fetchSkills(skills.scope)
    loaded.value = true
  } catch {
    loadFailed.value = true
  }
}

async function changeScope(scope) {
  dialogOpen.value = false
  loaded.value = false
  skills.selectScope(scope)
  await router.replace({ query: { ...route.query, scope: skills.scope } })
  await load()
}

function openCreateDialog() {
  editingSkill.value = null
  Object.assign(form, { name: '', description: '', instructions: '' })
  dialogOpen.value = true
}

function openEditDialog(skill) {
  editingSkill.value = skill
  Object.assign(form, {
    name: skill.name,
    description: skill.description,
    instructions: skill.instructions
  })
  dialogOpen.value = true
}

async function saveSkill() {
  if (!await formRef.value?.validate()) return
  saving.value = true
  try {
    const payload = {
      name: form.name.trim(),
      description: form.description.trim(),
      instructions: form.instructions.trim()
    }
    if (editingSkill.value) {
      await skills.updateSkill(editingSkill.value.id, payload)
      $q.notify({ type: 'positive', message: 'Skill gespeichert' })
    } else {
      await skills.createSkill(payload)
      $q.notify({ type: 'positive', message: 'Skill erstellt' })
    }
    dialogOpen.value = false
  } catch {
    // The central Axios interceptor displays the error; keep the dialog open.
  } finally {
    saving.value = false
  }
}

async function removeSkill(skill) {
  const accepted = await confirm(
    `Soll der Skill „${skill.name}“ dauerhaft gelöscht werden?`,
    'Skill löschen',
    { okLabel: 'Löschen', okColor: 'negative' }
  )
  if (!accepted) return
  try {
    await skills.deleteSkill(skill.id)
    $q.notify({ type: 'positive', message: 'Skill gelöscht' })
  } catch {
    // The central Axios interceptor displays the error.
  }
}

async function setEnabled(skill, enabled) {
  try {
    await skills.setEnabled(skill.id, enabled)
    $q.notify({ type: 'positive', message: enabled ? 'Skill aktiviert' : 'Skill deaktiviert' })
  } catch {
    // The central Axios interceptor displays the error.
  }
}

async function resetSkill(skill) {
  const accepted = await confirm(
    `Soll der Skill „${skill.name}“ auf den ausgelieferten Standard zurückgesetzt werden?`,
    'Skill zurücksetzen',
    { okLabel: 'Zurücksetzen' }
  )
  if (!accepted) return
  try {
    await skills.resetSkill(skill.id)
    $q.notify({ type: 'positive', message: 'Skill auf Standard zurückgesetzt' })
  } catch {
    // The central Axios interceptor displays the error.
  }
}

onMounted(() => {
  skills.selectScope(resolveSettingsScope(route.query.scope, auth.isAdmin))
  void load()
})
</script>

<style scoped>
.skill-dialog {
  width: min(720px, calc(100vw - 32px));
  max-height: calc(100dvh - 32px);
}

@media (max-width: 599px) {
  .skill-dialog {
    width: 100%;
    max-height: 100dvh;
  }
}
</style>
