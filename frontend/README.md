# Frontend

React + TypeScript client for NexaAssist, built with Vite.

It talks to the API over both transports: HTTP for everything that has a
request and a response, and a WebSocket for answers that arrive a fragment at
a time. The socket is an enhancement rather than a requirement — when it is
not connected the client falls back to HTTP and the conversation still works.

## Layout

```
src/
├── main.tsx           React entry point
├── App.tsx            composes the layout and the conversation screen
├── config.ts          the one place import.meta.env is read
├── api/
│   ├── client.ts      typed fetch wrapper; one error shape in, one out
│   ├── types.ts       mirrors the server's schemas
│   └── errors.ts      ApiError, carrying the server's code
├── conversation/
│   ├── model.ts       the message model, transport-agnostic
│   ├── useConversation.ts  send, receive, and reconcile streamed text
│   ├── MessageList.tsx · Composer.tsx · Citations.tsx
│   └── ConversationScreen.tsx
├── realtime/
│   ├── frames.ts      the socket's frame types, mirroring app.realtime.envelope
│   └── useRealtime.ts connection lifecycle, reconnection, ticket handshake
├── auth/
│   ├── useApiKey.ts   the key, held in this browser and nowhere else
│   └── ApiKeyPanel.tsx
├── components/        Layout and the shared primitives
├── hooks/useReadiness.ts   what the backend says it is missing
├── styles/app.css
└── test/setup.ts      jsdom gaps: localStorage, scrollIntoView
```

Tests sit beside what they test, as `*.test.ts(x)`.

## Setup

```bash
npm ci
```

```bash
npm run dev
```

The dev server runs on port 5173, which matches the default `CORS_ORIGINS` in
`.env.example`.

| Command | |
| --- | --- |
| `npm run dev` | Dev server with hot reload. |
| `npm run test` | Vitest, in jsdom. |
| `npm run typecheck` | `tsc --noEmit`. |
| `npm run build` | Typechecks first, then builds — an artefact that compiled a type error is one nobody wanted. |

## Configuration

Browser-visible config comes from Vite env variables prefixed with `VITE_`,
read in `src/config.ts` and nowhere else. The backend base URL is
`VITE_API_BASE_URL`, declared in `.env.example` and typed in
`src/vite-env.d.ts`. The WebSocket URL is derived from it rather than
configured separately: two settings that must agree are two settings that will
eventually disagree.

Vite inlines these at build time, so the API URL is chosen when the bundle is
built. A deployment pointing at a different backend needs a different build —
which is why the container takes it as a build argument.

## The API key

When the backend runs with `AUTH_PROVIDER=api_key`, the client asks for a key
and keeps it in this browser's storage. It is sent as a bearer header on HTTP
requests, and it is never put in a URL — a WebSocket handshake cannot carry a
header, so the client exchanges the key for a short-lived, single-use ticket
over HTTP and spends that on the socket instead.
