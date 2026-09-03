import { fileURLToPath, URL } from 'node:url';

import react from '@vitejs/plugin-react';
/// <reference types="vitest" />
import { defineConfig } from 'vite';

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    port: 5173,
  },
  test: {
    // jsdom, because the components under test render and respond to events;
    // a node environment would only be able to test the client.
    environment: 'jsdom',
    globals: false,
    setupFiles: ['./src/test/setup.ts'],
  },
});
