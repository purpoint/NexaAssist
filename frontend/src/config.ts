/**
 * Runtime configuration.
 *
 * Vite only exposes variables prefixed with `VITE_` to browser code, and it
 * inlines them at build time -- so this is a build-time choice, not a runtime
 * one, and a deployment that needs a different backend needs a different
 * build. Read in one place so no component reaches for `import.meta.env`.
 */

const DEFAULT_API_BASE_URL = 'http://127.0.0.1:8000/api/v1';

/** Base URL of the NexaAssist v1 API, without a trailing slash. */
export const API_BASE_URL: string = (
  import.meta.env.VITE_API_BASE_URL ?? DEFAULT_API_BASE_URL
).replace(/\/+$/, '');

/**
 * The WebSocket URL, derived rather than configured separately.
 *
 * Two settings that must agree are two settings that will eventually
 * disagree; the socket lives beside the HTTP API by construction.
 */
export const WS_URL: string = `${API_BASE_URL.replace(/^http/, 'ws')}/ws`;
