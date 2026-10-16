"""Live context-search trajectories over rollback-only synthetic PostgreSQL fixtures."""

import argparse
import asyncio
import copy
import hashlib
import json
import os
import re
import runpy
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
from jsonschema import ValidationError
from jsonschema.validators import validator_for
from sqlalchemy import select

import mato_loop_backend.prompt_eval as prompt_eval
from mato_loop_backend.db.session import AsyncSessionFactory, engine
from mato_loop_backend.error_contract import error_payload
from mato_loop_backend.files.service import RootFileService
from mato_loop_backend.memory.embeddings import ACTIVE_EMBEDDING_PROFILE, embedding_client
from mato_loop_backend.memory.episode_indexing import EpisodeIndexService
from mato_loop_backend.memory.service import normalize_content
from mato_loop_backend.models import (
    ChatSession,
    MemoryEmbedding,
    MemoryRecord,
    Message,
    MessageSearchChunk,
    UserAccount,
)
from mato_loop_backend.permissions.policy import PermissionDecision
from mato_loop_backend.providers.base import ToolCall
from mato_loop_backend.providers.service import ProviderService
from mato_loop_backend.settings import (
    IntegrationSettings,
    ToolSettings,
    TrustLevel,
    load_effective_ai,
)
from mato_loop_backend.tools.catalog import tool_registry
from mato_loop_backend.tools.context import ToolContext

ROOT = Path('/workspace')
HELPERS = runpy.run_path(str(ROOT / 'scripts/automation-permissions-eval.py'))
TOOLS = ('context_search', 'memory_browse', 'session_read', 'session_browse', 'question',
         'web_search', 'webfetch', 'read', 'glob', 'grep', 'list_execution_agents',
         'select_execution_agent', 'variable_read', 'secret_input')
NOW = datetime(2026, 10, 16, 17, tzinfo=UTC)


@dataclass
class Fixture:
    name: str
    user: str
    memories: list[str]
    sessions: list[tuple[str, list[tuple[str, str]]]]
    answer: str | None
    supplied_reference: bool = False
    memory_category: str = 'fact'


