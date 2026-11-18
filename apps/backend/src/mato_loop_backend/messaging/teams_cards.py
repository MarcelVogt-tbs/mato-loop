from __future__ import annotations

import hashlib
import json

from mato_loop_backend.messaging.approvals import encode_approval_callback, parse_approval_payload
from mato_loop_backend.messaging.questions import (
    QuestionInteraction,
    encode_question_callback,
    parse_question_envelope,
)
from mato_loop_backend.messaging.sessions import session_help_sections
from mato_loop_backend.models import MessagingOutbox


def text_block(text: str, **style: str) -> dict:
    return {"type": "TextBlock", "text": text, "wrap": True, **style}


def adaptive_card(body: list[dict], actions: list[dict]) -> dict:
    return {
        "type": "AdaptiveCard",
        "version": "1.5",
        "$schema": "https://adaptivecards.io/schemas/adaptive-card.json",
        "body": body,
        "actions": actions,
    }


def help_card() -> dict:
    sessions, coding = session_help_sections("teams")
    body = [text_block("MatoLoop-Befehle", size="Medium", weight="Bolder")]
    for heading, commands in (("Chat und Sessions", sessions), ("Coding", coding)):
        body.append(text_block(heading, weight="Bolder", spacing="Medium"))
        if heading == "Coding":
            body.append(text_block(
                "Wähle zuerst unter /sessions einen Chat mit Computer und Workspace.",
                size="Small",
                spacing="Small",
            ))
        body.extend({
            "type": "RichTextBlock",
            "spacing": "Small",
            "inlines": [
                {"type": "TextRun", "text": name, "weight": "Bolder"},
                {"type": "TextRun", "text": f" – {description}"},
            ],
        } for name, description in commands)
    return adaptive_card(body, [])


def session_list_card(item: MessagingOutbox) -> dict:
    body = [text_block("Chat wechseln", size="Medium", weight="Bolder")]
    revision = card_revision(item.payload)
    for session in item.payload["sessions"]:
        body.append({
            "type": "ActionSet",
            "actions": [{
                "type": "Action.Execute",
                "title": session["title"],
                "verb": "matoloop",
                "associatedInputs": "none",
                "data": {
                    "outboxId": str(item.id), "revision": revision,
                    "callback": f"session:{session['id']}",
                },
            }],
        })
    if len(body) == 1:
        body.append(text_block("Noch keine Chats vorhanden."))
    return adaptive_card(body, [])


def card_revision(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=True).encode()
    ).hexdigest()


def interaction_card(item: MessagingOutbox) -> dict | None:
    approval = parse_approval_payload(item.payload)
    question = parse_question_envelope(item.payload)
    if approval is None and question is None:
        return None
    revision = card_revision(item.payload)

    def action(label: str, callback: str, *, submit_inputs: bool = False) -> dict:
        return {
            "type": "Action.Execute",
            "title": label,
            "verb": "matoloop",
            "associatedInputs": "auto" if submit_inputs else "none",
            "data": {"outboxId": str(item.id), "revision": revision, "callback": callback},
        }

    body = []
    actions = []
    if approval:
        body.append(text_block("Deine Freigabe ist nötig", size="Medium", weight="Bolder"))
        body.append(text_block(approval.text, spacing="Small"))
        actions = [
            action(value.label, encode_approval_callback(approval.approval_id, value.decision))
            for value in approval.actions
        ]
    else:
        current = question.questions[question.current_index]
        has_next = question.current_index < len(question.questions) - 1
        submit_label = "Weiter" if has_next else "Antwort senden"
        body.append(text_block("Deine Antwort ist nötig", size="Medium", weight="Bolder"))
        body.append(text_block(current.header, weight="Bolder", spacing="Small"))
        if len(question.questions) > 1:
            body.append(text_block(
                f"Frage {question.current_index + 1} von {len(question.questions)}",
                size="Small",
                spacing="Small",
            ))
        body.append(text_block(current.question))
        answers = question.answers.get(current.id, [])
        if current.type == "single_choice" and not question.awaiting_text:
            row: list[dict] = []

            def flush_choices() -> None:
                if row:
                    body.append({"type": "ActionSet", "spacing": "Small", "actions": row.copy()})
                    row.clear()

            for index, option in enumerate(current.options):
                selected = option.label in answers
                choice = action(
                    ("✓ " if selected else "") + option.label,
                    encode_question_callback(
                        question.question_id,
                        QuestionInteraction(
                            action="select",
                            optionIndex=index,
                        ),
                    ),
                )
                standalone = bool(option.description) or len(option.label) > 12
                if standalone:
                    flush_choices()
                if option.description:
                    body.append(text_block(f"{option.label}: {option.description}"))
                row.append(choice)
                if standalone or len(row) == 2:
                    flush_choices()
            flush_choices()
        if current.type == "multiple_choice":
            body.append({
                "type": "Input.ChoiceSet",
                "id": "answer",
                "label": "Optionen auswählen",
                "style": "expanded",
                "isMultiSelect": True,
                "choices": [
                    {"title": option.label, "value": str(index)}
                    for index, option in enumerate(current.options)
                ],
                "value": ",".join(
                    str(index) for index, option in enumerate(current.options)
                    if option.label in answers
                ),
            })
            body.extend(
                text_block(f"**{option.label}:** {option.description}", spacing="Small")
                for option in current.options if option.description
            )
        if current.type == "free_text" or current.allow_custom:
            labels = {option.label for option in current.options}
            custom = current.type != "free_text"
            body.append(
                {
                    "type": "Input.Text",
                    "id": "customAnswer" if current.type == "multiple_choice" else "answer",
                    "label": "Eigene Antwort (optional)" if custom else "Deine Antwort",
                    "isMultiline": True,
                    "maxLength": 20_000,
                    "value": "\n".join(value for value in answers if value not in labels),
                }
            )
        if current.type == "multiple_choice":
            actions.append(action(
                submit_label, f"qchoices:{question.question_id}", submit_inputs=True,
            ))
        elif current.type == "free_text" or current.allow_custom:
            label = "Eigene Antwort senden" if current.type == "single_choice" and not has_next else submit_label
            actions.append(action(label, f"qtext:{question.question_id}", submit_inputs=True))
        if question.current_index:
            actions.append(
                action(
                    "Zurück",
                    encode_question_callback(
                        question.question_id, QuestionInteraction(action="back")
                    ),
                )
            )
        actions.append(action(
            "Ablehnen",
            encode_question_callback(question.question_id, QuestionInteraction(action="reject")),
        ))
    return adaptive_card(body, actions)
