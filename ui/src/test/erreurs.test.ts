import { afterEach, expect, it, vi } from 'vitest'

import { ErreurAvecCode, ErreurHttp, EnvoiRefuseError, getCloudUltra, lancerCompteRendu, submitTranscribe } from '../api'
import type { TranscribeOptions } from '../api'
import { phraseDErreur } from '../lib/erreurs'
import { translate } from '../lib/i18n'
import type { Lang } from '../lib/i18n'
import { CATALOGUES } from '../lib/messages'

afterEach(() => vi.unstubAllGlobals())

const traducteur = (lang: Lang) => (cle: Parameters<typeof translate>[1], params?: Parameters<typeof translate>[2]) => translate(lang, cle, params)

function serveurRepond(statut: number, corps: unknown) {
  const texte = typeof corps === 'string' ? corps : JSON.stringify(corps)
  vi.stubGlobal('fetch', vi.fn(async () => new Response(texte, { status: statut })))
}


// ---------- la phrase d'une erreur ----------

it('un code connu donne la phrase du catalogue de la langue choisie, jamais celle du serveur', () => {
  const erreur = new ErreurAvecCode('auth.login.invalid', 'Identifiant ou mot de passe incorrect. (phrase du serveur)')
  expect(phraseDErreur(traducteur('fr'), 'fr', erreur, 'REPLI')).toBe('Identifiant ou mot de passe incorrect.')
  expect(phraseDErreur(traducteur('en'), 'en', erreur, 'FALLBACK')).toBe('Incorrect username or password.')
})

it('un code absent du catalogue tombe sur le repli du contexte, jamais sur la phrase du serveur', () => {
  const erreur = new ErreurAvecCode('code.inconnu.du.catalogue', 'Une phrase française du serveur')
  expect(phraseDErreur(traducteur('fr'), 'fr', erreur, 'Le repli de cet écran.')).toBe('Le repli de cet écran.')
  expect(phraseDErreur(traducteur('en'), 'en', erreur, 'This screen’s fallback.')).toBe('This screen’s fallback.')
})

it('une erreur sans code (réseau, serveur, exception) tombe sur le repli : jamais « HTTP », jamais le texte de l’exception', () => {
  const t = traducteur('fr')
  for (const erreur of [new Error('HTTP 500: {"detail":"base indisponible"}'), new TypeError('Failed to fetch'),
    'HTTP 503', { code: 'auth.login.invalid' }, null, undefined]) {
    const phrase = phraseDErreur(t, 'fr', erreur, 'Le repli.')
    expect(phrase).toBe('Le repli.')
    expect(phrase).not.toMatch(/HTTP|base indisponible|Failed to fetch/)
  }
})


// ---------- le client garde le code, sans changer ce que les écrans lisent déjà ----------

it('un refus du serveur qui porte un code lève une erreur avec ce code, et son message reste la phrase du serveur', async () => {
  serveurRepond(401, { detail: 'Connexion requise.', code: 'auth.required' })
  const erreur = await getCloudUltra().catch((e: unknown) => e)
  expect(erreur).toBeInstanceOf(ErreurAvecCode)
  expect((erreur as ErreurAvecCode).code).toBe('auth.required')
  expect((erreur as Error).message).toBe('Connexion requise.')
})

it('un refus sans code reste une erreur ordinaire (sa phrase), et un corps illisible garde le statut', async () => {
  serveurRepond(404, { detail: 'Jeton inconnu' })
  const ordinaire = await getCloudUltra().catch((e: unknown) => e)
  expect(ordinaire).not.toBeInstanceOf(ErreurAvecCode)
  expect((ordinaire as Error).message).toBe('Jeton inconnu')

  serveurRepond(502, 'Bad Gateway')
  expect(((await getCloudUltra().catch((e: unknown) => e)) as Error).message).toBe('HTTP 502')
})

it('une régénération refusée avec un code garde le texte « HTTP <statut> » que l’écran lit pour choisir sa phrase', async () => {
  // L'écran de régénération lit /HTTP (\d{3})/ dans l'erreur : ajouter un code au corps ne doit pas le priver du statut.
  for (const [statut, code] of [[429, 'resources.busy'], [403, 'cloud.refused']] as const) {
    serveurRepond(statut, { detail: 'phrase du serveur', code })
    const erreur = await lancerCompteRendu('a.md', 'p', 'mistral').catch((e: unknown) => e)
    expect(/HTTP (\d{3})/.exec(String(erreur))?.[1]).toBe(String(statut))
  }
})