def fixtures():
    return [
        Fixture('memory_only', 'Welches Datumsformat bevorzuge ich für Dateinamen?',
                ['Der Nutzer bevorzugt für Dateinamen das Datumsformat YYYY_MM_DD.'], [], 'YYYY_MM_DD', memory_category='preference'),
        Fixture('session_only', 'Unter welcher Adresse erreiche ich mein Atlas-NAS?',
                ['Der Nutzer besitzt ein Atlas-NAS.'],
                [('Atlas-NAS einrichten', [('user', 'Mein Atlas-NAS ist unter http://192.0.2.73:5080 erreichbar.'),
                                            ('assistant', 'Die Adresse ist für die Einrichtung notiert.')])],
                '192.0.2.73:5080'),
        Fixture('cross_language', 'In welchem Verzeichnis liegt mein Projekt Leuchtturm?',
                ['Der Nutzer arbeitet am Projekt Leuchtturm.'],
                [('Lighthouse project setup', [('user', 'My Leuchtturm project, called Lighthouse in English, lives in /srv/work/lighthouse-main.'),
                                                ('assistant', 'The project directory is established.')])],
                '/srv/work/lighthouse-main'),
        Fixture('long_session', 'Welchen Port hatten wir für den Mistral-Exporter festgelegt?',
                ['Der Nutzer betreibt einen Mistral-Exporter.'],
                [('Mistral-Exporter einrichten', [
                    ('user', 'Wir richten den Mistral-Exporter ein. Zuerst geht es um Farben und Diagramme.'),
                    *[('user' if i % 2 else 'assistant', f'Layout-Schritt {i}: Die Diagramme bekommen eine eigene Legende und einen Titel.') for i in range(1, 121)],
                    ('user', 'Für den Mistral-Exporter haben wir endgültig Port 18437 festgelegt.'),
                    ('assistant', 'Port 18437 ist die vereinbarte Einstellung für den Mistral-Exporter.'),
                    *[('user' if i % 2 else 'assistant', f'Layout-Schritt {i}: Wir prüfen die Farben und die Legende.') for i in range(122, 162)],
                ])], '18437', supplied_reference=True),
        Fixture('scoped_explicit', 'Suche gezielt innerhalb der bekannten Session {session_id}: Welches Aufbewahrungslimit hatten wir für die Boreal-Backups festgelegt?',
                ['Der Nutzer betreibt Boreal-Backups.'],
                [('Boreal-Backups einrichten', [
                    ('user', 'Wir richten die Boreal-Backups ein. Zuerst geht es um die Statusanzeige.'),
                    *[('user' if i % 2 else 'assistant', f'Anzeige-Schritt {i}: Wir prüfen die Statusanzeige und die Beschriftung.') for i in range(1, 101)],
                    ('user', 'Für Boreal-Backups haben wir ein Aufbewahrungslimit von genau 47 Tagen vereinbart.'),
                    ('assistant', 'Die Boreal-Backups werden genau 47 Tage aufbewahrt.'),
                    *[('user' if i % 2 else 'assistant', f'Anzeige-Schritt {i}: Wir prüfen die Legende der Statusanzeige.') for i in range(102, 142)],
                ])], '47 Tage', supplied_reference=True),
        Fixture('two_devices', 'Wie lautet die Adresse von Atlas Pro? Nicht vom Atlas Mini.',
                ['Der Nutzer besitzt Atlas Mini und Atlas Pro.'],
                [('Atlas Mini', [('user', 'Atlas Mini erreichst du unter http://192.0.2.44:7080.')]),
                 ('Atlas Pro', [('user', 'Atlas Pro erreichst du unter http://192.0.2.91:9080.')])],
                '192.0.2.91:9080'),
        Fixture('unknown', 'Unter welcher Adresse erreiche ich meinen Orbit-Speicher?',
                ['Der Nutzer besitzt einen Orbit-Speicher.'],
                [('Orbit-Speicher kaufen', [('user', 'Ich habe einen Orbit-Speicher gekauft. Die Einrichtung steht noch aus.')])],
                None),
        Fixture('tool_only', 'Welche Adresse hatten wir für den Helios-Sensor verwendet?',
                ['Der Nutzer besitzt einen Helios-Sensor.'],
                [('Helios-Sensor testen', [
                    ('user', 'Prüfe den Helios-Sensor im Netzwerk.'),
                    ('assistant', 'Ich prüfe die Erreichbarkeit.'),
                    ('tool', '{"sensor":"Helios","url":"http://192.0.2.118:6080","reachable":true}'),
                    ('assistant', 'Der Helios-Sensor ist erreichbar.'),
                ])], '192.0.2.118:6080'),
    ]


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


async def seed(db, fixture):
    owner = UserAccount(username=f'context-eval-{uuid4().hex}', password_hash='evaluation-only')
    db.add(owner)
    await db.flush()
    memories = []
    for content in fixture.memories:
        _, content_hash = normalize_content(content)
        memory = MemoryRecord(user_id=owner.id, content=content, normalized_content_hash=content_hash,
                              category=fixture.memory_category, audience='both', source_type='manual')
        db.add(memory)
        memories.append(memory)
    sessions = []
    messages = []
    for title, source in fixture.sessions:
        session = ChatSession(user_id=owner.id, title=title, memory_mode='full')
        db.add(session)
        await db.flush()
        session_messages = []
        for i, (role, content) in enumerate(source):
            message = Message(session_id=session.id, role=role, content=content,
                              generation_status='completed', trust_level='public',
                              created_at=NOW - timedelta(days=2) + timedelta(minutes=i))
            db.add(message)
            session_messages.append(message)
        sessions.append((session, session_messages))
        messages.extend(session_messages)
    await db.flush()
    for message in messages:
        await EpisodeIndexService.enqueue(db, message)
    chunks = list((await db.scalars(select(MessageSearchChunk).join(
        Message, Message.id == MessageSearchChunk.message_id).join(
        ChatSession, ChatSession.id == Message.session_id).where(ChatSession.user_id == owner.id))).all())
    vectors = await embedding_client.embed_documents([m.content for m in memories] + [c.content for c in chunks])
    for memory, vector in zip(memories, vectors[:len(memories)], strict=True):
        db.add(MemoryEmbedding(memory_id=memory.id, profile_fingerprint=ACTIVE_EMBEDDING_PROFILE.fingerprint,
                               content_hash=memory.normalized_content_hash, status='ready', vector=vector))
    for chunk, vector in zip(chunks, vectors[len(memories):], strict=True):
        chunk.status, chunk.vector = 'ready', vector
    await db.flush()
    return owner, sessions, memories


