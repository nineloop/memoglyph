import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, expect, it, vi } from 'vitest'

import type { LibraryItem, Meeting } from '../api'
import { getLanguages, getMeetings, getTranscript, moveMeeting } from '../api'
import { LibraryPanel } from '../components/LibraryPanel'
import { AudioProvider } from '../audio/AudioProvider'


vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return {
    ...actual,
    getLanguages: vi.fn(),
    getMeetings: vi.fn(),
    getTranscript: vi.fn(),
    moveMeeting: vi.fn(),
  }
})


const transcript: LibraryItem = {
  name: 'meeting.md', id: 'document-id', meeting_id: 'meeting-id', project_id: 'project-id',
  title: 'Réunion test', date: '2026-07-21', duration: '00:10:00', language: 'fr',
  engine: 'whisper', source: 'audio.mp3', project: 'Non classé', diarization: true,
  profiled: false, n_speakers: 2, type: null, source_transcript: null,
  source_document_id: null, warnings: 0, size: 100, modified: 10,
}

const report: LibraryItem = {
  ...transcript,
  name: 'meeting-cr.md', id: 'report-id', title: 'Compte-rendu', type: 'report',
  diarization: false, source_transcript: transcript.name, source_document_id: transcript.id,
}

const meeting: Meeting = {
  id: 'meeting-id', title: transcript.title, project_id: 'project-id', project: 'Non classé',
  source: transcript.source, date: transcript.date, generated_at: null, modified: 10,
  duration: transcript.duration, language: transcript.language, diarization: true,
  profiled: false, n_speakers: 2, transcripts: [transcript], reports: [report],
  preferred_transcript: transcript, quality_reservations: [],
}


beforeEach(() => {
  localStorage.clear() // pas de « reprendre » fuité d'un test à l'autre
  // `clear()` efface aussi la langue de référence posée par `setup.ts` : sans
  // elle, le panneau (désormais i18n) retomberait sur l'`en-US` de jsdom et
  // les libellés français attendus ici deviendraient anglais.
  localStorage.setItem('memoglyph.lang', 'fr')
  vi.mocked(getLanguages).mockResolvedValue(['fr'])
  vi.mocked(getMeetings).mockResolvedValue([meeting])
  vi.mocked(moveMeeting).mockResolvedValue(meeting)
  vi.mocked(getTranscript).mockResolvedValue({
    name: 'meeting-cr.md', meta: { type: 'report' }, body: '# Compte-rendu', content: '# Compte-rendu',
  })
})


const projects = [
  { id: 'project-id', project: 'Non classé', name: 'Non classé', kind: 'inbox' as const, count: 1 },
  { id: 'climate-id', project: 'Climat', name: 'Climat', kind: 'regular' as const, count: 0 },
]


