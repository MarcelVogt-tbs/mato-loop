# Documentation

Choose the document for your task. The German [user and administrator handbook](user/index.md) is served at `/docs/`; English developer references stay in the repository.

## Start or operate MatoLoop

- [First chat](user/schnellstart.md), [installation and backup/recovery](user/betrieb-und-datensicherung.md), and [updates/upgrade notes](user/aktualisierung.md) (German)
- [Development setup, checks and documentation maintenance](development.md)
- [Computer installation/update lifecycle](computer-maintenance.md) and [Windows acceptance procedure](windows-agent-acceptance.md)
- [CI and releases](ci-and-releases.md)

## Understand or change the implementation

Begin with the [architecture overview](architecture.md) and [module ownership](modularity.md). Then use the authoritative topic reference:

| Topic | Reference |
| --- | --- |
| Runtime, profiles, execution, files and context budgets | [Agent harness](harness.md) |
| Tool actions, policy defaults and consent scopes | [Tool permissions](tool-permissions.md) |
| Product adapters, resources, tool loading and MCP | [Integrations](integrations.md) |
| Commands, Git management and worktrees | [Commands](commands.md) |
| Model assignment and session initialization | [Session templates](session-templates.md) |
| Direct input, retries and elevated environments | [Run continuations](run-continuations.md) |
| Memory, retrieval and Dreaming | [Memory](memory.md) |
| Personas and assistant identity | [Agent identity](agent-identity.md) |
| Reusable instruction packages | [Skills](skills.md) |
| Questions and secret-bearing tool input | [Questions](questions.md), [protected tool text](protected-tool-text.md) |
| Web retrieval and networking | [Webfetch](webfetch.md) |
| Browser isolation, permissions and viewer | [Browser](browser.md), [Agent browser runtime](../apps/matoloop-agent/BROWSER.md) |
| Instance branding and themes | [Appearance](appearance.md) |

Component entry points: [backend](../apps/backend/README.md), [frontend](../apps/frontend/README.md), [MatoLoop Agent](../apps/matoloop-agent/README.md). Repository contribution rules, including documentation style, live in [AGENTS.md](../AGENTS.md).

## Historical evidence and proposals

These are dated measurements or proposals, **not current contracts or evidence of a new validation run**. Preserve their recorded results; use the references above for current behavior. They remain separate from the published user handbook.

- July 2026: [prompt evaluation](prompt-evaluation-2026-07.md).
- August 2026: [prompt slimming](prompt-slimming-evaluation-2026-08.md).
- September 2026: [reason contract](reason-contract-evaluation-2026-09.md), [bounded Qwen evaluation](qwen38-bounded-evaluation-2026-09.md), [prompt countermeasures](qwen38-prompt-countermeasures-2026-09.md).
- October 2026, context/tools: [tool contracts](tool-contracts-evaluation-2026-10.md), [context search](context-search-evaluation-2026-10.md), [session caching](session-cache-evaluation-2026-10.md), [file routing/delegation](file-routing-delegation-evaluation-2026-10.md).
- October 2026, profiles: [authority](profile-authority-evaluation-2026-10.md), [continuation](profile-continuation-evaluation-2026-10.md), [message metadata](profile-message-metadata-evaluation-2026-10.md), [roles](profile-role-evaluation-2026-10.md), [switching](profile-switch-evaluation-2026-10.md), [temporal follow-ups](profile-temporal-followup-evaluation-2026-10.md).
- October 2026, shell: [CPU decider](shell-decider-cpu-evaluation-2026-10.md), [classifier comparison](shell-classifier-qwen-vs-jevk5-2026-10.md), [inspector hardening](shell-inspector-hardening-2026-10.md).
- November 2026: [session-summary prototype](session-summary-evaluation-2026-11.md).
- [Browser performance measurements](browser-performance.md).
- Proposals: [Memory approvals](plans/memory-dreaming-approvals.md), [projects](plans/opencode-projects.md).
