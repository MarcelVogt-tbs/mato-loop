export const settingsRoutes = [
  {
    path: 'providers',
    name: 'settings-providers',
    component: () => import('pages/settings/ProviderSettingsPage.vue'),
    meta: { settingsLabel: 'Provider', settingsIcon: 'dns' }
  },
  {
    path: 'default-models',
    name: 'settings-default-models',
    component: () => import('pages/settings/DefaultModelSettingsPage.vue'),
    meta: { settingsLabel: 'Standardmodelle', settingsIcon: 'stars' }
  },
  {
    path: 'integrations',
    name: 'settings-integrations',
    component: () => import('pages/settings/IntegrationSettingsPage.vue'),
    meta: { settingsLabel: 'Integrationen', settingsIcon: 'extension' },
    children: [
      {
        path: ':integrationId/access',
        name: 'settings-integration-access',
        component: () => import('pages/settings/IntegrationAccessPage.vue')
      }
    ]
  },
  {
    path: 'tools',
    name: 'settings-tools',
    component: () => import('pages/settings/ToolSettingsPage.vue'),
    meta: { settingsLabel: 'Tools', settingsIcon: 'build' }
  },
  {
    path: 'approvals',
    name: 'settings-approvals',
    component: () => import('pages/settings/ApprovalSettingsPage.vue'),
    meta: { settingsLabel: 'Freigaben', settingsIcon: 'pending_actions' }
  },
  {
    path: 'execution-agents',
    name: 'settings-execution-agents',
    component: () => import('pages/settings/ExecutionAgentSettingsPage.vue'),
    meta: { settingsLabel: 'Computer', settingsIcon: 'computer' }
  },
  {
    path: 'skills',
    name: 'settings-skills',
    component: () => import('pages/settings/SkillSettingsPage.vue'),
    meta: { settingsLabel: 'Skills', settingsIcon: 'school' }
  },
  {
    path: 'persona',
    name: 'settings-persona',
    component: () => import('pages/settings/AgentIdentitySettingsPage.vue'),
    meta: { settingsLabel: 'Persona', settingsIcon: 'face' }
  },
  {
    path: 'memory',
    name: 'settings-memory',
    component: () => import('pages/settings/MemorySettingsPage.vue'),
    meta: { settingsLabel: 'Memory', settingsIcon: 'psychology' }
  },
  {
    path: 'account',
    name: 'settings-account',
    component: () => import('pages/settings/AccountSettingsPage.vue'),
    meta: { settingsLabel: 'Konto', settingsIcon: 'manage_accounts' }
  },
  {
    path: 'users',
    name: 'settings-users',
    component: () => import('pages/settings/UserSettingsPage.vue'),
    meta: { settingsLabel: 'Benutzer', settingsIcon: 'group', requiresAdmin: true }
  }
]

const routes = [
  { path: '/login', name: 'login', component: () => import('pages/AuthPage.vue'), meta: { public: true } },
  {
    path: '/print/markdown',
    name: 'markdown-print',
    component: () => import('pages/MarkdownPrintPage.vue')
  },
  {
    path: '/',
    component: () => import('layouts/MainLayout.vue'),
    children: [
      { path: '', name: 'chat', component: () => import('pages/ChatPage.vue') },
      {
        path: 'settings',
        name: 'settings',
        component: () => import('pages/SettingsPage.vue'),
        redirect: { name: 'settings-providers' },
        children: settingsRoutes
      }
    ]
  },
  {
    path: '/:catchAll(.*)*',
    component: () => import('pages/NotFoundPage.vue')
  }
]

export default routes
