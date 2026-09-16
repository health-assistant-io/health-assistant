import { existsSync, readFileSync } from 'node:fs'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { VitePWA } from 'vite-plugin-pwa'

// The family UI library is served raw (optimizeDeps.exclude, per family
// dev-link flow) — but its dependencies must be pre-bundled, or any CJS
// package in its tree (markdown-it via tiptap-markdown, katex, the
// use-sync-external-store shim via @tiptap/react, …) reaches the browser
// as raw CommonJS and crashes with "doesn't provide an export named".
const excludedLib = '@neuronection/assistant-ui'
const libPkgPath = new URL(`./node_modules/${excludedLib}/package.json`, import.meta.url)
// Subpath-only packages (e.g. "@tiptap/pm" exposes only "./state" & co)
// cannot be include entries — vite fails to resolve their root. They are
// still covered: their subpaths bundle inside the depending chunks.
const hasRootEntry = (name: string) => {
  try {
    const p = JSON.parse(
      readFileSync(new URL(`./node_modules/${name}/package.json`, import.meta.url), 'utf8'),
    )
    return !p.exports || '.' in p.exports || Boolean(p.main || p.module)
  } catch {
    return false
  }
}
const libDeps = existsSync(libPkgPath)
  ? Object.keys(JSON.parse(readFileSync(libPkgPath, 'utf8')).dependencies ?? {}).filter(hasRootEntry)
  : []

export default defineConfig({
  optimizeDeps: {
    exclude: [excludedLib],
    include: [
      ...libDeps,
      'use-sync-external-store',
      'use-sync-external-store/shim/with-selector',
      'zustand/vanilla',
      'zustand/traditional',
      'zustand/shallow',
    ],
  },
  plugins: [
    react(),
    VitePWA({
      strategies: 'injectManifest',
      srcDir: 'src',
      filename: 'sw.ts',
      injectManifest: {
        maximumFileSizeToCacheInBytes: 5 * 1024 * 1024,
      },
      registerType: 'autoUpdate',
      includeAssets: ['favicon.ico', 'icon.svg'],
      devOptions: {
        enabled: true,
        type: 'module'
      },
      manifest: {
        name: 'Health Assistant - Universal Health Data Platform',
        short_name: 'Health Assistant',
        description: 'Self-hosted, privacy-first web application for centralizing health and wellness data.',
        theme_color: '#3b82f6',
        background_color: '#ffffff',
        display: 'standalone',
        icons: [
          {
            src: 'pwa-192.png',
            sizes: '192x192',
            type: 'image/png'
          },
          {
            src: 'pwa-512.png',
            sizes: '512x512',
            type: 'image/png'
          },
          {
            src: 'pwa-192-maskable.png',
            sizes: '192x192',
            type: 'image/png',
            purpose: 'maskable'
          },
          {
            src: 'pwa-512-maskable.png',
            sizes: '512x512',
            type: 'image/png',
            purpose: 'maskable'
          }
        ],
        shortcuts: [
          {
            name: 'New Examination',
            url: '/examinations/upload',
            icons: [{ src: 'pwa-192.png', sizes: '192x192', type: 'image/png' }]
          },
          {
            name: 'Upload Result',
            url: '/documents/upload',
            icons: [{ src: 'pwa-192.png', sizes: '192x192', type: 'image/png' }]
          }
        ]
      },
      workbox: {
        globPatterns: ['**/*.{js,css,html,ico,png,svg}'],
        runtimeCaching: [
          {
            // Match same-origin /api/v1 calls so the runtime cache works
            // regardless of where the app is deployed.
            urlPattern: ({ url, sameOrigin }) =>
              sameOrigin && url.pathname.startsWith('/api/v1/auth/me'),
            handler: 'NetworkFirst',
            options: {
              cacheName: 'auth-cache',
              expiration: {
                maxEntries: 1,
                maxAgeSeconds: 60 * 60 * 24 // 24 hours
              }
            }
          },
          {
            urlPattern: ({ url, sameOrigin }) =>
              sameOrigin && url.pathname.startsWith('/api/v1/biomarkers'),
            handler: 'StaleWhileRevalidate',
            options: {
              cacheName: 'biomarker-cache',
              expiration: {
                maxEntries: 50,
                maxAgeSeconds: 60 * 60 * 24 * 7 // 1 week
              }
            }
          },
          {
            urlPattern: ({ url, sameOrigin }) =>
              sameOrigin &&
              /^\/api\/v1\/patients\/[^/]+\/examinations/.test(url.pathname),
            handler: 'NetworkFirst',
            options: {
              cacheName: 'patient-data-cache',
              expiration: {
                maxEntries: 20,
                maxAgeSeconds: 60 * 60 * 24 // 24 hours
              }
            }
          }
        ]
      }
    })
  ],
  server: {
    port: 3000,
    proxy: {
      '/api': {
        target: process.env.VITE_BACKEND_URL || 'http://localhost:8000',
        changeOrigin: true,
        ws: true,
      },
      '/ws': {
        target: process.env.VITE_BACKEND_URL || 'http://localhost:8000',
        ws: true,
        changeOrigin: true,
      }
    }
  },
  preview: {
    port: 3000,
    allowedHosts: true
  }
})
