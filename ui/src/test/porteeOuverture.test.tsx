import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'

import type { LibraryItem } from '../api'
import type { Reunion } from '../lib/meetings'
import { AskPanel } from '../components/AskPanel'
import { DedupPanel } from '../components/DedupPanel'
import { MeetingView } from '../components/MeetingView'
import { SearchPanel } from '../components/SearchPanel'

/**
 * La portée d'ouverture d'un document vient du DOCUMENT, jamais du projet
 * courant.
 *
 * Recherche et Assistant portent un bouton « chercher dans tous mes projets »
 * dont l'état est LOCAL : ni l'URL ni le parent ne le connaissent. Un résultat
 * ouvert depuis cette portée élargie appartient donc à n'importe quel projet,
 * et rouvrir avec la portée du projet courant le rendrait introuvable.
 *
 * ## Pourquoi ces tests n'espionnent PAS `../api`
 *
 * Toutes les autres suites mockent le module client. Ici ce serait creux : un
 * espion sur `getTranscript` prouve qu'un composant a passé un argument, pas
 * que l'URL le porte — or c'est l'URL que le backend borne. On stube donc
 * `fetch` et on lit ce qui part réellement sur le réseau.
 *
 * ## Le piège auquel ces tests répondent
 *
 * Tout ici est classé dans un SECOND projet, jamais dans celui d'où l'on
 * regarde. Un test dont le document et la portée courante coïncideraient
 * passerait au vert avec ou sans correctif : `view_scope=<courant>` serait
 * alors le bon, par accident.
 */

/** Là d'où l'on regarde. Aucun document de ce fichier n'y est classé. */
const PROJET_COURANT = '01PROJETCOURANT0000000000'
/** Là où vivent les documents qu'on ouvre. */
const PROJET_DU_DOCUMENT = '01PROJETDOCUMENT000000000'

type Appel = { url: string; methode: string }

/** Serveur de test minimal : une réponse par CHEMIN, et la trace des URL. */
function serveur(reponses: Record<string, unknown>): Appel[] {
  const appels: Appel[] = []
  vi.stubGlobal('fetch', vi.fn(async (entree: RequestInfo | URL, init?: RequestInit) => {
    const url = String(entree)
    appels.push({ url, methode: (init?.method ?? 'GET').toUpperCase() })
    return new Response(JSON.stringify(reponses[url.split('?')[0]] ?? {}), { status: 200 })
  }))
  return appels
}

/** L'URL du premier appel visant ce chemin avec cette méthode. */
function urlDe(appels: Appel[], chemin: string, methode = 'GET'): string | undefined {
  return appels.find((a) => a.url.split('?')[0] === chemin && a.methode === methode)?.url
}

afterEach(() => { vi.unstubAllGlobals() })

function documentDe(name: string, projectId: string | null): LibraryItem {
  return {
    name, id: `${name}-id`, meeting_id: 'reunion-b', project_id: projectId,
    title: 'Comité B', date: '2026-07-21', duration: '00:10:00', language: 'fr',
    engine: 'whisper', source: 'reunion-b.mp4', project: 'Projet B',
    diarization: false, profiled: false, n_speakers: null, type: null,
    source_transcript: null, source_document_id: null, warnings: 0,
    size: 100, modified: 1_760_000_000,
  }
}

function reunionDe(transcript: LibraryItem): Reunion {
  return {
    cle: 'reunion-b', source: 'reunion-b.mp4', titre: 'Comité B', date: '2026-07-21',
    duree: '00:10:00', langue: 'fr', projet: 'Projet B', nSpeakers: null,
    avertissements: 0, transcripts: [transcript], comptesRendus: [], retenu: transcript,
  }
}

const DETAIL = { meta: {}, body: '[00:12] Décision prise', content: '[00:12] Décision prise' }

/**
 * Le bug DÉJÀ VIVANT en production : `/archive-plan` et le DELETE sont bornés
 * côté backend depuis leur écriture, et la vue réunion les appelait sans
 * portée. La portée vide retombe sur la boîte de réception — archiver un
 * document classé ailleurs rendait donc 404, aujourd'hui, à l'écran.
 */
it('Bibliothèque : archiver un document classé AILLEURS porte la portée du document', async () => {
  const user = userEvent.setup()
  const appels = serveur({
    '/api/transcript/reunion-b.md': { name: 'reunion-b.md', ...DETAIL },
    '/api/transcript/reunion-b.md/archive-plan': {
      requested: 'reunion-b.md',
      documents: [{ name: 'reunion-b.md', title: 'Comité B', type: null, document_id: 'doc-b' }],
    },
  })
  vi.spyOn(window, 'confirm').mockReturnValue(true)

  render(
    <MeetingView
      reunion={reunionDe(documentDe('reunion-b.md', PROJET_DU_DOCUMENT))}
      onBack={vi.fn()}
      onChanged={vi.fn()}
    />,
  )
  await waitFor(() => expect(urlDe(appels, '/api/transcript/reunion-b.md')).toBeDefined())

  await user.click(screen.getByRole('button', { name: 'Archiver' }))
  await waitFor(() => expect(urlDe(appels, '/api/transcript/reunion-b.md', 'DELETE')).toBeDefined())

  expect(urlDe(appels, '/api/transcript/reunion-b.md/archive-plan'))
    .toBe(`/api/transcript/reunion-b.md/archive-plan?view_scope=${PROJET_DU_DOCUMENT}`)
  expect(urlDe(appels, '/api/transcript/reunion-b.md', 'DELETE'))
    .toBe(`/api/transcript/reunion-b.md?view_scope=${PROJET_DU_DOCUMENT}`)
  // Et la lecture elle-même, qui passe par le même contrat.
  expect(urlDe(appels, '/api/transcript/reunion-b.md'))
    .toBe(`/api/transcript/reunion-b.md?view_scope=${PROJET_DU_DOCUMENT}`)
})