def inputs(fixture, model_id, sessions, memories):
    references = []
    if fixture.supplied_reference:
        session, messages = sessions[0]
        references = [{'sessionId': str(session.id), 'title': session.title,
                       'anchorMessage': {'preview': messages[0].content},
                       'sessionReadArguments': {'session_id': str(session.id), 'around_message_id': str(messages[0].id)}}]
    case = prompt_eval.PromptCase(
        id=fixture.name, kind='general', user=fixture.user.replace('{session_id}', str(sessions[0][0].id) if sessions else ''), tools=TOOLS,
        memory_scope={'total': len(memories), 'pinned': 0},
        memory_text=json.dumps({'durableMemories': [], 'priorSessionEpisodes': references}),
        system_options={'injected_episode_count': len(references)},
    )
    return prompt_eval.build_case(case, model_id=model_id)


async def execute(context, raw):
    name, args = raw['name'], raw['arguments']
    if name == 'question':
        return {'waitingForUser': True}
    if name not in {'context_search', 'memory_browse', 'session_read', 'session_browse'}:
        return error_payload('permission.userDenied')
    context.tool_call = ToolCall(raw['id'], name, args)
    return json.loads((await tool_registry.execute(context.tool_call, context)).content)


def validate(raw, schemas):
    schema = schemas.get(raw['name'])
    if schema is None:
        return error_payload('tool.unavailable')
    try:
        validator_for(schema)(schema).validate(raw['arguments'])
        definition = tool_registry._definitions.get(raw['name']) or tool_registry._operation_definitions.get(raw['name'])
        definition.validate_arguments(raw['arguments'])
    except ValidationError as exc:
        parts = list(exc.absolute_path)
        if exc.validator == 'additionalProperties' and isinstance(exc.instance, dict):
            parts += sorted(set(exc.instance) - set(exc.schema.get('properties', {})))[:1]
        return error_payload('tool.invalidArguments', field='.'.join(map(str, parts)) or None)
    except (ValueError, TypeError) as exc:
        errors = exc.errors(include_input=False, include_url=False) if hasattr(exc, 'errors') else []
        field = '.'.join(map(str, errors[0]['loc'])) if errors else getattr(exc, 'field', None)
        return error_payload('tool.invalidArguments', field=field or None)
    return None


def assess(fixture, row):
    final = row.get('final', '')
    results = [result for step in row['steps'] for result in step.get('toolResults', [])]
    evidence = '\n'.join(json.dumps(r['content'], ensure_ascii=False) for r in results)
    asked = any(r['name'] == 'question' and not r.get('invalid') for r in results)
    row['askedQuestion'] = asked
    row['answerRetrieved'] = fixture.answer is not None and fixture.answer in evidence
    if fixture.answer is None:
        row['passed'] = (row['status'] in {'completed', 'question'} and
                         (asked or bool(re.search(r'nicht|keine|unbekannt|fehlt', final, re.I))) and
                         not re.search(r'192\.0\.2\.|https?://', final))
    else:
        row['passed'] = (row['status'] == 'completed' and row['answerRetrieved'] and fixture.answer in final and not asked)
        if fixture.name == 'two_devices':
            urls = re.findall(r'https?://[\d.:]+', final)
            row['passed'] = row['passed'] and bool(urls) and urls[0] == 'http://' + fixture.answer
    row['toolCalls'] = len(results)
    row['validationErrors'] = sum(r.get('invalid', False) for r in results)
    row['targetedSearches'] = sum(bool(r.get('arguments', {}).get('session_id')) and
                                  r['name'] == 'context_search' for r in results)


async def execution_context(db, owner):
    current = ChatSession(user_id=owner.id, title='Current evaluation', memory_mode='full')
    db.add(current)
    await db.flush()

    async def allow_read(_request, *, check_only=False):
        return PermissionDecision.ALLOW

    return ToolContext(
        db=db, run_id=uuid4(), session=current, tool_call=ToolCall('native-eval', '', {}),
        root_files=RootFileService(current.id), integration_settings=IntegrationSettings(),
        tool_settings=ToolSettings(), ask_handler=allow_read, memory_read_enabled=True,
        memory_trust_level=TrustLevel.PUBLIC,
    )


