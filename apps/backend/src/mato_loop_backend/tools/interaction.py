from __future__ import annotations

from uuid import uuid4

import mato_loop_backend.permissions.resolution as permissions_resolution
import mato_loop_backend.tools.access as tools_access
from mato_loop_backend.agent.session_secrets import session_secrets
from mato_loop_backend.permissions.policy import personal_variables_permission
from mato_loop_backend.personal_variables import (
    get_personal_variable,
    get_personal_variable_by_name,
    list_personal_variables,
    personal_variable_payload,
)
from mato_loop_backend.settings import ToolExecutionPolicy
from mato_loop_backend.tools.actions import _SECRETS_ACCESS, _SECRETS_WRITE
from mato_loop_backend.tools.arguments import (
    ProtectedQuestionArguments,
    ProtectedQuestionItem,
    ProtectedQuestionResolution,
    QuestionArguments,
    SecretInputArguments,
    TaskArguments,
    TodoWriteArguments,
    VariableReadArguments,
    VariableWriteArguments,
)
from mato_loop_backend.tools.context import ToolContext as ToolContext
from mato_loop_backend.tools.contracts import ToolDefinition as ToolDefinition
from mato_loop_backend.tools.contracts import ToolResult as ToolResult


async def _question(arguments: QuestionArguments, context: ToolContext) -> ToolResult:
    answers = await context.ask_user(arguments)
    if answers is None:
        return ToolResult.error("question.expired")
    if answers == "rejected":
        return ToolResult.error("question.rejected")
    return ToolResult.json(
        {
            "answers": [
                {
                    "questionId": question.id,
                    "question": question.question,
                    "answers": answers[question.id],
                    "answered": bool(answers[question.id]),
                }
                for question in arguments.questions
            ]
        }
    )


async def _variable_read(
    arguments: VariableReadArguments, context: ToolContext
) -> ToolResult:
    if not await tools_access.allow(
        context, personal_variables_permission(context.session.user_id)
    ):
        return ToolResult.error("permission.denied")
    if arguments.name is not None:
        variable = await get_personal_variable_by_name(
            context.db, context.session.user_id, arguments.name
        )
        if variable is None:
            return ToolResult.error("personalVariable.notFound")
        variables = [variable]
    else:
        variables = await list_personal_variables(context.db, context.session.user_id)
    return ToolResult.json(
        {
            "variables": [
                {
                    **personal_variable_payload(variable),
                    "valueRef": str(variable.id),
                }
                for variable in variables
            ]
        }
    )


async def _secret_input(
    arguments: SecretInputArguments, context: ToolContext
) -> ToolResult:
    answers = await context.ask_user(
        ProtectedQuestionArguments(
            questions=[
                ProtectedQuestionItem(
                    id=item.name,
                    header=item.label,
                    question=item.question,
                    type="password" if item.masked else "value",
                    one_time=True,
                )
                for item in arguments.inputs
            ]
        )
    )
    if answers is None:
        return ToolResult.error("question.expired")
    if answers == "rejected":
        return ToolResult.error("question.rejected")
    assert isinstance(answers, ProtectedQuestionResolution)
    remembered = {
        item.name: answers.answers[item.name][0]
        for item in arguments.inputs
        if item.name in answers.remember_for_session
    }
    session_environment = session_secrets.put(
        context.session.id,
        remembered,
        {
            item.name: item.description
            for item in arguments.inputs
            if item.description and item.name in answers.remember_for_session
        },
    )
    environment = {}
    for item in arguments.inputs:
        if item.name in session_environment:
            environment[item.name] = session_environment[item.name]
        else:
            secret_id = uuid4()
            context.one_time_secrets[secret_id] = answers.answers[item.name][0]
            environment[item.name] = f"run-secret:{secret_id}"
    return ToolResult.json({"environment": environment})


