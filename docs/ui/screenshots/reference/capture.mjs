// Capture de la baseline visuelle de l'interface — pilote la VRAIE application.
//
// Prérequis (voir README.md) :
//   1. backend :  .venv/bin/python -m uvicorn api.main:app --port 8771
//   2. front   :  npm --prefix ui run dev -- --port 8780 --strictPort
//   3. deps    :  npm --prefix ui i -D playwright && npx --prefix ui playwright install chromium
//              (ou PLAYWRIGHT=<dossier du paquet playwright déjà installé ailleurs>)
//   4. capture :  node docs/ui/screenshots/reference/capture.mjs
//
// Playwright est résolu depuis ui/node_modules (le script vit hors de ui/ pour
// ne pas être linté ni bundlé), sauf si PLAYWRIGHT désigne un paquet déjà
// présent sur la machine. Aucune donnée backend n'est fabriquée : le script ne
// fait que naviguer et photographier des états réels.

import { createRequire } from 'node:module'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { mkdirSync } from 'node:fs'
import path from 'node:path'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const ROOT = path.resolve(HERE, '../../../..') // racine du dépôt (worktree)

const BASE = process.env.BASE || 'http://localhost:8780'
// Identifiants propres au corpus capturé : aucun défaut, pour ne jamais
// photographier par mégarde une bibliothèque autre que celle choisie.
// Réunion (portée « tous les projets ») utilisée pour l'espace document.
const MEETING = process.env.MEETING
// Projet réellement vide, pour l'état vide de la bibliothèque.
const EMPTY_PROJECT = process.env.EMPTY_PROJECT
// Requête de la capture de recherche : un thème présent dans le corpus de démo
// (les réunions AMI portent sur la conception d'une télécommande).
const QUERY = process.env.QUERY || 'remote control'
// Sous-ensemble d'écrans à refaire, par nom (`ONLY=08-duplicates,09-trash`) :
// certains états (doublon, corbeille) se fabriquent APRÈS les autres captures,
// pour ne pas apparaître dans la bibliothèque ni dans la recherche.
const ONLY = (process.env.ONLY || '').split(',').map((s) => s.trim()).filter(Boolean)
const retenu = (t) => ONLY.length === 0 || ONLY.includes(t.name)
// La connexion est obligatoire : un compte d'essai de la racine capturée.
const IDENTIFIANT = process.env.IDENTIFIANT
const MOT_DE_PASSE = process.env.MOT_DE_PASSE
if (!MEETING || !EMPTY_PROJECT || !IDENTIFIANT || !MOT_DE_PASSE) {
  process.stderr.write('MEETING, EMPTY_PROJECT, IDENTIFIANT et MOT_DE_PASSE sont requis.\n')
  process.exit(2)
}

const require = createRequire(pathToFileURL(path.join(ROOT, 'ui', 'package.json')))
const { chromium } = require(process.env.PLAYWRIGHT || 'playwright')

const DESKTOP = { width: 1440, height: 900 }
const MOBILE = { width: 390, height: 844 }

// Neutralise animations, caret et défilement doux : captures reproductibles.
const FREEZE = `*,*::before,*::after{transition:none!important;animation:none!important;caret-color:transparent!important}
html{scroll-behavior:auto!important}`

const meetingUrl = (onglet) => `/?vue=library&projet=all&reunion=${MEETING}&onglet=${onglet}`

// Chaque cible : nom de fichier, URL (contrat d'état d'App), sélecteur d'attente.
const DESKTOP_ALL = [
  { name: '01-transcribe', url: '/?vue=transcribe', wait: '.transcribe-hero' },
  { name: '02-library', url: '/?vue=library&projet=all', wait: '.meeting-row' },
  { name: '03-meeting-report', url: meetingUrl('cr'), wait: '.report-panel .markdown-view' },
  { name: '04-transcript-player', url: meetingUrl('transcript'), wait: '.transcript-lecture' },
  { name: '05-search', url: `/?vue=search&projet=all&q=${encodeURIComponent(QUERY)}`, wait: '.search-meeting, .evidence-empty' },
  { name: '06-assistant', url: '/?vue=ask&projet=all', wait: '.ask-intro, .rag-result' },
  { name: '07-voices', url: '/?vue=voices&projet=all', wait: '.voices-master-detail' },
  { name: '08-duplicates', url: '/?vue=dedup&projet=all', wait: '.app-main' },
  { name: '09-trash', url: '/?vue=trash&projet=all', wait: '.app-main' },
  { name: '10-maintenance', url: '/?vue=maintenance&projet=all', wait: '.app-main' },
  { name: '11-versions', url: meetingUrl('versions'), wait: '.versions-panel' },
  { name: '12-information', url: meetingUrl('info'), wait: '.info-panel' },
  { name: '13-library-empty', url: `/?vue=library&projet=${EMPTY_PROJECT}`, wait: '.kw-empty' },
  { name: '15-share', url: meetingUrl('cr'), wait: '.meeting-scope-actions',
    action: async (page) => {
      await page.getByRole('button', { name: 'Partager' }).click()
      await page.waitForSelector('.partage-dialog', { timeout: 9000 })
    } },
]

