// Mesures d'accessibilité de l'interface, sur une instance de démo (aucune donnée du dépôt).
//
//   node tools/mesure_ui.mjs <sortie.json> [--base http://localhost:9200] [--compte compte.json] [--ecran /?vue=library] [--ecran ...] [--lang fr|en]
//
// `compte.json` : { "identifiant": "...", "mot_de_passe": "..." } (compte de la démo).
// La partie PURE (`mesurer`) travaille sur des boîtes données : elle est testée sans navigateur par
// `tools/mesure_ui.test.mjs` (hors `make check` et hors CI publique : aucun navigateur à télécharger).
// Le reste est un script manuel, exécuté sur la démo.

export const CIBLE_MIN = 44

/**
 * @param {{ elements: {nom: string, role: string, x: number, y: number, w: number, h: number, enLigne?: boolean}[],
 *           largeurDocument: number, largeurFenetre: number }} entree
 * Une cible EN LIGNE (lien dans une phrase) est exemptée de la taille, comme l'exige WCAG 2.5.8 ;
 * une cible sans nom accessible ne l'est jamais.
 */
export function mesurer({ elements, largeurDocument, largeurFenetre }) {
  const petitesCibles = elements
    .filter((e) => !e.enLigne && (e.w < CIBLE_MIN || e.h < CIBLE_MIN))
    .map((e) => ({ nom: e.nom, role: e.role, largeur: Math.round(e.w), hauteur: Math.round(e.h) }))
  const sansNom = elements.filter((e) => !String(e.nom ?? '').trim()).map((e) => ({ role: e.role, x: Math.round(e.x), y: Math.round(e.y) }))
  return {
    cibles: elements.length,
    petitesCibles,
    sansNom,
    defilementHorizontal: largeurDocument > largeurFenetre + 1,
  }
}

/**
 * La largeur du transcript, à partir des boîtes mesurées.
 *
 * - `texte` : la colonne des passages (`.tour-passages`) ; `libre` : l'espace à côté du panneau des
 *   locuteurs (`.transcript-texte`) ; `ligneMax` : la plus grande largeur d'une ligne.
 * - `ratio` = `texte` / `libre` ; `nbCaracteresMax` : combien de caractères tiennent sur la ligne la plus large
 *   (`ligneMax` / largeur moyenne d'un caractère mesurée), pour vérifier la limite d'environ 120 au-delà de 1600 px.
 */
export function mesurerTranscript({ texte, libre, ligneMax, largeurCaractere, largeurFenetre, largeurDocument }) {
  const ratio = libre > 0 ? texte / libre : 0
  const nbCaracteresMax = largeurCaractere > 0 ? Math.floor(ligneMax / largeurCaractere) : null
  return {
    texte: Math.round(texte), libre: Math.round(libre), ratio: Math.round(ratio * 1000) / 1000,
    nbCaracteresMax,
    defilementHorizontal: largeurDocument > largeurFenetre + 1,
    // ≥ 90 % à 1440 px ; limite d'environ 120 caractères (110 à 130) au-delà de 1600 px.
    conforme1440: largeurFenetre !== 1440 || ratio >= 0.9,
    limiteAtteinte: largeurFenetre > 1600 ? nbCaracteresMax !== null && nbCaracteresMax <= 130 : null,
  }
}

