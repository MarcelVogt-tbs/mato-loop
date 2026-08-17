<template>
  <q-page class="app-page settings-page column no-wrap">
    <AppPageHeader title="Einstellungen" subtitle="Persona, Provider, Computer, Integrationen und Funktionen zentral verwalten." />

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
      <nav class="settings-category-nav q-py-xs q-pr-md" aria-label="Einstellungskategorien">
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
  </q-page>
</template>

<script setup>
import { computed } from 'vue'
import AppPageHeader from 'components/AppPageHeader.vue'
import { settingsRoutes } from 'src/router/routes'
import { useAuthStore } from 'stores/auth'

defineOptions({ name: 'SettingsPage' })

const auth = useAuthStore()
const navigation = computed(() => settingsRoutes.filter((item) => (
  item.meta?.settingsLabel && (!item.meta.requiresAdmin || auth.isAdmin)
)))
</script>
