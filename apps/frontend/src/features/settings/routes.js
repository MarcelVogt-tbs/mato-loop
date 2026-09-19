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
    path: 'agents',
    name: 'settings-agents',
    component: () => import('pages/settings/AgentProfileSettingsPage.vue'),
    meta: { settingsLabel: 'Agenten', settingsIcon: 'smart_toy' },
    children: [
      {
        path: 'new',
        name: 'settings-agent-create',
        component: () => import('pages/settings/AgentProfileEditorPage.vue')
      },
      {
        path: ':agentId',
        name: 'settings-agent-edit',
        component: () => import('pages/settings/AgentProfileEditorPage.vue')
      }
    ]
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
    path: 'mcp',
    name: 'settings-mcp',
    component: () => import('pages/settings/McpSettingsPage.vue'),
    meta: { settingsLabel: 'MCP-Server', settingsIcon: 'hub' },
    children: [
      {
        path: ':serverId/access',
        name: 'settings-mcp-access',
        component: () => import('pages/settings/McpAccessPage.vue')
      }
    ]
  },
  {
    path: 'channels',
    name: 'settings-channels',
    component: () => import('pages/settings/ChannelSettingsPage.vue'),
    meta: { settingsLabel: 'Kanäle', settingsIcon: 'forum' }
  },
  {
    path: 'tools',
    name: 'settings-tools',
    component: () => import('pages/settings/ToolSettingsPage.vue'),
    meta: { settingsLabel: 'Tools', settingsIcon: 'build' }
  },
  {
    path: 'personal-variables',
    name: 'settings-personal-variables',
    component: () => import('pages/settings/PersonalVariablesSettingsPage.vue'),
    meta: { settingsLabel: 'Variablen & Secrets', settingsIcon: 'key' }
  },
  {
    path: 'approvals',
    name: 'settings-approvals',
    redirect: { name: 'chat', query: { controlCenter: 'notifications' } }
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
    path: 'automations',
    name: 'settings-automations',
    component: () => import('pages/settings/AutomationSettingsPage.vue'),
    meta: { settingsLabel: 'Automationen', settingsIcon: 'schedule' },
    children: [
      {
        path: 'new',
        name: 'settings-automation-create',
        component: () => import('pages/settings/AutomationEditorPage.vue')
      },
      {
        path: ':automationId/edit',
        name: 'settings-automation-edit',
        redirect: (to) => ({ name: 'settings-automation-detail', params: to.params, query: to.query })
      },
      {
        path: ':automationId',
        name: 'settings-automation-detail',
        component: () => import('pages/settings/AutomationEditorPage.vue')
      }
    ]
  },
  {
    path: 'personalization',
    name: 'settings-personalization',
    component: () => import('pages/settings/PersonalizationSettingsPage.vue'),
    meta: { settingsLabel: 'Personalisierung', settingsIcon: 'manage_accounts' }
  },
  {
    path: 'persona',
    name: 'settings-persona',
    redirect: { name: 'settings-personalization', query: { tab: 'assistant' } }
  },
  {
    path: 'memory',
    name: 'settings-memory',
    component: () => import('pages/settings/MemorySettingsPage.vue'),
    meta: { settingsLabel: 'Memory', settingsIcon: 'psychology' }
  },
  {
    path: 'users',
    name: 'settings-users',
    component: () => import('pages/settings/UserSettingsPage.vue'),
    meta: { settingsLabel: 'Benutzer', settingsIcon: 'group', requiresAdmin: true }
  },
  {
    path: 'debug-access',
    name: 'settings-debug-access',
    component: () => import('pages/settings/DebugAccessSettingsPage.vue'),
    meta: { settingsLabel: 'Debug-Zugang', settingsIcon: 'bug_report', requiresAdmin: true }
  },
  {
    path: 'instance-limits',
    name: 'settings-instance-limits',
    component: () => import('pages/settings/InstanceLimitsPage.vue'),
    meta: { settingsLabel: 'Instanzlimits', settingsIcon: 'tune', requiresAdmin: true }
  }
]
