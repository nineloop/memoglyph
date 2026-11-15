/**
 * Un envoi refusé AVANT le démarrage du traitement se dit clairement : « L’envoi a été refusé : » puis la phrase du serveur.
 * Jamais `Error: HTTP …`, jamais d'accolade, jamais « Le traitement s'est interrompu » (rien n'a démarré).
 *
 * Les corps sont ceux du serveur, lus dans son code : 400 (horodatages), 403 du nuage (`detail` seul), 403 d'un modèle gratuit
 * (`{detail, code}`), 409 d'un doublon (`{detail, code, job_id, titre}`, le SEUL 409 de cette route), 413 (taille), 422 (liste
 * de validation), 429 (`detail`). Le doublon et le modèle gratuit gardent leur traitement propre.
 *
 * Les assertions de l'écran portent sur ce que la personne voit (le fichier, le bouton, l'absence de suivi), pas sur le balisage.
 */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  DoublonError, EnvoiRefuseError, ModeleGratuitError, streamJob, submitTranscribe, type AppConfig,
} from '../api'
import { TranscribePanel } from '../components/TranscribePanel'
import { CATALOGUES } from '../lib/messages'

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return { ...actual, submitTranscribe: vi.fn(), streamJob: vi.fn() }
})

const reelle = async () => (await vi.importActual<typeof import('../api')>('../api')).submitTranscribe

const config: AppConfig = {
  engines: ['whisper'], default_engine: 'whisper', default_project: 'Non classé', default_project_id: '01KY243SVP5GA31F6KSBYXEAHR',
  whisper_model: 'whisper-test', llm_model: 'llm-test', max_upload_mb: 2048, supported_extensions: ['.mp3', '.mp4', '.wav'],
  diarization_available: true, llm_available: true,
}

const REFUS_DU_NUAGE = 'Un document ultra-confidentiel ne part pas vers un fournisseur cloud'
const HORODATAGES = 'Un compte rendu exige les horodatages : activez-les ou retirez le compte rendu.'
const TROP = "2 traitement(s) en cours. Réessayez quand l'un sera terminé."
const OPTIONS = { engine: 'whisper', language: '', diarize: false, profile: false, timestamps: true, destinationProjectId: 'p1' }
const AUTRE = 'Transcrire un autre enregistrement'
const LANCER = 'Lancer la transcription'

afterEach(() => vi.unstubAllGlobals())
beforeEach(() => {
  vi.mocked(submitTranscribe).mockReset()
  vi.mocked(streamJob).mockReset()
})

