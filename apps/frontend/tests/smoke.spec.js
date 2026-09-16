import { describe, expect, it } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import routes, { settingsRoutes } from '../src/router/routes'

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

    expect(routeNames).toEqual(['chat', 'settings'])
    expect(settingsRoute.children.map((route) => route.name)).toEqual([
      'settings-providers',
      'settings-default-models',
      'settings-agents',
      'settings-integrations',
      'settings-mcp',
      'settings-channels',
      'settings-tools',
      'settings-approvals',
      'settings-execution-agents',
      'settings-skills',
      'settings-automations',
      'settings-personalization',
      'settings-persona',
      'settings-memory',
      'settings-users',
      'settings-instance-limits'
    ])
    expect(
      settingsRoute.children.find((route) => route.name === 'settings-integrations').children
        .map((route) => route.name)
    ).toEqual(['settings-integration-access'])
    expect(
      settingsRoute.children.find((route) => route.name === 'settings-mcp').children
        .map((route) => ({ name: route.name, path: route.path }))
    ).toEqual([{ name: 'settings-mcp-access', path: ':serverId/access' }])
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
    expect(settingsRoutes.find((route) => route.name === 'settings-users')?.meta.requiresAdmin).toBe(true)
    expect(settingsRoutes.find((route) => route.name === 'settings-instance-limits')?.meta.requiresAdmin).toBe(true)
    expect(settingsRoutes.find((route) => route.name === 'settings-channels')?.meta).toEqual({
      settingsLabel: 'Kanäle', settingsIcon: 'forum'
    })
    expect(settingsRoutes.find((route) => route.name === 'settings-mcp')?.meta).toEqual({
      settingsLabel: 'MCP-Server', settingsIcon: 'hub'
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
      .filter((route) => ['settings-providers', 'settings-default-models', 'settings-integrations', 'settings-tools'].includes(route.name))
      .every((route) => route.meta.requiresAdmin === undefined)).toBe(true)
  })
})