it('un envoi refusé avec un code garde son statut et la phrase du serveur dans l’erreur typée', async () => {
  serveurRepond(400, { detail: 'Un compte rendu exige les horodatages', code: 'transcribe.timestamps.required' })
  const options = { engine: 'whisper', language: 'fr', destinationProjectId: 'p' } as unknown as TranscribeOptions
  const erreur = await submitTranscribe(new File(['a'], 'a.wav'), options).catch((e: unknown) => e)
  expect(erreur).toBeInstanceOf(EnvoiRefuseError)
  expect((erreur as EnvoiRefuseError).statut).toBe(400)
  expect((erreur as EnvoiRefuseError).detail).toBe('Un compte rendu exige les horodatages')
})


// ---------- le catalogue ----------

it('chaque phrase d’erreur existe dans les deux langues et ne porte aucun espace réservé de texte brut', () => {
  const cles = Object.keys(CATALOGUES.fr).filter((c) => c.startsWith('erreur.'))
  expect(cles.length).toBeGreaterThanOrEqual(29)
  expect(Object.keys(CATALOGUES.en).filter((c) => c.startsWith('erreur.')).sort()).toEqual([...cles].sort())
  for (const cle of cles) {
    for (const lang of ['fr', 'en'] as const) {
      expect(CATALOGUES[lang][cle as keyof typeof CATALOGUES.fr]).not.toMatch(/\{(error|erreur|detail|cause|message)\}/)
    }
  }
})


// ---------- `j` : la plupart des écrans passent par lui ----------

it('une réponse en erreur lève une erreur HTTP typée : le message reste « HTTP <statut> : <corps> », le code et la phrase se lisent', async () => {
  serveurRepond(429, { detail: 'phrase du serveur', code: 'resources.busy' })
  const erreur = await lancerCompteRendu('a.md', 'p', 'mistral').catch((e: unknown) => e)
  expect(erreur).toBeInstanceOf(ErreurHttp)
  expect((erreur as ErreurHttp).statut).toBe(429)
  expect((erreur as ErreurHttp).code).toBe('resources.busy')
  expect((erreur as ErreurHttp).detail).toBe('phrase du serveur')
  expect((erreur as Error).message).toBe('HTTP 429: {"detail":"phrase du serveur","code":"resources.busy"}')
})

it('un corps sans code, vide ou illisible lève la même erreur, sans code ni phrase inventés', async () => {
  serveurRepond(500, { detail: 'sans code' })
  const sansCode = await lancerCompteRendu('a.md', 'p', 'mistral').catch((e: unknown) => e) as ErreurHttp
  expect([sansCode.statut, sansCode.code, sansCode.detail]).toEqual([500, null, 'sans code'])

  serveurRepond(502, 'Bad Gateway')
  const illisible = await lancerCompteRendu('a.md', 'p', 'mistral').catch((e: unknown) => e) as ErreurHttp
  expect([illisible.statut, illisible.code, illisible.detail, illisible.message]).toEqual([502, null, null, 'HTTP 502: Bad Gateway'])
})

it('la phrase d’une erreur HTTP à code connu vient du catalogue ; sans code connu, c’est le repli', () => {
  const t = traducteur('fr')
  const connue = new ErreurHttp(429, '{"detail":"x","code":"resources.busy"}')
  expect(phraseDErreur(t, 'fr', connue, 'REPLI')).toBe('Un autre traitement est en cours : réessayer à sa fin.')
  expect(phraseDErreur(t, 'fr', new ErreurHttp(429, '{"detail":"x","code":"code.inconnu.du.catalogue"}'), 'REPLI')).toBe('REPLI')
  expect(phraseDErreur(t, 'fr', new ErreurHttp(500, 'boum'), 'REPLI')).toBe('REPLI')
})

it('un envoi refusé avec un code garde ce code dans l’erreur typée, et null sinon', async () => {
  const options = { engine: 'whisper', language: 'fr', destinationProjectId: 'p' } as unknown as TranscribeOptions
  serveurRepond(400, { detail: 'phrase', code: 'transcribe.timestamps.required' })
  const codee = await submitTranscribe(new File(['a'], 'a.wav'), options).catch((e: unknown) => e) as EnvoiRefuseError
  expect(codee.code).toBe('transcribe.timestamps.required')
  serveurRepond(400, { detail: 'phrase' })
  const sans = await submitTranscribe(new File(['a'], 'a.wav'), options).catch((e: unknown) => e) as EnvoiRefuseError
  expect(sans.code).toBeNull()
})
