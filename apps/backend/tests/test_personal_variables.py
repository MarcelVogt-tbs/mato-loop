from uuid import uuid4

import pytest
from pydantic import ValidationError

from mato_loop_backend.agent.tool_arguments import (
    ProtectedQuestionArguments,
    ShellRunArguments,
    validate_protected_question_answers,
)
from mato_loop_backend.models import Credential
from mato_loop_backend.personal_variables import personal_variable_payload


def test_personal_variable_payload_never_reads_or_returns_secret(monkeypatch) -> None:
    variable = Credential(
        id=uuid4(),
        user_id=uuid4(),
        kind="personal_variable",
        label="API_TOKEN",
        encrypted_payload=b"encrypted",
        metadata_json={"secret": True},
    )
    def read(_variable):
        raise AssertionError("secret decrypted")

    monkeypatch.setattr("mato_loop_backend.personal_variables.read_credential", read)

    assert personal_variable_payload(variable) == {
        "id": str(variable.id),
        "name": "API_TOKEN",
        "secret": True,
    }


def test_protected_answers_preserve_exact_value() -> None:
    questions = ProtectedQuestionArguments.model_validate(
        {
            "questions": [
                {
                    "id": "value",
                    "header": "Secret",
                    "question": "Enter the value",
                    "type": "password",
                    "save_proposal": {
                        "name": "API_TOKEN",
                        "secret": True,
                        "action": "create",
                        "trust_level": "public",
                    },
                }
            ]
        }
    )

    assert validate_protected_question_answers(
        questions, {"value": ["  exact value  "]}
    ) == {"value": ["  exact value  "]}


def test_one_time_protected_answers_support_visible_and_masked_values() -> None:
    questions = ProtectedQuestionArguments.model_validate(
        {
            "questions": [
                {
                    "id": "SSH_USERNAME",
                    "header": "Username",
                    "question": "Enter username",
                    "type": "value",
                    "one_time": True,
                },
                {
                    "id": "SSH_PASSWORD",
                    "header": "Password",
                    "question": "Enter password",
                    "type": "password",
                    "one_time": True,
                },
            ]
        }
    )

    assert validate_protected_question_answers(
        questions,
        {"SSH_USERNAME": [" user "], "SSH_PASSWORD": [" password "]},
    ) == {"SSH_USERNAME": [" user "], "SSH_PASSWORD": [" password "]}

    with pytest.raises(ValueError, match="one answer per item"):
        validate_protected_question_answers(
            questions, {"SSH_PASSWORD": [" password "]}
        )


def test_shell_environment_accepts_references_but_rejects_host_names() -> None:
    reference = uuid4()
    assert ShellRunArguments(
        command="env", environment={"API_TOKEN": reference}
    ).environment == {"API_TOKEN": reference}

    for name in ("PATH", "LD_PRELOAD", "GIT_CONFIG_COUNT"):
        with pytest.raises(ValidationError):
            ShellRunArguments(command="env", environment={name: reference})
