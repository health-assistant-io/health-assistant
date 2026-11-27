import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    // Node 26 defines globalThis.localStorage (undefined without
    // --localstorage-file), which shadows jsdom's; keep the storage globals off
    // so window.localStorage is the jsdom one.
    execArgv: ['--no-experimental-webstorage'],
    globals: true,
    setupFiles: ['./src/tests/setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
  },
});
