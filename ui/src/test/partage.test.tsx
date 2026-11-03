import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, expect, it, vi } from 'vitest'

import App from '../App'
import type { AppConfig, LibraryItem, Partage, Project, Voice } from '../api'
import {
  ask, creerPartage, getConfig, getEtatAuth, getMeetings, getPartages, getProjects,
  getTranscript, getTrash, getVoices, retirerPartage,
} from '../api'
import { AskPanel } from '../components/AskPanel'
import { LangProvider } from '../components/LangProvider'
import { MeetingView } from '../components/MeetingView'
import { PartageDialog } from '../components/PartageDialog'
import { VoicesPanel } from '../components/VoicesPanel'
import { PartageContext } from '../lib/partage'
import type { Reunion } from '../lib/meetings'

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return {
    ...actual,
    getConfig: vi.fn(), getProjects: vi.fn(), getTrash: vi.fn(), getEtatAuth: vi.fn(),
    getPartages: vi.fn(), creerPartage: vi.fn(), retirerPartage: vi.fn(),
    getMeetings: vi.fn(), getTranscript: vi.fn(), getVoices: vi.fn(), ask: vi.fn(),
  }
})

const INBOX = '01KY243SVP5GA31F6KSBYXEAHR'
const CLIENTS = '01KY243SVQMK79ZY6TVJW2Q3Y0'
const ANCIEN = '01KY243SVQMK79ZY6TVJW2Q3Y1'
const projets: Project[] = [
  { id: INBOX, project: 'Non classé', name: 'Non classé', kind: 'inbox', owner: 'alice', count: 0 },
  { id: CLIENTS, project: 'Clients', name: 'Clients', kind: 'regular', owner: 'alice', count: 2 },
  { id: ANCIEN, project: 'Ancien', name: 'Ancien', kind: 'regular', owner: null, count: 1 },
]
const recus: Partage[] = [
  { id: 'S-P', proprietaire: 'bruno', invite: 'alice', type: 'projet', cible: 'P-BRUNO',
    cree_le: '', nom: 'Chantier' },
  { id: 'S-R', proprietaire: 'bruno', invite: 'alice', type: 'reunion', cible: 'M-BRUNO',
    cree_le: '', nom: 'Revue de chantier' },
]

beforeEach(() => {
  history.replaceState(null, '', '/')
  vi.mocked(getConfig).mockResolvedValue({
    engines: ['whisper'], default_engine: 'whisper', default_project: 'Non classé',
    default_project_id: INBOX, whisper_model: 'w', llm_model: 'l', max_upload_mb: 2048,
    supported_extensions: ['.mp3'], diarization_available: true, llm_available: true,
  } as AppConfig)
  vi.mocked(getProjects).mockResolvedValue(projets)
  vi.mocked(getTrash).mockResolvedValue([])
  vi.mocked(getMeetings).mockResolvedValue([])
  vi.mocked(getEtatAuth).mockResolvedValue({
    installation_requise: false, connecte: true, utilisateur: { identifiant: 'alice', admin: false },
  })
  vi.mocked(getPartages).mockResolvedValue({ donnes: [], recus: [] })
})

/** Le panneau des projets de la Bibliothèque, une fois les projets (et les partages reçus) chargés. */
async function panneau(attendu: RegExp | string = /Clients/) {
  const nav = await screen.findByRole('navigation', { name: 'Projets de la bibliothèque' })
  await within(nav).findByRole('link', { name: attendu })
  return nav
}

/** Consulter un projet comme l'utilisateur : par un lien du panneau. */
async function consulter(user: ReturnType<typeof userEvent.setup>, nom: RegExp | string) {
  const nav = await panneau(nom)
  await user.click(within(nav).getByRole('link', { name: nom }))
}

function dock() {
  return screen.getByRole('navigation', { name: /principale|main/i })
}

// ---------- (1) Partager, voir les partages, retirer ----------

