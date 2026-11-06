import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    proxy: {
      // API_URL is set by docker compose; locally the API runs on port 5000.
      '/api': process.env.API_URL || 'http://localhost:5000',
    },
  },
});