it('aligne les métadonnées et garde les actions destructives dans un menu', async () => {
  const user = userEvent.setup()
  render(
    <LibraryPanel
      reloadKey={0}
      viewScope="project-id"
      projects={projects}
    />,
  )

  expect(await screen.findByRole('heading', { name: 'Bibliothèque' })).toBeInTheDocument()
  expect(screen.getByText('21/07/2026')).toBeInTheDocument()
  expect(screen.getByText('00:10:00')).toBeInTheDocument()
  expect(screen.getByText('FR')).toBeInTheDocument()
  expect(screen.getByText('1 transcript')).toBeInTheDocument()
  expect(screen.getByText('1 CR')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Archiver la réunion' })).not.toBeInTheDocument()

  await user.click(screen.getByRole('button', { name: 'Actions pour « Réunion test »' }))
  expect(screen.getByRole('button', { name: 'Archiver la réunion' })).toBeInTheDocument()
  await waitFor(() => expect(getMeetings).toHaveBeenCalledWith(expect.objectContaining({
    viewScope: 'project-id',
  })))
})


it('propose une action Classer aux réunions de Non classé', async () => {
  const user = userEvent.setup()
  render(<LibraryPanel reloadKey={0} viewScope="project-id" projects={projects} />)

  const classify = await screen.findByRole('button', { name: 'Classer' })
  await user.click(classify)
  const destination = screen.getByRole('combobox', { name: 'Classer « Réunion test »' })
  await user.selectOptions(destination, 'climate-id')

  await waitFor(() => expect(moveMeeting).toHaveBeenCalledWith('meeting-id', 'climate-id', 'project-id'))
})


it('rend l’état vide actionnable et le chargement perceptible', async () => {
  const user = userEvent.setup()
  const onTranscrire = vi.fn()
  vi.mocked(getMeetings).mockResolvedValueOnce([])
  const { rerender } = render(
    <LibraryPanel reloadKey={0} viewScope="project-id" projects={projects} onTranscrire={onTranscrire} />,
  )

  await user.click(await screen.findByRole('button', { name: 'Transcrire un enregistrement' }))
  expect(onTranscrire).toHaveBeenCalledOnce()

  vi.mocked(getMeetings).mockReturnValueOnce(new Promise(() => {}))
  rerender(<LibraryPanel reloadKey={1} viewScope="project-id" projects={projects} />)
  expect(screen.getByLabelText('Chargement des réunions')).toBeInTheDocument()
})


it('présente le nom de source comme titre humain quand le titre stocké est un slug', async () => {
  vi.mocked(getMeetings).mockResolvedValueOnce([{
    ...meeting,
    title: 'weekly-team-sync',
    source: 'Weekly Team Sync.mp4',
  }])
  render(<LibraryPanel reloadKey={0} viewScope="project-id" projects={projects} />)

  expect(await screen.findByText('Weekly Team Sync')).toBeInTheDocument()
  expect(screen.getByText('Weekly Team Sync.mp4')).toBeInTheDocument()
})


it('ouvre directement la réunion du document qui vient d’être créé', async () => {
  const opened = vi.fn()
  render(
    <LibraryPanel
      reloadKey={0}
      viewScope="project-id"
      projects={projects}
      openDocumentName="meeting.md"
      onInitialMeetingOpened={opened}
    />,
  )

  expect(await screen.findByRole('heading', { name: 'Réunion test', level: 1 })).toBeInTheDocument()
  expect(opened).toHaveBeenCalledOnce()
})


it('ne laisse pas une ancienne portée écraser la bibliothèque courante', async () => {
  let resolveOld!: (value: Meeting[]) => void
  let resolveNew!: (value: Meeting[]) => void
  vi.mocked(getMeetings)
    .mockReturnValueOnce(new Promise((resolve) => { resolveOld = resolve }))
    .mockReturnValueOnce(new Promise((resolve) => { resolveNew = resolve }))

  const { rerender } = render(
    <LibraryPanel reloadKey={0} viewScope="project-id" projects={projects} />,
  )
  await waitFor(() => expect(getMeetings).toHaveBeenCalledWith(
    expect.objectContaining({ viewScope: 'project-id' }),
  ))
  rerender(<LibraryPanel reloadKey={0} viewScope="climate-id" projects={projects} />)
  await waitFor(() => expect(getMeetings).toHaveBeenCalledWith(
    expect.objectContaining({ viewScope: 'climate-id' }),
  ))

  await act(async () => resolveNew([
    { ...meeting, id: 'new', title: 'Réunion nouvelle', project_id: 'climate-id' },
  ]))
  expect(await screen.findByText('Réunion nouvelle')).toBeInTheDocument()
  await act(async () => resolveOld([{ ...meeting, id: 'old', title: 'Réunion ancienne' }]))

  await waitFor(() => {
    expect(screen.queryByText('Réunion ancienne')).not.toBeInTheDocument()
    expect(screen.getByText('Réunion nouvelle')).toBeInTheDocument()
  })
})


it('offre de reprendre la dernière écoute et ouvre la réunion au clic', async () => {
  // État honnête côté client : une écoute mémorisée pour un document VISIBLE ici.
  localStorage.setItem('memoglyph.recent', JSON.stringify({
    name: 'meeting.md', scope: 'project-id', title: 'Réunion test', at: 125,
  }))
  const user = userEvent.setup()
  render(
    <AudioProvider>
      <LibraryPanel reloadKey={0} viewScope="project-id" projects={projects} />
    </AudioProvider>,
  )

  const hero = await screen.findByRole('button', {
    name: /Reprendre l’écoute de « Réunion test » à 2:05/,
  })
  await user.click(hero)
  // La réunion s'ouvre : son en-tête de niveau 1 apparaît.
  expect(await screen.findByRole('heading', { name: 'Réunion test', level: 1 })).toBeInTheDocument()
})


it('ne propose pas de reprendre une écoute absente de la portée courante', async () => {
  localStorage.setItem('memoglyph.recent', JSON.stringify({
    name: 'document-fantome.md', scope: 'project-id', title: 'Ailleurs', at: 90,
  }))
  render(
    <AudioProvider>
      <LibraryPanel reloadKey={0} viewScope="project-id" projects={projects} />
    </AudioProvider>,
  )

  await screen.findByRole('heading', { name: 'Bibliothèque' })
  expect(screen.queryByText('Reprendre l\'écoute')).not.toBeInTheDocument()
})


it('présente une galerie de projets en vue globale et en ouvre un au clic', async () => {
  const onSelectProject = vi.fn()
  const user = userEvent.setup()
  render(
    <LibraryPanel reloadKey={0} viewScope="all" projects={projects} onSelectProject={onSelectProject} />,
  )

  await screen.findByRole('heading', { name: 'Bibliothèque' })
  // Chaque projet réel est une carte ouvrable ; le clic ouvre ce projet.
  await user.click(screen.getByRole('button', { name: /Climat/ }))
  expect(onSelectProject).toHaveBeenCalledWith('climate-id')
})


it('ne montre pas la galerie de projets dans un projet précis', async () => {
  render(
    <LibraryPanel reloadKey={0} viewScope="project-id" projects={projects} onSelectProject={vi.fn()} />,
  )

  await screen.findByRole('heading', { name: 'Bibliothèque' })
  expect(screen.queryByRole('region', { name: 'Projets' })).not.toBeInTheDocument()
})


// ---------- en-tête de projet, Rafraîchir, fil d'Ariane ----------

it('Rafraîchir recharge la liste SANS recharger la page', async () => {
  const user = userEvent.setup()
  const onChanged = vi.fn()
  render(<LibraryPanel reloadKey={0} viewScope="project-id" projects={projects} onChanged={onChanged} />)
  await screen.findByText('21/07/2026')

  await user.click(screen.getByRole('button', { name: 'Rafraîchir' }))
  // Le parent recharge les projets et les partages (reloadKey) ; la liste suit.
  expect(onChanged).toHaveBeenCalledTimes(1)
})

it('l’en-tête du projet donne son nom, son nombre de réunions et avec qui il est partagé', async () => {
  render(<LibraryPanel reloadKey={0} viewScope="climate-id" projects={projects}
                       titre="Climat" partageAvec={['carole', 'bruno']} />)
  expect(await screen.findByRole('heading', { name: 'Climat' })).toBeInTheDocument()
  expect(screen.getByText(/partagé avec carole, bruno/i)).toBeInTheDocument()
})

it('dit « partagé avec personne » quand c’est le cas, et propose Partager au propriétaire', async () => {
  const user = userEvent.setup()
  const onPartager = vi.fn()
  render(<LibraryPanel reloadKey={0} viewScope="climate-id" projects={projects}
                       titre="Climat" partageAvec={[]} onPartager={onPartager} />)
  expect(await screen.findByText(/partagé avec personne/i)).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: 'Partager' }))
  expect(onPartager).toHaveBeenCalledTimes(1)
})