function resultat(projectId: string | null) {
  return [{
    name: 'ailleurs.md', document_id: 'doc-b', meeting_id: 'reunion-b',
    project_id: projectId, project: 'Projet B', title: 'Comité B',
    source: 'reunion-b.mp4', language: 'fr', duration: null, diarization: false,
    profiled: false, n_speakers: null, ts: '00:12', snippet: 'Décision prise', rang: 1,
  }]
}

it('Recherche : un résultat trouvé en portée élargie s’ouvre dans SON projet', async () => {
  const user = userEvent.setup()
  const appels = serveur({
    '/api/search': resultat(PROJET_DU_DOCUMENT),
    '/api/transcript/ailleurs.md': { name: 'ailleurs.md', ...DETAIL },
  })

  render(<SearchPanel viewScope={PROJET_COURANT} scopeLabel="Projet A" />)
  // L'élargissement est un état LOCAL au panneau : le parent l'ignore.
  await user.click(screen.getByRole('button', { name: 'Chercher dans tous mes projets' }))
  await user.type(screen.getByRole('searchbox', { name: 'Recherche détaillée' }), 'décision')

  await user.click(await screen.findByRole('button', { name: /Ouvrir le passage/ }))
  await waitFor(() => expect(urlDe(appels, '/api/transcript/ailleurs.md')).toBeDefined())
  expect(urlDe(appels, '/api/transcript/ailleurs.md'))
    .toBe(`/api/transcript/ailleurs.md?view_scope=${PROJET_DU_DOCUMENT}`)
})

it('Recherche : un résultat SANS projet déclaré s’ouvre en portée « all »', async () => {
  // Seule portée qui contient à coup sûr un document qu'on ne sait pas classer.
  const user = userEvent.setup()
  const appels = serveur({
    '/api/search': resultat(null),
    '/api/transcript/ailleurs.md': { name: 'ailleurs.md', ...DETAIL },
  })

  render(<SearchPanel viewScope={PROJET_COURANT} scopeLabel="Projet A" />)
  await user.type(screen.getByRole('searchbox', { name: 'Recherche détaillée' }), 'décision')

  await user.click(await screen.findByRole('button', { name: /Ouvrir le passage/ }))
  await waitFor(() => expect(urlDe(appels, '/api/transcript/ailleurs.md')).toBeDefined())
  expect(urlDe(appels, '/api/transcript/ailleurs.md'))
    .toBe('/api/transcript/ailleurs.md?view_scope=all')
})

it('Assistant : une source citée s’ouvre dans le projet qu’elle déclare', async () => {
  const user = userEvent.setup()
  const appels = serveur({
    '/api/ask': {
      answer: 'Décision confirmée [1].',
      sources: [{
        n: 1, name: 'ailleurs.md', title: 'Comité B', ts: '00:12', rang: 1,
        excerpt: 'Décision prise', document_id: 'doc-b', meeting_id: 'reunion-b',
        project_id: PROJET_DU_DOCUMENT, project: 'Projet B',
      }],
    },
    '/api/transcript/ailleurs.md': { name: 'ailleurs.md', ...DETAIL },
  })

  render(<AskPanel viewScope={PROJET_COURANT} scopeLabel="Projet A" />)
  await user.type(
    screen.getByPlaceholderText('Pose une question sur tes transcripts…'), 'Quelle décision ?')
  await user.click(screen.getByRole('button', { name: 'Chercher dans tous mes projets' }))
  await user.click(screen.getByRole('button', { name: 'Demander' }))

  await user.click(await screen.findByRole('button', { name: 'Ouvrir la source 1' }))
  await waitFor(() => expect(urlDe(appels, '/api/transcript/ailleurs.md')).toBeDefined())
  expect(urlDe(appels, '/api/transcript/ailleurs.md'))
    .toBe(`/api/transcript/ailleurs.md?view_scope=${PROJET_DU_DOCUMENT}`)
})

it('Doublons : un membre listé sur tout le corpus s’ouvre en portée « all »', async () => {
  // `/api/duplicates` ne rend pas le projet de chaque membre ; la portée du
  // LISTAGE est donc la seule connue, et elle est exacte — le backend a filtré
  // la liste avec elle. Sans elle, on retomberait sur la boîte de réception.
  const user = userEvent.setup()
  const appels = serveur({
    '/api/duplicates': [{
      keep: 'gardee.md', reason: 'le plus récent', count: 2, source: 'reunion-b.mp4',
      members: [
        {
          name: 'gardee.md', title: 'Comité B', source: 'reunion-b.mp4', language: 'fr',
          duration: '00:10:00', diarization: false, profiled: false, n_speakers: null,
          generated_at: '2026-07-23', keep: true,
        },
        {
          name: 'ancienne.md', title: 'Comité B', source: 'reunion-b.mp4', language: 'fr',
          duration: '00:10:00', diarization: false, profiled: false, n_speakers: null,
          generated_at: '2026-07-22', keep: false,
        },
      ],
    }],
    '/api/transcript/ancienne.md': { name: 'ancienne.md', ...DETAIL },
  })

  render(<DedupPanel viewScope="all" embedded />)

  await user.click(await screen.findByText('ancienne.md'))
  await waitFor(() => expect(urlDe(appels, '/api/transcript/ancienne.md')).toBeDefined())
  expect(urlDe(appels, '/api/transcript/ancienne.md'))
    .toBe('/api/transcript/ancienne.md?view_scope=all')
})
