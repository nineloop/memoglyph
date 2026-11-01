import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    setupFiles: './src/test/setup.ts',
    clearMocks: true,
    // Un import `?raw` d'une feuille de style rend son texte (garde des règles de
    // mise en page, que jsdom ne calcule pas) ; les imports CSS ordinaires restent vides.
    css: { include: [/\.css\?raw$/] },
  },
})