async function main() {
  const [{ readFileSync, writeFileSync }, { createRequire }] = await Promise.all([import('node:fs'), import('node:module')])
  const args = process.argv.slice(2)
  const sortie = args[0]
  if (!sortie) throw new Error('usage : node tools/mesure_ui.mjs <sortie.json> [--base URL] [--compte compte.json] [--ecran URL]... [--lang fr|en]')
  const opt = (nom, defaut) => { const i = args.indexOf(nom); return i >= 0 ? args[i + 1] : defaut }
  const base = opt('--base', 'http://localhost:9200')
  const compte = JSON.parse(readFileSync(opt('--compte', 'compte.json'), 'utf8'))
  const ecrans = args.flatMap((a, i) => (a === '--ecran' ? [args[i + 1]] : []))
  const cibles = ecrans.length ? ecrans : ['/?vue=transcribe', '/?vue=library', '/?vue=search', '/?vue=ask']
  const transcript = args.includes('--transcript')   // `--transcript <url de réunion avec onglet=transcript>`
  const largeurs = (opt('--largeurs', '1440,390')).split(',').map(Number)
  const replie = args.includes('--replie')
  const langue = opt('--lang', '')   // `fr` ou `en` : la langue de l'interface (sinon celle du navigateur)
  const { chromium } = createRequire(new URL('../ui/package.json', import.meta.url))('playwright')
  const navigateur = await chromium.launch()
  const resultat = {}
  try {
    for (const largeur of largeurs) {
      const ctx = await navigateur.newContext({ viewport: { width: largeur, height: 844 } })
      const r = await ctx.request.post(base + '/api/auth/connexion', { data: { identifiant: compte.identifiant, mot_de_passe: compte.mot_de_passe } })
      if (!r.ok()) throw new Error('connexion refusée : ' + r.status())
      if (langue) await ctx.addInitScript((l) => { try { localStorage.setItem('memoglyph.lang', l) } catch {} }, langue)
      const page = await ctx.newPage()
      for (const url of cibles) {
        await page.goto(base + url, { waitUntil: 'networkidle' }).catch(() => {})
        if (transcript) {
          await page.evaluate((r) => { try { localStorage.setItem('memoglyph.panneau.replie.' + r.compte, r.replie ? '1' : '0') } catch {} }, { compte: compte.identifiant, replie })
          await page.reload({ waitUntil: 'networkidle' }).catch(() => {})
          await page.waitForSelector('.tour-passages', { timeout: 15000 }).catch(() => {})
          const brut = await page.evaluate(() => {
            const larg = (sel) => document.querySelector(sel)?.getBoundingClientRect().width ?? 0
            const lignes = [...document.querySelectorAll('.tour-passages .tline')]
            const plus = lignes.reduce((m, e) => Math.max(m, e.getBoundingClientRect().width), 0)
            // Largeur moyenne d'un caractère : mesurée sur le VRAI texte du transcript (pas sur un « x » ni un « 0 »),
            // dans la même police, car « environ 120 caractères » se lit en caractères courants.
            const echantillon = lignes.map((e) => e.textContent ?? '').join(' ').replace(/\s+/g, ' ').slice(0, 400)
            const essai = document.createElement('span'); essai.textContent = echantillon
            essai.style.cssText = 'position:absolute;visibility:hidden;white-space:pre;font:' + getComputedStyle(lignes[0] ?? document.body).font
            document.body.appendChild(essai); const carac = essai.getBoundingClientRect().width / Math.max(1, echantillon.length); essai.remove()
            return { texte: larg('.tour-passages'), libre: larg('.transcript-texte'), ligneMax: plus, largeurCaractere: carac,
                     largeurFenetre: window.innerWidth, largeurDocument: document.documentElement.scrollWidth }
          })
          resultat[`${largeur}${replie ? ' (panneau replié)' : ''}${url}`] = mesurerTranscript(brut)
          continue
        }
        const brut = await page.evaluate(() => {
          const visible = (el) => { const b = el.getBoundingClientRect(); const s = getComputedStyle(el); return b.width > 0 && b.height > 0 && s.visibility !== 'hidden' && s.display !== 'none' }
          const nomDe = (el) => {
            const lab = el.getAttribute('aria-label'); if (lab) return lab
            const par = el.getAttribute('aria-labelledby')
            if (par) return par.split(' ').map((id) => document.getElementById(id)?.textContent ?? '').join(' ')
            if (el.id) { const l = document.querySelector(`label[for="${el.id}"]`); if (l) return l.textContent }
            return el.textContent?.trim() || el.getAttribute('title') || el.getAttribute('placeholder') || ''
          }
          const sel = 'a[href], button, input:not([type=hidden]), select, textarea, [role=button], [role=link], [role=tab], [role=combobox], [role=option]'
          const elements = [...document.querySelectorAll(sel)].filter(visible).map((el) => {
            const b = el.getBoundingClientRect()
            const enLigne = el.tagName === 'A' && getComputedStyle(el).display === 'inline' && !!el.closest('p, li')?.textContent?.replace(el.textContent ?? '', '').trim()
            return { nom: nomDe(el), role: el.getAttribute('role') ?? el.tagName.toLowerCase(), x: b.x, y: b.y, w: b.width, h: b.height, enLigne }
          })
          return { elements, largeurDocument: document.documentElement.scrollWidth, largeurFenetre: window.innerWidth }
        })
        resultat[`${largeur}${url}`] = mesurer(brut)
      }
      await ctx.close()
    }
  } finally { await navigateur.close() }
  writeFileSync(sortie, JSON.stringify(resultat, null, 2))
  for (const [cle, m] of Object.entries(resultat)) {
    if (m.ratio !== undefined) { console.log(`${cle} : texte ${m.texte} px sur ${m.libre} px libres = ${Math.round(m.ratio * 100)} %, ligne max ${m.nbCaracteresMax} car., défilement horizontal : ${m.defilementHorizontal ? 'OUI' : 'non'}`); continue }
    console.log(`${cle} : ${m.cibles} cibles, ${m.petitesCibles.length} sous ${CIBLE_MIN} px, ${m.sansNom.length} sans nom, défilement horizontal : ${m.defilementHorizontal ? 'OUI' : 'non'}`)
  }
}

import { fileURLToPath } from 'node:url'
if (process.argv[1] === fileURLToPath(import.meta.url)) main().catch((e) => { console.error(e.message); process.exit(1) })