// Avant toute connexion : l'écran de connexion, champs vides.
const CONNEXION = [{ name: '00-connexion', url: '/', wait: '.auth-card' }]

const DARK_KEYS = ['01-transcribe', '02-library', '03-meeting-report', '04-transcript-player']

const MOBILE_LIST = [
  // La navigation primaire est désormais un DOCK flottant, visible sur chaque
  // écran : plus de tiroir à ouvrir. Le dock se photographie donc en situation.
  { name: '00-dock', url: '/?vue=library&projet=all', wait: '.dock' },
  { name: '01-transcribe', url: '/?vue=transcribe', wait: '.transcribe-hero' },
  { name: '02-library', url: '/?vue=library&projet=all', wait: '.meeting-row' },
  { name: '03-meeting-report', url: meetingUrl('cr'), wait: '.report-panel' },
  { name: '04-transcript-player', url: meetingUrl('transcript'), wait: '.wave, .transcript-texte' },
]

function outDir(...parts) {
  const dir = path.join(HERE, ...parts)
  mkdirSync(dir, { recursive: true })
  return dir
}

async function capture(context, list, dir) {
  list = list.filter(retenu)
  if (list.length === 0) return
  const page = await context.newPage()
  for (const t of list) {
    await page.goto(BASE + t.url, { waitUntil: 'networkidle', timeout: 30000 }).catch(() => {})
    if (t.wait) await page.waitForSelector(t.wait, { timeout: 9000 }).catch(() => {})
    await page.addStyleTag({ content: FREEZE }).catch(() => {})
    if (t.action) await t.action(page).catch(() => {})
    await page.mouse.move(0, 0)
    await page.waitForTimeout(500)
    await page.screenshot({ path: path.join(dir, `${t.name}.png`) })
    process.stdout.write(`  ✓ ${path.relative(HERE, dir)}/${t.name}.png\n`)
  }
  await page.close()
}

async function newContext(browser, viewport, theme, connecte = true) {
  const ctx = await browser.newContext({ viewport, deviceScaleFactor: 1 })
  if (connecte) {
    // Le cookie de session posé par l'API vaut pour tout le contexte.
    const r = await ctx.request.post(BASE + '/api/auth/connexion',
      { data: { identifiant: IDENTIFIANT, mot_de_passe: MOT_DE_PASSE } })
    if (!r.ok()) throw new Error(`connexion refusée (${r.status()})`)
  }
  await ctx.addInitScript((th) => {
    try {
      localStorage.setItem('memoglyph.theme', th)
      localStorage.setItem('memoglyph.lang', 'fr')
    } catch { /* stockage indisponible */ }
  }, theme)
  return ctx
}

async function main() {
  const browser = await chromium.launch()
  try {
    // Écran de connexion, sans session.
    let ctx = await newContext(browser, DESKTOP, 'light', false)
    await capture(ctx, CONNEXION, outDir('desktop', 'light'))
    await ctx.close()

    // Desktop clair — toutes les destinations.
    ctx = await newContext(browser, DESKTOP, 'light')
    await capture(ctx, DESKTOP_ALL, outDir('desktop', 'light'))
    await ctx.close()

    // Desktop sombre — écrans produit clés.
    ctx = await newContext(browser, DESKTOP, 'dark')
    await capture(ctx, DESKTOP_ALL.filter((t) => DARK_KEYS.includes(t.name)), outDir('desktop', 'dark'))
    await ctx.close()

    // Mobile clair — navigation + écrans clés.
    ctx = await newContext(browser, MOBILE, 'light')
    await capture(ctx, MOBILE_LIST, outDir('mobile', 'light'))
    await ctx.close()
  } finally {
    await browser.close()
  }
  process.stdout.write('Baseline capturée.\n')
}

main().catch((e) => { process.stderr.write(String(e) + '\n'); process.exit(1) })
