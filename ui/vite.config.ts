import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Ports pilotés par l'environnement (dev.sh les exporte, avec auto-décalage).
// Défauts dans une plage dédiée peu commune pour éviter les conflits.
// Préfixe `MEMOGLYPH_`, seul point de lecture comme `core/config.getenv_compat`.
const PREFIXE = 'MEMOGLYPH_'
const env = (suffix: string): string | undefined => process.env[PREFIXE + suffix]
const UI_PORT = Number(env('UI_PORT')) || 8770
const API_PORT = Number(env('API_PORT')) || 8771
const UI_HOST = env('UI_HOST') || '127.0.0.1'
const IS_LAN = UI_HOST === '0.0.0.0'

// Contrôle d'hôte de Vite (protection anti-DNS-rebinding) : par IP, il laisse
// TOUJOURS passer ; par nom d'hôte, il renvoie 403 « Blocked request » sauf si
// l'hôte figure dans `allowedHosts`. En mode LAN, un testeur atteint souvent ce
// Mac par son nom Bonjour (`macbook.local`) plutôt que par une IP qui bouge à
// chaque bail DHCP — d'où un 403 déroutant. On autorise donc les noms `.local`
// (suffixe) UNIQUEMENT en mode LAN, plus un échappatoire explicite pour un nom
// DNS d'entreprise via MEMOGLYPH_ALLOWED_HOSTS. Le défaut localhost est inchangé
// et l'API reste sur 127.0.0.1 derrière le proxy : rien n'est exposé de plus.
const EXTRA_ALLOWED = (env('ALLOWED_HOSTS') || '')
  .split(',')
  .map((h) => h.trim())
  .filter(Boolean)
const allowedHosts = IS_LAN ? ['.local', ...EXTRA_ALLOWED] : undefined

// Le front appelle /api/* ; Vite proxifie vers le backend FastAPI.
export default defineConfig({
  plugins: [react()],
  server: {
    host: UI_HOST,
    port: UI_PORT,
    strictPort: true,
    // `undefined` = comportement Vite par défaut (localhost/IP), non touché.
    ...(allowedHosts ? { allowedHosts } : {}),
    proxy: {
      '/api': {
        target: `http://127.0.0.1:${API_PORT}`,
        changeOrigin: true,
        // Transmet l'hôte et l'adresse du navigateur (X-Forwarded-*) : l'API
        // contrôle l'en-tête Origin contre le vrai hôte, et compte les
        // tentatives de connexion par adresse, pas par celle du proxy.
        xfwd: true,
      },
    },
  },
})
