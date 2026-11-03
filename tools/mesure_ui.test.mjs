// Test de la partie PURE de mesure_ui.mjs, sans navigateur : `node --test tools/mesure_ui.test.mjs`.
// Hors `make check` et hors CI publique (aucun navigateur à télécharger).
import test from 'node:test'
import assert from 'node:assert/strict'
import { mesurer, mesurerTranscript, CIBLE_MIN } from './mesure_ui.mjs'

const el = (nom, w, h, extra = {}) => ({ nom, role: 'button', x: 0, y: 0, w, h, ...extra })

test('une cible de 44 x 44 passe, une de 43 ne passe pas', () => {
  const m = mesurer({ elements: [el('ok', CIBLE_MIN, CIBLE_MIN), el('petit', 43, 44), el('bas', 44, 30)], largeurDocument: 390, largeurFenetre: 390 })
  assert.deepEqual(m.petitesCibles.map((c) => c.nom), ['petit', 'bas'])
})

test('un lien dans une phrase est exempté de la taille, pas du nom', () => {
  const m = mesurer({ elements: [el('lien', 60, 18, { enLigne: true }), el('', 60, 18, { enLigne: true })], largeurDocument: 390, largeurFenetre: 390 })
  assert.equal(m.petitesCibles.length, 0)
  assert.equal(m.sansNom.length, 1)
})

test('un nom blanc ou absent est signalé', () => {
  const m = mesurer({ elements: [el('  ', 50, 50), el(undefined, 50, 50), el('Réglages', 50, 50)], largeurDocument: 390, largeurFenetre: 390 })
  assert.equal(m.sansNom.length, 2)
})

test('un défilement horizontal se détecte, avec 1 px de tolérance', () => {
  assert.equal(mesurer({ elements: [], largeurDocument: 391, largeurFenetre: 390 }).defilementHorizontal, false)
  assert.equal(mesurer({ elements: [], largeurDocument: 420, largeurFenetre: 390 }).defilementHorizontal, true)
})

test('transcript : le ratio compare le texte à l\'espace libre, et SC-007 exige 90 % à 1440 px', () => {
  const base = { ligneMax: 700, largeurCaractere: 7, largeurDocument: 1440 }
  assert.equal(mesurerTranscript({ ...base, texte: 440, libre: 584, largeurFenetre: 1440 }).conforme1440, false)   // l'état d'avant
  const apres = mesurerTranscript({ ...base, texte: 782, libre: 782, largeurFenetre: 1440 })
  assert.equal(apres.ratio, 1)
  assert.equal(apres.conforme1440, true)
  assert.equal(mesurerTranscript({ ...base, texte: 702, libre: 782, largeurFenetre: 1440 }).conforme1440, false)    // 89,8 %
})

test('transcript : au-delà de 1600 px la ligne doit tenir en environ 120 caractères', () => {
  const m = (ligneMax) => mesurerTranscript({ texte: 840, libre: 900, ligneMax, largeurCaractere: 7, largeurFenetre: 1920, largeurDocument: 1920 })
  assert.equal(m(840).nbCaracteresMax, 120)
  assert.equal(m(840).limiteAtteinte, true)
  assert.equal(m(1300).limiteAtteinte, false)       // 185 caractères : pas de limite
  assert.equal(mesurerTranscript({ texte: 1, libre: 1, ligneMax: 100, largeurCaractere: 7, largeurFenetre: 1440, largeurDocument: 1440 }).limiteAtteinte, null)
})

test('transcript : un défilement horizontal se détecte aussi', () => {
  assert.equal(mesurerTranscript({ texte: 300, libre: 320, ligneMax: 300, largeurCaractere: 7, largeurFenetre: 390, largeurDocument: 520 }).defilementHorizontal, true)
})