it('le propriétaire partage son projet, voit avec qui, et retire', async () => {
  const user = userEvent.setup()
  const partageCarole: Partage = { id: 'S-1', proprietaire: 'alice', invite: 'carole', type: 'projet',
                                   cible: CLIENTS, cree_le: '', nom: 'Clients' }
  vi.mocked(getPartages).mockResolvedValue({ donnes: [partageCarole], recus: [] })
  vi.mocked(creerPartage).mockResolvedValue({ partage: { ...partageCarole, id: 'S-2', invite: 'bruno' } })
  vi.mocked(retirerPartage).mockResolvedValue({ retire: 'S-1' })
  history.replaceState(null, '', '/?vue=library')   // le bouton vit dans la Bibliothèque
  render(<App />)

  await consulter(user, /Clients/)
  await user.click(await screen.findByRole('button', { name: 'Partager' }))

  const dialogue = await screen.findByRole('dialog', { name: 'Partager le projet « Clients »' })
  expect(await within(dialogue).findByText('carole')).toBeInTheDocument()
  await user.type(within(dialogue).getByLabelText('Identifiant du compte'), '  bruno ')
  await user.click(within(dialogue).getByRole('button', { name: 'Partager' }))
  expect(creerPartage).toHaveBeenCalledWith('projet', CLIENTS, 'bruno')
  await user.click(within(dialogue).getByRole('button', { name: 'Retirer le partage avec carole' }))
  expect(retirerPartage).toHaveBeenCalledWith('S-1')
})

it('le bouton Partager est réservé au propriétaire du projet', async () => {
  const user = userEvent.setup()
  history.replaceState(null, '', '/?vue=library')   // là où le bouton peut paraître
  render(<App />)

  await consulter(user, /Ancien/)                            // sans propriétaire
  expect(screen.queryByRole('button', { name: 'Partager' })).not.toBeInTheDocument()
  await consulter(user, /Tous mes projets/)
  expect(screen.queryByRole('button', { name: 'Partager' })).not.toBeInTheDocument()
})

it('Partager n’apparaît que dans la Bibliothèque, jamais pour « Non classé », même pour un projet vide', async () => {
  const user = userEvent.setup()
  const VIDE = '01KY243SVQMK79ZY6TVJW2Q3Y2'
  vi.mocked(getProjects).mockResolvedValue([
    ...projets, { id: VIDE, project: 'Vide', name: 'Vide', kind: 'regular', owner: 'alice', count: 0 }])
  history.replaceState(null, '', '/?vue=transcribe')
  render(<App />)

  // Transcrire : on choisit une destination, on ne partage rien.
  expect(await screen.findByRole('combobox', { name: 'Ranger dans' })).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Partager' })).not.toBeInTheDocument()
  // Bibliothèque : le projet du compte se partage, même vide ; sa réception, non.
  await user.click(within(dock()).getByRole('button', { name: /Bibliothèque/ }))
  await consulter(user, /Clients/)
  expect(await screen.findByRole('button', { name: 'Partager' })).toBeInTheDocument()
  await consulter(user, /Vide/)
  expect(await screen.findByRole('button', { name: 'Partager' })).toBeInTheDocument()
  await consulter(user, /Non classé/)
  expect(screen.queryByRole('button', { name: 'Partager' })).not.toBeInTheDocument()
})

it('le refus du serveur s’affiche tel qu’il l’écrit', async () => {
  const user = userEvent.setup()
  const refus = 'Cette réunion contient un document ultra-confidentiel : elle ne se partage pas.'
  vi.mocked(creerPartage).mockRejectedValue(new Error(refus))
  render(<LangProvider><PartageDialog type="reunion" cible="M-1" nom="Comité" onClose={vi.fn()} /></LangProvider>)

  const dialogue = await screen.findByRole('dialog', { name: 'Partager la réunion « Comité »' })
  expect(await within(dialogue).findByText('Partagé avec personne pour l’instant.')).toBeInTheDocument()
  await user.type(within(dialogue).getByLabelText('Identifiant du compte'), 'bruno')
  await user.click(within(dialogue).getByRole('button', { name: 'Partager' }))

  expect(await within(dialogue).findByRole('alert')).toHaveTextContent(refus)
})

// ---------- (2) « Partagés avec moi » dans le panneau ----------