describe("ce que l'API rend pour chaque refus réel", () => {
  const envoyer = async (statut: number, corps: unknown, brut?: string) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(brut ?? JSON.stringify(corps), { status: statut })))
    const envoi = await reelle()
    return envoi(new File(['audio'], 'visio.mp3', { type: 'audio/mpeg' }), OPTIONS).catch((e: unknown) => e)
  }

  it.each([
    [400, { detail: HORODATAGES }, HORODATAGES],
    [403, { detail: REFUS_DU_NUAGE }, REFUS_DU_NUAGE],
    [413, { detail: 'Fichier trop volumineux (limite : 2048 Mo)' }, 'Fichier trop volumineux (limite : 2048 Mo)'],
    [429, { detail: TROP }, TROP],
  ])('un %i devient un refus d\'envoi qui porte la phrase du serveur', async (statut, corps, phrase) => {
    const erreur = await envoyer(statut, corps)
    expect(erreur).toBeInstanceOf(EnvoiRefuseError)
    expect(erreur).toMatchObject({ statut, detail: phrase })
    expect(String(erreur)).toContain(`HTTP ${statut}`)               // le texte interne reste celui d'avant : rien d'autre ne casse
  })

  it('un 422 à liste de validation, un corps vide ou non JSON : un refus SANS phrase exploitable', async () => {
    const liste = await envoyer(422, { detail: [{ type: 'missing', loc: ['body', 'file'], msg: 'Field required' }] })
    expect(liste).toBeInstanceOf(EnvoiRefuseError)
    expect((liste as EnvoiRefuseError).detail).toBeNull()
    const vide = await envoyer(429, null, '')
    expect(vide).toBeInstanceOf(EnvoiRefuseError)
    expect((vide as EnvoiRefuseError).detail).toBeNull()
    const texte = await envoyer(400, null, 'pas du JSON')
    expect((texte as EnvoiRefuseError).detail).toBeNull()
  })

  it('le doublon et le modèle gratuit gardent leur erreur propre', async () => {
    const doublon = await envoyer(409, { detail: 'Ce fichier est déjà en cours de traitement.', code: 'transcribe.duplicate', job_id: 'job-9', titre: 'Point prix' })
    expect(doublon).toBeInstanceOf(DoublonError)
    expect(doublon).not.toBeInstanceOf(EnvoiRefuseError)
    const gratuit = await envoyer(403, { detail: 'Un modèle gratuit ne reçoit pas ce document.', code: 'model.free.restricted' })
    expect(gratuit).toBeInstanceOf(ModeleGratuitError)
    expect(gratuit).not.toBeInstanceOf(EnvoiRefuseError)
  })

  it('une session expirée (401) et une panne (500) ne sont PAS des refus d\'envoi : comportement d\'avant', async () => {
    for (const statut of [401, 500, 503]) {
      const erreur = await envoyer(statut, { detail: 'x' })
      expect(erreur).not.toBeInstanceOf(EnvoiRefuseError)
      expect(String(erreur)).toContain(`HTTP ${statut}`)
    }
  })

  it('un envoi accepté rend l\'identifiant du traitement', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ job_id: 'job-1' }), { status: 200 })))
    const envoi = await reelle()
    expect(await envoi(new File(['audio'], 'visio.mp3'), OPTIONS)).toBe('job-1')
  })
})

function choisir(container: HTMLElement, nom = 'visio.mp3') {
  const input = container.querySelector('input[type="file"]') as HTMLInputElement
  fireEvent.change(input, { target: { files: [new File(['audio'], nom, { type: 'audio/mpeg' })] } })
}

function surLEcran(refus: Error) {
  vi.mocked(submitTranscribe).mockRejectedValue(refus)
  return render(<TranscribePanel config={config} onCompleted={vi.fn()} />)
}

/** Ce que la personne doit voir après un refus : rien n'a démarré, son fichier est toujours là, elle peut renvoyer. */
function rienNaDemarre() {
  expect(screen.queryByText(/interrompu/)).not.toBeInTheDocument()
  expect(screen.queryByText('Envoi sécurisé du fichier…')).not.toBeInTheDocument()
  expect(screen.queryByRole('status', { name: 'Envoi accepté' })).not.toBeInTheDocument()
  expect(screen.getAllByText('visio.mp3')[0]).toBeInTheDocument()
  expect(screen.getByRole('button', { name: LANCER })).toBeEnabled()
}

