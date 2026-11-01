import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, expect, it, vi } from 'vitest'

import {
  archiveTranscript, getArchivePlan, getDuplicates,
} from '../api'
import { DedupPanel } from '../components/DedupPanel'


vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return {
    ...actual,
    getDuplicates: vi.fn(),
    getArchivePlan: vi.fn(),
    archiveTranscript: vi.fn(),
  }
})


beforeEach(() => {
  vi.mocked(getDuplicates).mockResolvedValue([{
    keep: 'version-récente.md',
    reason: 'version la plus récente',
    count: 2,
    source: 'réunion.m4a',
    members: [
      {
        name: 'version-récente.md',
        title: 'Version récente',
        source: 'réunion.m4a',
        language: 'fr',
        duration: '10:00',
        diarization: true,
        profiled: true,
        n_speakers: 2,
        generated_at: '2026-07-23',
        keep: true,
      },
      {
        name: 'ancienne.md',
        title: 'Ancienne version',
        source: 'réunion.m4a',
        language: 'fr',
        duration: '10:00',
        diarization: true,
        profiled: false,
        n_speakers: 2,
        generated_at: '2026-07-22',
        keep: false,
      },
    ],
  }])
  vi.mocked(getArchivePlan).mockResolvedValue({
    requested: 'ancienne.md',
    documents: [
      {
        name: 'ancienne-cr.md',
        title: 'Ancienne version — compte-rendu',
        type: 'report',
        document_id: 'doc-cr',
      },
      {
        name: 'ancienne.md',
        title: 'Ancienne version',
        type: null,
        document_id: 'doc-source',
      },
    ],
  })
  vi.mocked(archiveTranscript).mockResolvedValue({
    archived: 'ancienne.md',
    documents: ['ancienne-cr.md', 'ancienne.md'],
  })
  vi.spyOn(window, 'confirm').mockReturnValue(true)
})


it('annonce le transcript et ses comptes-rendus avant archivage', async () => {
  const user = userEvent.setup()
  render(<DedupPanel viewScope="project-1" embedded />)

  await user.click(await screen.findByRole('button', { name: 'Archiver' }))

  expect(getArchivePlan).toHaveBeenCalledWith('ancienne.md', 'project-1')
  expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining(
    'Ancienne version — compte-rendu (ancienne-cr.md)',
  ))
  expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining(
    'bibliothèque, de la recherche et du RAG',
  ))
  expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining(
    'archives audio et empreintes restent récupérables',
  ))
  expect(archiveTranscript).toHaveBeenCalledWith('ancienne.md', 'project-1')
  await waitFor(() => expect(getDuplicates).toHaveBeenCalledTimes(2))
})


it('n’archive rien quand la confirmation est refusée', async () => {
  const user = userEvent.setup()
  vi.mocked(window.confirm).mockReturnValue(false)
  render(<DedupPanel viewScope="project-1" embedded />)

  await user.click(await screen.findByRole('button', { name: 'Archiver' }))

  expect(getArchivePlan).toHaveBeenCalledWith('ancienne.md', 'project-1')
  expect(archiveTranscript).not.toHaveBeenCalled()
})


it('titre un groupe par le titre lisible de la réunion, ni slug ni extension', async () => {
  vi.mocked(getDuplicates).mockResolvedValue([{
    keep: 'ami-is1009a-reunion.md',
    reason: 'version la plus récente',
    count: 2,
    source: 'AMI IS1009a, réunion.wav',
    members: [
      { name: 'ami-is1009a-reunion.md', title: 'ami-is1009a-reunion', source: 'AMI IS1009a, réunion.wav',
        language: 'fr', duration: '10:00', diarization: true, profiled: true, n_speakers: 2,
        generated_at: '2026-07-23', keep: true },
      { name: 'ami-is1009a-reunion-1.md', title: 'ami-is1009a-reunion', source: 'AMI IS1009a, réunion.wav',
        language: 'fr', duration: '10:00', diarization: false, profiled: false, n_speakers: null,
        generated_at: '2026-07-22', keep: false },
    ],
  }])
  render(<DedupPanel viewScope="project-1" />)

  expect(await screen.findByText('AMI IS1009a, réunion')).toBeInTheDocument()
  expect(screen.queryByText('AMI IS1009a, réunion.wav')).not.toBeInTheDocument()
  expect(screen.queryByText('ami-is1009a-reunion')).not.toBeInTheDocument()
})
