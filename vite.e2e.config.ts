import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import type { Connect } from 'vite'

const localApiStub = () => ({
  name: 'skillfair-e2e-local-api-stub',
  configureServer(server: { middlewares: Connect.Server }) {
    server.middlewares.use((request, response, next) => {
      const localRequest = request as unknown as { method?: string; url?: string }
      const pathname = (localRequest.url ?? '/').split('?', 1)[0]
      if (!pathname.startsWith('/api/')) {
        next()
        return
      }

      response.setHeader('Content-Type', 'application/json; charset=utf-8')
      response.setHeader('Cache-Control', 'no-store')
      if (localRequest.method === 'GET' && pathname === '/api/auth/me') {
        response.statusCode = 401
        response.end(JSON.stringify({ detail: 'Authentication required.' }))
        return
      }

      response.statusCode = 503
      response.end(JSON.stringify({ detail: 'Backend APIs are disabled in frontend-only browser smoke tests.' }))
    })
  },
})

export default defineConfig({
  // Prevent Vite from loading private root or backend dotenv files for E2E.
  envDir: false,
  plugins: [react(), tailwindcss(), localApiStub()],
  server: {
    host: '127.0.0.1',
    port: 4174,
    strictPort: true,
  },
})