describe("l'écran dit le refus", () => {
  it.each([
    ['un refus de place (429)', new EnvoiRefuseError(429, TROP, `{"detail":"${TROP}"}`, 'resources.busy'),
      'Un autre traitement est en cours : réessayer à sa fin.'],
    ['un refus du nuage (403)', new EnvoiRefuseError(403, REFUS_DU_NUAGE, `{"detail":"${REFUS_DU_NUAGE}"}`, 'cloud.refused'),
      'Ce document est ultra-confidentiel : il ne part vers aucun fournisseur en ligne sans l’autorisation du compte.'],
    ['des horodatages absents (400)', new EnvoiRefuseError(400, HORODATAGES, `{"detail":"${HORODATAGES}"}`, 'transcribe.timestamps.required'), HORODATAGES],
  ])('%s : « L\'envoi a été refusé : » puis la phrase du catalogue, jamais celle du serveur, sans texte brut', async (_cas, refus, phrase) => {
    const user = userEvent.setup()
    const { container } = surLEcran(refus)
    choisir(container)

    await user.click(screen.getByRole('button', { name: LANCER }))

    const alerte = await screen.findByRole('alert')
    expect(alerte).toHaveTextContent(`L’envoi a été refusé : ${phrase}`)
    expect(alerte).not.toHaveTextContent(/HTTP|Error|[{}]|"detail"/)
    if (refus.detail && refus.detail !== phrase) expect(alerte).not.toHaveTextContent(refus.detail)   // la phrase du serveur ne s'affiche pas
    rienNaDemarre()
  })

  it('un refus à code connu dit la phrase du catalogue, jamais celle du serveur', async () => {
    const user = userEvent.setup()
    const { container } = surLEcran(new EnvoiRefuseError(400, 'phrase-francaise-du-serveur', '{"code":"transcribe.timestamps.required"}',
      'transcribe.timestamps.required'))
    choisir(container)

    await user.click(screen.getByRole('button', { name: LANCER }))

    const alerte = await screen.findByRole('alert')
    expect(alerte).toHaveTextContent('L’envoi a été refusé : Un compte rendu exige les horodatages : activez-les ou retirez le compte rendu.')
    expect(alerte).not.toHaveTextContent(/phrase-francaise-du-serveur/)
  })

  it('un refus SANS code connu dit la phrase générale, jamais la phrase du serveur', async () => {
    const user = userEvent.setup()
    const { container } = surLEcran(new EnvoiRefuseError(400, 'phrase-francaise-du-serveur', '{"detail":"phrase-francaise-du-serveur"}'))
    choisir(container)

    await user.click(screen.getByRole('button', { name: LANCER }))

    const alerte = await screen.findByRole('alert')
    expect(alerte).toHaveTextContent('L’envoi a été refusé. Réessayez dans un moment.')
    expect(alerte).not.toHaveTextContent(/phrase-francaise-du-serveur/)
  })

  it('un refus sans phrase exploitable dit la phrase générale, sans code ni JSON', async () => {
    const user = userEvent.setup()
    const { container } = surLEcran(new EnvoiRefuseError(422, null, '{"detail":[{"msg":"x"}]}'))
    choisir(container)

    await user.click(screen.getByRole('button', { name: LANCER }))

    const alerte = await screen.findByRole('alert')
    expect(alerte).toHaveTextContent("L’envoi a été refusé. Réessayez dans un moment.")
    expect(alerte).not.toHaveTextContent(/HTTP|422|[{}\[\]]/)
    rienNaDemarre()
  })

  it('un modèle gratuit garde SA phrase traduite, sous le titre du refus (rien n\'a démarré)', async () => {
    const user = userEvent.setup()
    const { container } = surLEcran(new ModeleGratuitError())
    choisir(container)

    await user.click(screen.getByRole('button', { name: LANCER }))

    const alerte = await screen.findByRole('alert')
    expect(alerte).toHaveTextContent(CATALOGUES.fr['job.step.error.freeModelRestricted'])
    expect(alerte).toHaveTextContent("L’envoi a été refusé.")
    rienNaDemarre()
  })

  it('un doublon garde SON titre et « Suivre » : la phrase générale ne le remplace pas', async () => {
    const user = userEvent.setup()
    const { container } = surLEcran(new DoublonError('job-9', 'Point prix'))
    choisir(container)

    await user.click(screen.getByRole('button', { name: LANCER }))

    const alerte = await screen.findByRole('alert')
    expect(alerte).toHaveTextContent('Ce fichier est déjà en cours de traitement.')
    expect(alerte).not.toHaveTextContent("L’envoi a été refusé")
  })

  it('une panne (500, réseau) garde le comportement d\'avant : ce n\'est pas un refus', async () => {
    const user = userEvent.setup()
    const { container } = surLEcran(new Error('HTTP 500: boum'))
    choisir(container)

    await user.click(screen.getByRole('button', { name: LANCER }))

    expect(await screen.findByRole('alert')).toHaveTextContent('L’envoi n’a pas pu aboutir. Réessayez dans un moment.')
    expect(document.body.textContent).not.toMatch(/HTTP|boum/)
    expect(screen.queryByText(/L’envoi a été refusé/)).not.toBeInTheDocument()
  })

  it('après un refus, la personne renvoie sans recharger : l\'envoi est accepté et le refus disparaît', async () => {
    const user = userEvent.setup()
    vi.mocked(streamJob).mockReturnValue(vi.fn())
    const { container } = surLEcran(new EnvoiRefuseError(429, TROP, `{"detail":"${TROP}"}`))
    choisir(container)
    await user.click(screen.getByRole('button', { name: LANCER }))
    await screen.findByRole('alert')

    vi.mocked(submitTranscribe).mockResolvedValue('job-2')
    await user.click(screen.getByRole('button', { name: LANCER }))

    await waitFor(() => expect(streamJob).toHaveBeenCalledTimes(1))
    expect(vi.mocked(streamJob).mock.calls[0][0]).toBe('job-2')
    expect(screen.queryByText(/L’envoi a été refusé/)).not.toBeInTheDocument()
  })

  it("choisir un autre fichier efface le refus de l'ancien", async () => {
    const user = userEvent.setup()
    const { container } = surLEcran(new EnvoiRefuseError(429, TROP, `{"detail":"${TROP}"}`))
    choisir(container)
    await user.click(screen.getByRole('button', { name: LANCER }))
    await screen.findByRole('alert')

    choisir(container, 'autre.mp3')

    expect(screen.queryByText(/L’envoi a été refusé/)).not.toBeInTheDocument()
  })
})

