export const settingsRoutes = [
  {
    path: 'providers',
    name: 'settings-providers',
    component: () => import('src/features/providers/ProviderSettingsPage.vue'),
    meta: { settingsLabel: 'Provider', settingsIcon: 'dns' }
  },
  {
    path: 'agents',
    name: 'settings-agents',
    component: () => import('src/features/agent-profiles/AgentProfileSettingsPage.vue'),
    meta: { settingsLabel: 'Agenten', settingsIcon: 'smart_toy' },
    children: [
      {
        path: 'new',
        name: 'settings-agent-create',
        component: () => import('src/features/agent-profiles/AgentProfileEditorPage.vue')
      },
      {
        path: ':agentId',
        name: 'settings-agent-edit',
        component: () => import('src/features/agent-profiles/AgentProfileEditorPage.vue')
      }
    ]
  },
  {
    path: 'integrations',
    name: 'settings-integrations',
    component: () => import('src/features/integrations/IntegrationSettingsPage.vue'),
    meta: { settingsLabel: 'Integrationen', settingsIcon: 'extension' },
    children: [
      {
        path: ':integrationId/resources',
        name: 'settings-integration-resources',
        component: () => import('src/features/integrations/IntegrationAccessPage.vue')
      }
    ]
  },
  {
    path: 'mcp',
    name: 'settings-mcp',
    component: () => import('src/features/mcp/McpSettingsPage.vue'),
    meta: { settingsLabel: 'MCP-Server', settingsIcon: 'hub' },
    children: [
      {
        path: ':serverId/tools',
        name: 'settings-mcp-tools',
        component: () => import('src/features/mcp/McpAccessPage.vue')
      }
    ]
  },
  {
    path: 'channels',
    name: 'settings-channels',
    component: () => import('src/features/channels/ChannelSettingsPage.vue'),
    meta: { settingsLabel: 'Kanäle', settingsIcon: 'forum' }
  },
  {
    path: 'tools',
    name: 'settings-tools',
    component: () => import('src/features/settings/ToolSettingsPage.vue'),
    meta: { settingsLabel: 'Tools', settingsIcon: 'build', requiresAdmin: true }
  },
  {
    path: 'personal-variables',
    name: 'settings-personal-variables',
    component: () => import('src/features/personal-variables/PersonalVariablesSettingsPage.vue'),
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
    component: () => import('src/features/computers/ExecutionAgentSettingsPage.vue'),
    meta: { settingsLabel: 'Computer', settingsIcon: 'computer' }
  },
  {
    path: 'skills',
    name: 'settings-skills',
    component: () => import('src/features/skills/SkillSettingsPage.vue'),
    meta: { settingsLabel: 'Skills', settingsIcon: 'school' }
  },
  {
    path: 'automations',
    name: 'settings-automations',
    component: () => import('src/features/automations/AutomationLinkPage.vue')
  },
  {
    path: 'automations/new',
    name: 'settings-automation-create',
    component: () => import('src/features/automations/AutomationLinkPage.vue')
  },
  {
    path: 'automations/:automationId/edit',
    name: 'settings-automation-edit',
    redirect: (to) => ({ name: 'settings-automation-detail', params: to.params, query: to.query })
  },
  {
    path: 'automations/:automationId',
    name: 'settings-automation-detail',
    component: () => import('src/features/automations/AutomationLinkPage.vue')
  },
  {
    path: 'personalization',
    name: 'settings-personalization',
    component: () => import('src/features/personalization/PersonalizationSettingsPage.vue'),
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
    component: () => import('src/features/memory/MemorySettingsPage.vue'),
    meta: { settingsLabel: 'Memory', settingsIcon: 'psychology' }
  },
  {
    path: 'notifications',
    name: 'settings-notifications',
    component: () => import('src/features/notifications/NotificationSettingsPage.vue'),
    meta: { settingsLabel: 'Benachrichtigungen', settingsIcon: 'notifications_active', requiresAdmin: true }
  },
  {
    path: 'debug-access',
    name: 'settings-debug-access',
    component: () => import('src/features/settings/DebugAccessSettingsPage.vue'),
    meta: { settingsLabel: 'Debug-Zugang', settingsIcon: 'bug_report', requiresAdmin: true }
  }
]

export const adminRoutes = [
  {
    path: 'users',
    name: 'admin-users',
    component: () => import('src/features/auth/UserSettingsPage.vue'),
    meta: { settingsLabel: 'Benutzer', settingsIcon: 'group' }
  },
  {
    path: 'providers',
    name: 'admin-providers',
    component: () => import('src/features/providers/ProviderSettingsPage.vue'),
    props: { admin: true },
    meta: { settingsLabel: 'Provider', settingsIcon: 'dns' }
  },
  {
    path: 'instance-limits',
    name: 'admin-instance-limits',
    component: () => import('src/features/settings/InstanceLimitsPage.vue'),
    meta: { settingsLabel: 'Instanzlimits', settingsIcon: 'tune' }
  }
]
