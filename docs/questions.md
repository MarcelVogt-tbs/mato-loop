# Agent questions

The built-in `question` tool lets the model pause an agent run for structured user input.
One tool call contains one to five questions of type `single_choice`, `multiple_choice`, or
`free_text`. Choice questions allow one custom answer by default. Questions may remain unanswered.
The tool must be the only tool call in that model response so later actions cannot run before the
reply is known.

Pending requests and accepted answers are persisted in `question_requests`. The runtime uses the
same single-process waiter semantics as approvals. Ordinary question answers create no permission
grants; submitted protected administrator input can grant run access or remembered session access.
Users can also reject a request, which returns a controlled rejected tool result. The
frontend loads pending requests independently of SSE and replaces the normal composer with a
sequential question dock. Backend restarts expire pending requests and fail their active runs
through the normal recovery path.

The question dock offers Next for navigation and Done on every step to submit the current draft,
including answers to previously visited questions. Empty answers are allowed, even when all
questions are unanswered. Ordinary questions retain their configured default-answer behavior.
The `answered` status means the request is closed, not that every field has a value. Rejection
discards the draft instead of submitting it.

Protected questions from `secret_input` and `variable_write` also allow empty answer lists.
Only filled fields receive secret references or can be remembered for the session. A skipped
administrator password grants no access. Temporary protected answers persist only a received
marker for filled fields and an empty list for skipped fields; their values are never persisted.
`secret_input` returns the available references in `environment` and the skipped input names in
`unanswered`, with instructions to explain blocked work instead of assuming values or repeating
skipped questions unless requested. An empty durable-storage answer creates or updates nothing;
`variable_write` reports `saved: false`. Protected values remain hidden from the model, so users
must send a change of direction as a normal chat message rather than entering it in a credential
field.

Ordinary question delivery is channel-neutral. A run started from a paired messaging endpoint persists a
versioned question envelope in that endpoint's outbox. Providers may render the envelope with
native controls and direct-reply text input, but all interactions use the same backend state
machine and answer validation. Every interaction revalidates the endpoint owner, source message,
session, run, delivered prompt, and pending request before changing canonical state.

Protected credential questions are handled in MatoLoop rather than delivered as ordinary channel
envelopes. Pending owned requests use `GET /api/questions/pending`; decisions use
`POST /api/questions/{question_id}/answers` or `/reject`. See [protected tool text](protected-tool-text.md)
for the reference/redaction boundary.
