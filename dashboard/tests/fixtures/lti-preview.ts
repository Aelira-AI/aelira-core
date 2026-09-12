/** Local synthetic LTI view: bun tests/fixtures/lti-preview.ts */
import { resolve } from 'node:path';
import process from 'node:process';
import { createServer } from 'vite';
import react from '@vitejs/plugin-react';

const root = resolve(import.meta.dirname, '../..');
const data = resolve(import.meta.dirname, 'lti-preview-data.ts');
const server = await createServer({
  root,
  configFile: false,
  plugins: [react()],
  resolve: {
    alias: [
      { find: /^.*\/hooks\/useLTISession$/, replacement: data },
      { find: /^.*\/api\/client$/, replacement: data },
      { find: /^.*\/api\/ltiClient$/, replacement: data },
    ],
  },
  server: {
    host: '127.0.0.1', port: Number(process.env.LTI_PREVIEW_PORT || 4321), strictPort: true,
    headers: { 'Content-Security-Policy': "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self' ws://127.0.0.1:4321; form-action 'none'; frame-src 'none'" },
  },
});
await server.listen();
process.stdout.write('Synthetic LTI views: http://127.0.0.1:4321/tests/fixtures/lti-preview.html\n');