async def _variable_write(
    arguments: VariableWriteArguments, context: ToolContext
) -> ToolResult:
    context.tool_settings = await tools_access.profile_tool_settings(context)
    if permissions_resolution.tool_execution_policy(
        context.tool_settings, "secrets.write", ToolExecutionPolicy.ASK
    ) == ToolExecutionPolicy.DISABLED or not context.tool_settings.tool_availability.get(
        "variable_write", True
    ):
        return ToolResult.error("tool.unavailable")
    action = "update" if arguments.variable_id is not None else "create"
    if arguments.variable_id is not None:
        variable = await get_personal_variable(
            context.db, context.session.user_id, arguments.variable_id
        )
        if variable is None:
            return ToolResult.error("personalVariable.notFound")
    questions = ProtectedQuestionArguments(
        questions=[
            ProtectedQuestionItem(
                id="value",
                header="Secret" if arguments.secret else "Variable",
                question=f"Welcher Wert soll als {arguments.name} gespeichert werden?",
                type="password" if arguments.secret else "value",
                save_proposal={
                    "name": arguments.name,
                    "description": arguments.description,
                    "secret": arguments.secret,
                    "action": action,
                    "variable_id": arguments.variable_id,
                    "agent_profile_id": context.agent_profile_id,
                    "trust_level": context.model_trust_level,
                },
            )
        ]
    )
    answers = await context.ask_user(questions)
    if answers is None:
        return ToolResult.error("question.expired")
    if answers == "rejected":
        return ToolResult.error("question.rejected")
    saved = await get_personal_variable_by_name(
        context.db, context.session.user_id, arguments.name
    )
    if saved is None:
        return ToolResult.error("personalVariable.notFound")
    return ToolResult.json(
        {
            "saved": True,
            "name": arguments.name,
            "description": arguments.description,
            "secret": arguments.secret,
            "valueRef": str(saved.id),
        }
    )


async def _task(arguments: TaskArguments, context: ToolContext) -> ToolResult:
    if context.task_handler is None:
        return ToolResult.error("tool.unavailable")
    return await context.task_handler(arguments)


async def _todo_write(arguments: TodoWriteArguments, _context: ToolContext) -> ToolResult:
    return ToolResult.json(arguments.model_dump(mode="json"))


QUESTION = ToolDefinition(
            "question",
            "Ask 1-5 questions; call alone. Copy every requested type exactly. Examples are not "
            "options. allow_custom replaces Other; default_answers are fallbacks. "
            "Never use for commands, output, or permissions.",
            QuestionArguments,
            _question,
            group="agent",
            label="Rückfrage stellen",
            icon="help_outline",
        )

TASK = ToolDefinition(
            "task",
            "Join scoped self-contained tasks with bounded results; avoid write conflicts; 8/run; no recursion.",
            TaskArguments,
            _task,
            group="agent",
            label="Aufgaben delegieren",
            icon="account_tree",
        )

TODO_WRITE = ToolDefinition(
            "todo_write",
            "Replace the current session checklist with the complete updated list. Each item has content "
            "and status: pending, in_progress, completed, or cancelled. Omitted items are removed. An "
            "empty list clears the checklist. Use at most one in_progress item. This updates tracking "
            "only; it does not perform or verify the listed work.",
            TodoWriteArguments,
            _todo_write,
            group="agent",
            label="Aufgaben aktualisieren",
            icon="checklist",
        )

VARIABLE_READ = ToolDefinition(
            "variable_read",
            "List personal variables or read one by name. Secret values are never returned; use valueRef "
            "with shell_run.environment to provide a value directly to an isolated process.",
            VariableReadArguments,
            _variable_read,
            group="agent",
            label="Variable lesen",
            icon="key",
            actions=(_SECRETS_ACCESS,),
            releases_private_data=True,
        )

VARIABLE_WRITE = ToolDefinition(
            "variable_write",
            "Ask for and durably save one personal variable or secret only when the user explicitly requests "
            "storage; otherwise use secret_input. Never put the value in tool arguments. Include variable_id "
            "only when updating an exact existing variable. Add description when its purpose is known.",
            VariableWriteArguments,
            _variable_write,
            group="agent",
            label="Variable speichern",
            icon="password",
            actions=(_SECRETS_WRITE,),
        )

SECRET_INPUT = ToolDefinition(
            "secret_input",
            "Ask once for all missing credentials, including usernames. Values are not returned. The user may "
            "keep selected fields temporarily for this session; other references remain reusable throughout the "
            "current run. Call alone, then pass references to shell_run.environment or the personal settings tool. "
            "Reuse the exact name/reference pairs instead of asking again. Set masked false only for values the user may see while "
            "typing. Use customize_matoloop only for explicitly requested durable storage.",
            SecretInputArguments,
            _secret_input,
            group="agent",
            label="Secret einmalig abfragen",
            icon="password",
        )
