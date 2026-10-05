<template>
  <AppPage
    class="settings-page column no-wrap"
    :title="admin ? 'Administration' : 'Einstellungen'"
    :subtitle="admin ? 'Zentrale Verwaltung der MatoLoop-Instanz.' : 'Personalisierung, persönliche Provider, Computer, Integrationen, Kanäle und Funktionen verwalten.'"
  >

    <q-tabs
      no-caps
      align="left"
      active-color="primary"
      indicator-color="primary"
      class="settings-category-tabs app-surface q-mb-md"
      mobile-arrows
      outside-arrows
    >
      <q-route-tab
        v-for="item in navigation"
        :key="item.name"
        :icon="item.meta.settingsIcon"
        :label="item.meta.settingsLabel"
        :to="{ name: item.name }"
      />
    </q-tabs>

    <div class="settings-layout row no-wrap items-stretch col">
      <nav class="settings-category-nav q-py-xs q-pr-md" :aria-label="admin ? 'Administrationsbereiche' : 'Einstellungskategorien'">
        <q-list class="settings-category-list">
          <q-item
            v-for="item in navigation"
            :key="item.name"
            clickable
            v-ripple
            class="q-mb-xs"
            :to="{ name: item.name }"
            active-class="app-nav-item--active"
          >
            <q-item-section avatar><q-icon :name="item.meta.settingsIcon" /></q-item-section>
            <q-item-section>{{ item.meta.settingsLabel }}</q-item-section>
          </q-item>
        </q-list>
      </nav>

      <q-separator vertical class="settings-category-separator q-mr-lg" />

      <main class="settings-category-content col q-pr-sm">
        <router-view />
      </main>
    </div>
  </AppPage>
</template>

<script setup>
import { computed } from 'vue'
import AppPage from 'src/shared/ui/AppPage.vue'
import { adminRoutes, settingsRoutes } from 'src/features/settings/routes'
import { useAuthStore } from 'stores/auth'
import { useProviderStore } from 'stores/providers'

defineOptions({ name: 'SettingsPage' })

const { admin } = defineProps({ admin: { type: Boolean, default: false } })
const auth = useAuthStore()
const providers = useProviderStore()
const navigation = computed(() => (admin ? adminRoutes : settingsRoutes).filter((item) => (
  item.meta?.settingsLabel && (!item.meta.requiresAdmin || auth.isAdmin)
  && (item.name !== 'settings-providers' || auth.isAdmin || (providers.personalPolicyLoaded && providers.personalProvidersEnabled))
)))
</script>