it('le panneau propose ce qui est partagé, avec le nom de qui le partage', async () => {
  const user = userEvent.setup()
  vi.mocked(getPartages).mockResolvedValue({ donnes: [], recus })
  history.replaceState(null, '', '/?vue=library')
  render(<App />)

  const nav = await panneau(/Chantier/)
  expect(within(nav).getByRole('heading', { name: 'Partagés avec moi' })).toBeInTheDocument()
  expect(within(nav).getByRole('link', { name: /Chantier.*bruno/ })).toBeInTheDocument()
  // Les réunions reçues une à une se retrouvent dans « tout ce qui est partagé avec moi ».
  await user.click(within(nav).getByRole('link', { name: 'Tout ce qui est partagé avec moi' }))
  await waitFor(() => expect(getMeetings).toHaveBeenCalledWith(
    expect.objectContaining({ viewScope: 'shared' })))
  expect(within(nav).getByRole('link', { name: 'Tout ce qui est partagé avec moi' }))
    .toHaveAttribute('aria-current', 'page')
  expect(screen.getAllByText(/lecture seule/i).length).toBeGreaterThan(0)
})

// ---------- (3) Lecture seule : aucune action d'écriture ----------

it('en lecture seule, les vues du propriétaire quittent la navigation', async () => {
  const user = userEvent.setup()
  vi.mocked(getPartages).mockResolvedValue({ donnes: [], recus })
  render(<App />)

  history.replaceState(null, '', '/?vue=library')
  await consulter(user, /Chantier/)

  for (const vue of ['Corbeille', 'Doublons', 'Usage', 'Maintenance']) {
    expect(within(dock()).queryByRole('button', { name: new RegExp(vue) })).not.toBeInTheDocument()
  }
  expect(within(dock()).getByRole('button', { name: /Bibliothèque/ })).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Partager' })).not.toBeInTheDocument()
  // La transcription écrit toujours dans un projet du compte.
  await user.click(within(dock()).getByRole('button', { name: /Transcrire/ }))
  const champ = await waitFor(() => {
    const trouve = document.querySelector<HTMLInputElement>('input[type="file"]')
    expect(trouve).not.toBeNull()
    return trouve!
  })
  await user.upload(champ, new File(['a'], 'a.mp3', { type: 'audio/mpeg' }))
  // Un projet reçu n'est jamais une destination : « Ranger dans » reste sur Non classé.
  expect(await screen.findByRole('combobox', { name: 'Ranger dans' })).toHaveValue('Non classé')
})

function item(name: string, type: string | null = null): LibraryItem {
  return {
    name, id: `${name}-id`, meeting_id: 'M-BRUNO', project_id: 'P-BRUNO', title: 'Revue',
    date: '2026-07-21', duration: '00:42:00', language: 'fr', engine: 'whisper', source: 'revue.mp4',
    project: 'Chantier', diarization: true, profiled: true, n_speakers: 2, type,
    source_transcript: type === 'report' ? 'revue.md' : null,
    source_document_id: type === 'report' ? 'revue.md-id' : null, warnings: 0, size: 1, modified: 1,
  }
}

const reunion: Reunion = {
  cle: 'M-BRUNO', source: 'revue.mp4', titre: 'Revue', date: '2026-07-21', duree: '00:42:00',
  langue: 'fr', projet: 'Chantier', nSpeakers: 2, avertissements: 0,
  transcripts: [item('revue.md')], comptesRendus: [item('revue-cr.md', 'report')], retenu: item('revue.md'),
}

function enLectureSeule(enfant: React.ReactNode, lectureSeule = true) {
  return (
    <LangProvider>
      <PartageContext.Provider value={{ lectureSeule, peutPartager: () => !lectureSeule }}>
        {enfant}
      </PartageContext.Provider>
    </LangProvider>
  )
}

it('une réunion reçue se lit sans archiver, générer, changer la sensibilité ni partager', async () => {
  const user = userEvent.setup()
  vi.mocked(getTranscript).mockImplementation(async (name) => ({
    name, meta: { sensitivity: 'confidential' }, body: 'Texte', content: '[00:00] Speaker 1 : Bonjour',
  }))
  const { rerender } = render(enLectureSeule(<MeetingView reunion={reunion} onBack={vi.fn()} />))

  await user.click(await screen.findByRole('tab', { name: /Transcript/ }))
  expect((await screen.findAllByText('Texte')).length).toBeGreaterThan(0)
  expect(screen.queryByRole('button', { name: /Compte-rendu/ })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /Archiver/ })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Partager' })).not.toBeInTheDocument()
  await user.click(screen.getByRole('tab', { name: /Informations/ }))
  expect(screen.queryByRole('combobox', { name: 'Sensibilité' })).not.toBeInTheDocument()

  // Le témoin : chez son propriétaire, les mêmes actions sont là.
  rerender(enLectureSeule(<MeetingView reunion={reunion} onBack={vi.fn()} />, false))
  expect(await screen.findByRole('button', { name: 'Partager' })).toBeInTheDocument()
  expect(screen.getByRole('combobox', { name: 'Sensibilité' })).toBeInTheDocument()
})

