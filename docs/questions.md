# Agent questions

The built-in `question` tool lets the model pause an agent run for structured user input.
One tool call contains one to five questions of type `single_choice`, `multiple_choice`, or
`free_text`. Choice questions allow one custom answer by default. Questions may remain unanswered.
The tool must be the only tool call in that model response so later actions cannot run before the
reply is known.

Pending requests and accepted answers are persisted in `question_requests`. The runtime uses the
same single-process waiter semantics as approvals, but question answers never create permission
grants. Users can also reject a request, which returns a controlled rejected tool result. The
frontend loads pending requests independently of SSE and replaces the normal composer with a
sequential question dock. Backend restarts expire pending requests and fail their active runs
through the normal recovery path.
