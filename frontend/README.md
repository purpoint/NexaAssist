# Frontend

React + TypeScript client for NexaAssist, built with Vite.

> **Scaffolding only.** No UI has been implemented and dependencies have not
> been installed. `src/App.tsx` is a placeholder.

## Layout

```
src/
├── main.tsx       React entry point
├── App.tsx        placeholder root component
├── components/    reusable UI components
├── pages/         route-level views
├── lib/           API client, helpers, shared types
└── styles/        global styles
public/            static assets served as-is
```

## Setup

```bash
npm install
```

```bash
npm run dev
```

The dev server runs on port 5173, which matches the default `CORS_ORIGINS` in
`.env.example`.

## Configuration

Browser-visible config comes from Vite env variables prefixed with `VITE_`.
The backend base URL is `VITE_API_BASE_URL`, declared in `.env.example` and
typed in `src/vite-env.d.ts`.