it('les voix partagées se lisent sans être renommées ni fusionnées', async () => {
  const voix: Voice[] = [1, 2].map((id) => ({
    id, label: `Voix nommée ${id}`, label_source: 'manual', suggestion: null, display: `Voix nommée ${id}`,
    n_members: 1, occurrences: [{ name: 'revue.md', document_id: 'd', title: 'Revue',
                                  speaker_local: `Speaker ${id}`, project_id: 'P-BRUNO', project: 'Chantier' }],
  }))
  vi.mocked(getVoices).mockResolvedValue(voix)
  render(enLectureSeule(<VoicesPanel viewScope="shared" scopeLabel="Partagés avec moi" />))

  expect(await screen.findByRole('heading', { name: 'Voix nommée 1' })).toBeInTheDocument()
  expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /Comparer et fusionner/ })).not.toBeInTheDocument()
})

it('l’assistant interroge ce qui est partagé', async () => {
  const user = userEvent.setup()
  vi.mocked(ask).mockResolvedValue({ answer: 'Réponse', sources: [] })
  render(enLectureSeule(
    <AskPanel viewScope="shared" scopeLabel="Partagés avec moi" llmAvailable />))

  await user.type(screen.getByRole('textbox'), 'Quel budget ?')
  await user.click(screen.getByRole('button', { name: 'Demander' }))

  await waitFor(() => expect(ask).toHaveBeenCalledWith('Quel budget ?', { mode: 'shared' }))
})

// ---------- (4) Rien ne change pour qui n'a rien reçu ----------

it('sans partage reçu, le panneau et la navigation restent ceux d’avant', async () => {
  history.replaceState(null, '', '/?vue=library')
  render(<App />)

  const nav = await panneau()
  await waitFor(() => expect(getPartages).toHaveBeenCalled())
  expect(within(nav).queryByRole('heading', { name: 'Partagés avec moi' })).not.toBeInTheDocument()
  expect(within(nav).getByRole('link', { name: /Tous mes projets/ })).toBeInTheDocument()
  for (const vue of ['Corbeille', 'Doublons', 'Usage']) {
    expect(within(dock()).getByRole('button', { name: new RegExp(vue) })).toBeInTheDocument()
  }
  expect(screen.queryByText('Lecture seule')).not.toBeInTheDocument()
})

// ---------- (4) Échap et nom accessible du dialogue ----------

it.each([
  ['fr', 'Partager la réunion « Comité »'],
  ['en', 'Share meeting “Comité”'],
] as const)('Échap ferme le dialogue quel que soit le focus (%s)', async (lang, titre) => {
  const fermer = vi.fn()
  render(
    <LangProvider initiale={lang}>
      <button type="button">ailleurs</button>
      <PartageDialog type="reunion" cible="M-1" nom="Comité" onClose={fermer} />
    </LangProvider>,
  )
  await screen.findByRole('dialog', { name: titre })
  const ailleurs = screen.getByRole('button', { name: 'ailleurs' })
  ailleurs.focus()
  expect(document.activeElement).toBe(ailleurs)
  await userEvent.keyboard('{Escape}')
  expect(fermer).toHaveBeenCalledTimes(1)
})

it.each([
  ['fr', 'Partager la réunion « Comité »', 'Identifiant du compte'],
  ['en', 'Share meeting “Comité”', 'Account username'],
] as const)('le champ de l’invité porte un nom accessible explicite (%s)', async (lang, titre, nom) => {
  render(
    <LangProvider initiale={lang}>
      <PartageDialog type="reunion" cible="M-1" nom="Comité" onClose={vi.fn()} />
    </LangProvider>,
  )
  const dialogue = await screen.findByRole('dialog', { name: titre })
  const champ = within(dialogue).getByRole('textbox', { name: nom })
  expect(champ).toHaveAttribute('aria-label', nom)
  expect(within(dialogue).getByText(nom)).toBeVisible()
})
