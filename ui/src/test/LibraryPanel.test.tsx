import { act, render, screen, waitFor } from '@testing-library/react'
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