async def trial(db, owner, sessions, memories, fixture, repetition, provider, model, deadline, report, save):
    messages, tools = inputs(fixture, model.model_id, sessions, memories)
    schema_hash = digest(tools)
    report['schemas'].setdefault(schema_hash, tools)
    row = {'scenario': fixture.name, 'variant': 'implemented', 'repetition': repetition,
           'messages': copy.deepcopy(messages), 'schemaHash': schema_hash, 'steps': [], 'status': 'running'}
    report['records'].append(row)
    start = time.monotonic()
    trial_deadline = min(deadline, start + 180)
    schemas = {tool['name']: tool['parameters'] for tool in tools}
    context = await execution_context(db, owner)
    try:
        for number in range(6):
            remaining = trial_deadline - time.monotonic()
            if remaining <= 0:
                row['status'] = 'timeout'
                break
            step = {'scenario': fixture.name, 'variant': 'implemented', 'repetition': repetition,
                    'model': model.model_id, 'number': number + 1}
            row['steps'].append(step)
            save()
            await HELPERS['complete_request'](step, messages, tools, provider, model, 'medium', remaining, save,
                                               check_result=lambda *_: [])
            if step['status'] != 'completed':
                row['status'] = step['status']
                break
            calls = step['toolCalls']
            if not calls:
                row.update(status='completed', final=step['content'])
                break
            messages.append({'role': 'assistant', 'content': step['content'], 'tool_calls': calls})
            step['toolResults'] = []
            for raw in calls:
                error = validate(raw, schemas)
                if raw['name'] == 'question' and len(calls) != 1:
                    error = error_payload('tool.invalidArguments', field='questions')
                payload = error or await execute(context, raw)
                step['toolResults'].append({'name': raw['name'], 'arguments': raw['arguments'],
                                            'content': payload, 'invalid': bool(error) or payload.get('error', {}).get('code') == 'tool.invalidArguments'})
                messages.append({'role': 'tool', 'tool_call_id': raw['id'], 'content': json.dumps(payload, ensure_ascii=False)})
            if any(result['name'] == 'question' and not result['invalid'] for result in step['toolResults']):
                row.update(status='question', final=step['content'])
                break
        else:
            row['status'] = 'step_limit'
    finally:
        row['seconds'] = round(time.monotonic() - start, 3)
        assess(fixture, row)
        save()
        print('TRIAL ' + json.dumps({k: v for k, v in row.items() if k not in {'messages', 'steps', 'final'}}), flush=True)


