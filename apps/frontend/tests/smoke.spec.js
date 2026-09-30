import { describe, expect, it } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import routes, { adminRoutes, settingsRoutes } from '../src/router/routes'

describe('MatoLoop frontend scaffold', () => {
  it('routes legacy automation editing and exact-run links to the shared page', async () => {
    const automation = settingsRoutes.find((route) => route.name === 'settings-automations')
    const component = { render: () => null }
    const router = createRouter({ history: createMemoryHistory(), routes: [{
      ...automation, path: '/settings/automations', component,
      children: automation.children.map((route) => ({ ...route, component }))
    }] })
    expect(router.resolve('/settings/automations/new').name).toBe('settings-automation-create')
    await router.push('/settings/automations/automation-1/edit?run=run-1')
    expect(router.currentRoute.value.name).toBe('settings-automation-detail')
    expect(router.currentRoute.value.path).toBe('/settings/automations/automation-1')
    expect(router.currentRoute.value.query).toEqual({ run: 'run-1' })
  })

  it('exposes chat and the complete settings navigation', () => {
    const appRoute = routes.find((route) => Array.isArray(route.children))
    const routeNames = appRoute.children.map((route) => route.name)
    const settingsRoute = appRoute.children.find((route) => route.name === 'settings')

    expect(routeNames).toEqual(['chat', 'settings', 'admin'])
    expect(settingsRoute.children.map((route) => route.name)).toEqual([
      'settings-providers',
      'settings-agents',
      'settings-integrations',
      'settings-mcp',
      'settings-channels',
      'settings-tools',
      'settings-personal-variables',
      'settings-approvals',
      'settings-execution-agents',
      'settings-skills',
      'settings-automations',
      'settings-personalization',
      'settings-persona',
      'settings-memory',
      'settings-notifications',
      'settings-debug-access'
    ])
    expect(
      settingsRoute.children.find((route) => route.name === 'settings-integrations').children
        .map((route) => route.name)
    ).toEqual(['settings-integration-resources'])
    expect(
      settingsRoute.children.find((route) => route.name === 'settings-mcp').children
        .map((route) => ({ name: route.name, path: route.path }))
    ).toEqual([{ name: 'settings-mcp-tools', path: ':serverId/tools' }])
    expect(
      settingsRoute.children.find((route) => route.name === 'settings-agents').children
        .map((route) => route.name)
    ).toEqual(['settings-agent-create', 'settings-agent-edit'])
    expect(
      settingsRoute.children.find((route) => route.name === 'settings-automations').children
        .map(({ name, path }) => ({ name, path }))
    ).toEqual([
      { name: 'settings-automation-create', path: 'new' },
      { name: 'settings-automation-edit', path: ':automationId/edit' },
      { name: 'settings-automation-detail', path: ':automationId' }
    ])
    expect(routes.find((route) => route.name === 'markdown-print')?.path).toBe('/print/markdown')
    expect(appRoute.children.find((route) => route.name === 'admin')?.meta.requiresAdmin).toBe(true)
    expect(adminRoutes.map((route) => route.name)).toEqual(['admin-users', 'admin-providers', 'admin-instance-limits'])
    expect(settingsRoutes.find((route) => route.name === 'settings-notifications')?.meta.requiresAdmin).toBe(true)
    expect(settingsRoutes.find((route) => route.name === 'settings-debug-access')?.meta.requiresAdmin).toBe(true)
    expect(settingsRoutes.find((route) => route.name === 'settings-tools')?.meta.requiresAdmin).toBe(true)
    expect(settingsRoutes.find((route) => route.name === 'settings-channels')?.meta).toEqual({
      settingsLabel: 'Kanäle', settingsIcon: 'forum'
    })
    expect(settingsRoutes.find((route) => route.name === 'settings-mcp')?.meta).toEqual({
      settingsLabel: 'MCP-Server', settingsIcon: 'hub'
    })
    expect(settingsRoutes.find((route) => route.name === 'settings-personal-variables')?.meta).toEqual({
      settingsLabel: 'Variablen & Secrets', settingsIcon: 'key'
    })
    expect(settingsRoutes.find((route) => route.name === 'settings-approvals')?.redirect).toEqual({
      name: 'chat', query: { controlCenter: 'notifications' }
    })
    expect(settingsRoutes.find((route) => route.name === 'settings-personalization')?.meta).toEqual({
      settingsLabel: 'Personalisierung', settingsIcon: 'manage_accounts'
    })
    expect(settingsRoutes.find((route) => route.name === 'settings-persona')?.redirect).toEqual({
      name: 'settings-personalization', query: { tab: 'assistant' }
    })
    expect(settingsRoutes.find((route) => route.name === 'settings-persona')?.meta).toBeUndefined()
    expect(settingsRoutes
      .filter((route) => ['settings-providers', 'settings-integrations'].includes(route.name))
      .every((route) => route.meta.requiresAdmin === undefined)).toBe(true)
  })

  it('keeps user management and instance limits in the admin-only section', () => {
    const router = createRouter({ history: createMemoryHistory(), routes })

    expect(router.resolve('/settings/users').matched.at(-1).path).toBe('/:catchAll(.*)*')
    expect(router.resolve('/admin/users').name).toBe('admin-users')
    expect(router.resolve('/admin/users').meta.requiresAdmin).toBe(true)
    expect(router.resolve('/admin/instance-limits').meta.requiresAdmin).toBe(true)
    expect(router.resolve('/admin/providers').meta.requiresAdmin).toBe(true)
    expect(adminRoutes.find((route) => route.name === 'admin-providers').props).toEqual({ admin: true })
    expect(router.resolve('/settings').matched.at(-1).redirect).toEqual({ name: 'settings-personalization' })
  })
})