describe("un refus pendant qu'un traitement est suivi", () => {
  type Gestionnaires = { onProgress?: (p: number, m: string, etape?: unknown) => void }

  async function envoyerUnFichier() {
    const user = userEvent.setup()
    const arreter = vi.fn()
    const flux: { courant: Gestionnaires } = { courant: {} }
    vi.mocked(submitTranscribe).mockResolvedValue('job-1')
    vi.mocked(streamJob).mockImplementation(((_id: string, h: Gestionnaires) => { flux.courant = h; return arreter }) as never)
    const rendu = render(<TranscribePanel config={config} onCompleted={vi.fn()} />)
    choisir(rendu.container, 'premier.mp3')
    await user.click(screen.getByRole('button', { name: LANCER }))
    await screen.findByRole('button', { name: AUTRE })
    return { user, arreter, flux, ...rendu }
  }

  it.each([
    ['un refus de place', new EnvoiRefuseError(429, TROP, `{"detail":"${TROP}"}`)],
    ['un modèle gratuit', new ModeleGratuitError()],
  ])('%s ne lâche pas le premier traitement : suivi, confirmation et progression restent', async (_cas, refus) => {
    const { user, arreter, flux, container } = await envoyerUnFichier()
    act(() => { flux.courant.onProgress?.(0.42, 'x', { code: 'step.audio.prepare', params: {}, message: 'x' }) })
    choisir(container, 'second.mp3')
    vi.mocked(submitTranscribe).mockRejectedValue(refus)

    await user.click(screen.getByRole('button', { name: AUTRE }))

    const alerte = await screen.findByRole('alert')
    expect(alerte).toHaveTextContent("L’envoi a été refusé")
    expect(arreter).not.toHaveBeenCalled()
    expect(screen.getByRole('status', { name: 'Envoi accepté' })).toBeInTheDocument()
    expect(screen.getByLabelText(/42 %/)).toBeInTheDocument()
    expect(within(screen.getByRole('status', { name: 'Envoi accepté' })).queryByText(/interrompu/)).not.toBeInTheDocument()
    expect(screen.queryByText(/interrompu/)).not.toBeInTheDocument()
  })
})

describe('les textes du refus existent dans les deux langues', () => {
  it.each(['envoi.refus.detail', 'envoi.refus.general', 'envoi.refus.titre'] as const)('%s', (cle) => {
    expect(CATALOGUES.fr[cle]).toBeTruthy()
    expect(CATALOGUES.en[cle]).toBeTruthy()
    expect(CATALOGUES.en[cle]).not.toBe(CATALOGUES.fr[cle])
  })
})