it('sans droit de partager (réception, lecture seule), l’en-tête n’offre pas Partager', async () => {
  render(<LibraryPanel reloadKey={0} viewScope="project-id" projects={projects} titre="Non classé" />)
  await screen.findByRole('heading', { name: 'Non classé' })
  expect(screen.queryByRole('button', { name: 'Partager' })).not.toBeInTheDocument()
  expect(screen.queryByText(/partagé avec/i)).not.toBeInTheDocument()
})

it('le fil d’Ariane de la liste mène à « Tous mes projets » en un clic', async () => {
  const user = userEvent.setup()
  const onSelectProject = vi.fn()
  render(<LibraryPanel reloadKey={0} viewScope="climate-id" projects={projects} titre="Climat"
                       onSelectProject={onSelectProject} />)
  const fil = await screen.findByRole('navigation', { name: /fil d.ariane/i })
  await user.click(within(fil).getByRole('link', { name: 'Bibliothèque' }))
  expect(onSelectProject).toHaveBeenCalledWith('all')
})

it('ouverte depuis « Tous mes projets », une réunion montre SON projet dans le fil d’Ariane', async () => {
  const user = userEvent.setup()
  const onSelectProject = vi.fn()
  const onMeetingChanged = vi.fn()
  render(<AudioProvider><LibraryPanel reloadKey={0} viewScope="all" projects={projects}
                       openMeetingId="meeting-id" onMeetingChanged={onMeetingChanged}
                       onSelectProject={onSelectProject} /></AudioProvider>)
  // La liste charge d'abord ; le fil de la réunion n'arrive qu'avec elle.
  const lien = await screen.findByRole('link', { name: 'Non classé' })
  expect(within(screen.getByRole('navigation', { name: /fil d.ariane/i })).getByText('Réunion test')).toBeInTheDocument()
  // Le projet du fil consulte CE projet, et referme la réunion.
  await user.click(lien)
  expect(onSelectProject).toHaveBeenCalledWith('project-id')
  expect(onMeetingChanged).toHaveBeenCalledWith(null)
})

