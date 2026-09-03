# API reference

> The generated schema at `/openapi.json` is authoritative for shapes; this
> describes the parts a schema cannot say — what an endpoint is for, when to
> reach for it, and what it refuses to do.

Version 1 is served under `/api/v1`. Interactive documentation is at `/docs`.

## Conventions

**One error shape.** Every failure, at every status, over both transports, is
the same object:

```json
{
  "code": "conversation_not_found",
  "message": "The requested conversation was not found.",
  "details": {"conversation_id": "…"}
}
```

`code` is stable and meant to be branched on; `message` is for people and may
be reworded. `details` is a whitelist — never a repr, never a traceback, and
never the value that was rejected. A validation failure reports which fields
were wrong and not what was in them, because the field it is describing may be
a customer's message with a card number in it.

**Authentication is optional and off by default.** With `AUTH_PROVIDER=none`
every request is anonymous and nothing is refused. With `api_key`, send:

```
Authorization: Bearer <key>
```

An endpoint that can require authentication documents `401`. Ownership
(`AUTHZ_PROVIDER=subject`) then scopes each resource to the subject that
created it — a `404`, not a `403`, because confirming that a resource exists
is already telling somebody something.

**Rate limiting** is off by default. When enabled, an exceeded limit is `429`
with `Retry-After`.

**Readiness is not liveness.** `/health` says the process is up. `/ready`
reports each component independently and only fails (`503`) when something is
configured and broken — a component that was never configured reports
`not_configured` and is not an error.

## Endpoints

### The assistant

| | |
| --- | --- |
| `POST /assistant/messages` | The main entry point. |

Takes a customer message, classifies it, answers it from retrieved documents,
applies policy, and escalates if a person is needed. The response carries the
answer, its citations, the route that was taken and why, and — when it
escalated — the reason. `503` means a required component (the database, the
model provider) is not configured.

This is the endpoint to use unless you specifically want one stage of it.

### Stages, individually

| | |
| --- | --- |
| `POST /intent/analyze` | Classify a message without answering it. |
| `POST /documents/answer` | Answer strictly from retrieved documents. |

`/documents/answer` either cites its sources or reports that it could not
answer. It will not fall back to the model's own knowledge; an answer with no
citation is a refusal, deliberately.

### Knowledge base

| | |
| --- | --- |
| `POST /documents` | Ingest a document: chunked and embedded on the way in. |
| `GET /documents` | List documents. |
| `GET /documents/{document_id}` | One document. |

### Conversations

| | |
| --- | --- |
| `POST /conversations` | Start a session. |
| `GET /conversations/{conversation_id}` | Its metadata. |
| `GET /conversations/{conversation_id}/messages` | Its history. |

History is bounded when it is fed back to the model: an unbounded transcript
eventually exceeds the context window, and the failure lands on whichever
message happens to be next.

### Tickets

| | |
| --- | --- |
| `POST /tickets` · `GET /tickets` · `GET /tickets/{ticket_id}` | The support-ticket domain. |

### Realtime

| | |
| --- | --- |
| `POST /ws/ticket` | Exchange a key for a short-lived handshake ticket. |
| `WS /ws?ticket=…` | The socket. Not described by OpenAPI. |

A browser cannot set a header on a WebSocket handshake, so the API key is not
what reaches the socket. A client presents its key over authenticated HTTP,
receives a ticket, and spends that on the handshake. The ticket is opaque,
random, bound to one subject, single-use, and valid for a minute — so a leaked
URL in a proxy log is worth nothing by the time anybody reads it.

Frames are JSON with a `type` discriminator. From the client: `ping`, and `ask`
to request an answer. From the server: `ready` once on connect (with a
`connection_id` and `protocol_version`), `pong`, `delta` for each fragment of
an answer, `complete` when a stream ends — carrying the assembled text, so a
client that does not render incrementally can ignore the deltas — and `error`,
in the same `code`/`message` shape as HTTP.

Concatenating the deltas of a stream reproduces the answer exactly. A frame
larger than 64 KiB closes the connection with 1009 rather than being answered,
because answering it would mean having already buffered it. A malformed frame
gets an `error` and the connection stays open: one bad message is not a reason
to drop a session.

## What the API will not do

- Echo back what you sent it in an error.
- Answer from outside the supplied documents.
- Report a schema mismatch by migrating your database.
- Distinguish "no such ticket" from "not yours".