async def run(args):
    if args.output.exists():
        raise SystemExit('Refusing to overwrite prior results')
    async with AsyncSessionFactory() as db:
        user = (await db.scalars(select(UserAccount).where(UserAccount.is_admin.is_(True), UserAccount.is_active.is_(True)))).one()
        ai = await load_effective_ai(db, user.id)
    provider = next(p for p in ai.providers if p.enabled and p.name == 'R9700')
    model = next(m for m in ai.models if m.enabled and m.provider_id == provider.id and m.model_id == 'qwen3.8-27b-mxfp4')
    prepared = await ProviderService.prepare_request(provider=provider, model=model, messages=[{'role': 'user', 'content': 'test'}], tools=[], reasoning_effort='medium')
    source_paths = [path for path in (ROOT / 'apps/backend/src/mato_loop_backend').rglob('*.py')]
    def hashes():
        return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in source_paths}
    report = {'commit': os.environ.get('MATO_LOOP_EVAL_COMMIT'), 'startedAt': datetime.now(UTC).isoformat(),
              'model': {'id': model.model_id, 'provider': provider.name, 'adapter': provider.adapter_type,
                        'contextWindow': model.context_window_tokens, 'reasoningEffort': prepared.request.reasoning_effort,
                        'enableThinking': prepared.request.enable_thinking, 'temperature': prepared.request.temperature},
              'method': 'Live provider multi-round trajectories using production context_search, memory_browse and session read/browse handlers and native argument validation. Synthetic owner-bound rows in rollback-only transactions. Read approvals are supplied by the evaluator; unrelated actions never execute. Not a persisted AgentRuntime or UI approval test.',
              'productionHashes': hashes(), 'runnerHash': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'fixtureHash': digest([vars(f) for f in fixtures()]), 'schemas': {}, 'fixtures': [vars(f) for f in fixtures()], 'records': []}
    async with httpx.AsyncClient() as client:
        base = str(provider.config.get('base_url', '')).rstrip('/')
        for endpoint in ('/models', '/../version'):
            try:
                response = await client.get(base + endpoint, timeout=5)
                report.setdefault('serverMetadata', {})[endpoint] = response.json() if response.is_success else {'status': response.status_code}
            except (httpx.HTTPError, ValueError):
                pass
    prompt_eval.FIXED_NOW = NOW
    start = time.monotonic()
    deadline = start + args.budget
    def save():
        report['elapsedSeconds'] = round(time.monotonic() - start, 3)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    save()
    try:
        for fixture in fixtures():
            if args.case and fixture.name not in args.case:
                continue
            async with AsyncSessionFactory() as db:
                try:
                    owner, sessions, memories = await seed(db, fixture)
                    for repetition in range(1, args.repetitions + 1):
                        if time.monotonic() >= deadline:
                            report['stoppedAtDeadline'] = True
                            return
                        await trial(db, owner, sessions, memories, fixture, repetition,
                                    provider, model, deadline, report, save)
                finally:
                    await db.rollback()
            report.setdefault('rolledBackScenarios', []).append(fixture.name)
            save()
    finally:
        report['finishedAt'] = datetime.now(UTC).isoformat()
        report['productionHashesAfter'] = hashes()
        report['productionUnchanged'] = report['productionHashesAfter'] == report['productionHashes']
        save()
        await embedding_client.close()
        await engine.dispose()


async def check():
    for fixture in fixtures():
        if fixture.supplied_reference:
            continue
        _messages, tools = inputs(fixture, 'qwen3.8-27b-mxfp4', [], [])
        names = {tool['name'] for tool in tools}
        assert {'context_search', 'memory_browse'} <= names
        assert not {'memory_retrieve', 'session_search'} & names
    unsupported = {
        'status': 'completed', 'final': 'Das Aufbewahrungslimit beträgt 47 Tage.',
        'steps': [{'toolResults': [{'name': 'session_read', 'arguments': {}, 'content': {
            'messages': [{'content': 'Anzeige-Schritt 47: Wir prüfen die Statusanzeige.'}],
        }}]}],
    }
    assess(next(f for f in fixtures() if f.name == 'scoped_explicit'), unsupported)
    assert not unsupported['passed']
    async with AsyncSessionFactory() as db:
        try:
            fixture = next(f for f in fixtures() if f.name == 'session_only')
            owner, sessions, memories = await seed(db, fixture)
            context = await execution_context(db, owner)
            combined = await execute(context, {'id': 'search', 'name': 'context_search',
                                               'arguments': {'query': 'Atlas-NAS Adresse Zugang'}})
            assert fixture.answer in json.dumps(combined)
            session_id = str(sessions[0][0].id)
            scoped = await execute(context, {'id': 'scoped', 'name': 'context_search',
                                             'arguments': {'query': '192.0.2.73', 'session_id': session_id}})
            assert scoped['sessions'] and all(item['sessionId'] == session_id for item in scoped['sessions'])
            schemas = {tool['name']: tool['parameters'] for tool in inputs(fixture, 'qwen3.8-27b-mxfp4', sessions, memories)[1]}
            assert validate({'name': 'context_search', 'arguments': {'query': 'Atlas', 'scope': 'invalid'}},
                            schemas)['error']['field'] == 'scope'
        finally:
            await db.rollback()
    await embedding_client.close()
    await engine.dispose()
    print('Checks passed: production schemas/handlers, scoped retrieval, invalid scope, unsupported-answer scoring, rollback.', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--output', type=Path, default=ROOT / 'data/context-search-20261016.json')
    parser.add_argument('--repetitions', type=int, default=2)
    parser.add_argument('--budget', type=int, default=1800)
    parser.add_argument('--case', action='append')
    arguments = parser.parse_args()
    asyncio.run(check() if arguments.check else run(arguments))