it('le fil d’Ariane garde le niveau « projet » pour une réunion d’un projet reçu', async () => {
  const recue: Meeting = { ...meeting, project_id: 'recu-id', project: 'Équipe partenaire' }
  recue.transcripts = [{ ...transcript, project_id: 'recu-id', project: 'Équipe partenaire' }]
  recue.preferred_transcript = recue.transcripts[0]
  vi.mocked(getMeetings).mockResolvedValue([recue])
  render(<AudioProvider><LibraryPanel reloadKey={0} viewScope="shared" projects={projects}
                       openMeetingId="meeting-id" onSelectProject={vi.fn()} /></AudioProvider>)
  expect(await screen.findByRole('link', { name: 'Équipe partenaire' })).toBeInTheDocument()
})

it('les flèches vont à la réunion voisine du MÊME projet', async () => {
  const user = userEvent.setup()
  const autre: Meeting = { ...meeting, id: 'm2', title: 'Deuxième', date: '2026-07-25',
    transcripts: [{ ...transcript, name: 'm2.md', id: 'd2', meeting_id: 'm2', title: 'Deuxième', date: '2026-07-25' }],
    reports: [], preferred_transcript: { ...transcript, name: 'm2.md', id: 'd2', meeting_id: 'm2', title: 'Deuxième', date: '2026-07-25' } }
  const ailleurs: Meeting = { ...meeting, id: 'm3', title: 'Ailleurs', project_id: 'climate-id', project: 'Climat', date: '2026-07-23',
    transcripts: [{ ...transcript, name: 'm3.md', id: 'd3', meeting_id: 'm3', project_id: 'climate-id', title: 'Ailleurs', date: '2026-07-23' }],
    reports: [], preferred_transcript: { ...transcript, name: 'm3.md', id: 'd3', meeting_id: 'm3', project_id: 'climate-id', title: 'Ailleurs', date: '2026-07-23' } }
  vi.mocked(getMeetings).mockResolvedValue([meeting, ailleurs, autre])
  const onMeetingChanged = vi.fn()
  render(<AudioProvider><LibraryPanel reloadKey={0} viewScope="all" projects={projects}
                       openMeetingId="meeting-id" onMeetingChanged={onMeetingChanged} /></AudioProvider>)
  const suivante = await screen.findByRole('button', { name: 'Réunion suivante' })
  expect(screen.getByRole('button', { name: 'Réunion précédente' })).toBeDisabled()
  await user.click(suivante)
  // « Ailleurs » (autre projet, entre les deux dates) est sautée.
  expect(onMeetingChanged).toHaveBeenLastCalledWith('m2')
})


it('la colonne « projet » de la liste n’apparaît que quand plusieurs projets sont affichés', async () => {
  const { unmount } = render(<LibraryPanel reloadKey={0} viewScope="project-id" projects={projects} />)
  await screen.findByText('21/07/2026')
  expect(document.querySelector('.meeting-project')).toBeNull()
  unmount()
  render(<LibraryPanel reloadKey={0} viewScope="all" projects={projects} />)
  await screen.findByText('21/07/2026')
  expect(document.querySelector('.meeting-project')).not.toBeNull()
})

it('dans une réunion ouverte par la Bibliothèque, le fil d’Ariane remplace le bouton « Bibliothèque »', async () => {
  render(<AudioProvider><LibraryPanel reloadKey={0} viewScope="project-id" projects={projects}
                       openMeetingId="meeting-id" onSelectProject={vi.fn()} /></AudioProvider>)
  await screen.findByRole('navigation', { name: /fil d.ariane/i })
  await screen.findByRole('link', { name: 'Non classé' })
  expect(screen.queryByRole('button', { name: /^← ?Bibliothèque$|^Bibliothèque$/ })).not.toBeInTheDocument()
})
