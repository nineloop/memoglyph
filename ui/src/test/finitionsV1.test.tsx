import { render, screen, waitFor } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'

import { getTranscript } from '../api'
import { AudioProvider } from '../audio/AudioProvider'
import { LangProvider } from '../components/LangProvider'
import { TranscriptReader } from '../components/TranscriptReader'
import { useLang } from '../lib/langContext'
import { cleSelonFaits } from '../lib/messages'

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return { ...actual, getTranscript: vi.fn() }
})

beforeEach(() => {
  vi.mocked(getTranscript).mockReset()
})

function Texte({ cle, faits }: { cle: 'privacy.opens' | 'sensitivity.hint.ultra'; faits: boolean }) {
  const { t } = useLang()
  return <p>{t(cleSelonFaits(cle, faits))}</p>
}

// Les faits de la réunion sont une option coupée par défaut : un texte qui les cite
// promettrait une fonction absente.
it.each([['fr', /faits/i], ['en', /facts/i]] as const)(
  '(%s) les textes de confidentialité ne citent les faits que si l’option est active', (lang, motif) => {
    for (const cle of ['privacy.opens', 'sensitivity.hint.ultra'] as const) {
      for (const faits of [false, true]) {
        const { container, unmount } = render(<LangProvider initiale={lang}><Texte cle={cle} faits={faits} /></LangProvider>)
        if (faits) expect(container.textContent).toMatch(motif)
        else expect(container.textContent).not.toMatch(motif)
        unmount()
      }
    }
  })

it.each([['en', 'Voice 2', /Voix/], ['fr', 'Voix 2', /Voice/]] as const)(
  '(%s) une voix anonyme porte le libellé de la langue et le numéro de son Speaker', async (lang, attendu, autre) => {
    vi.mocked(getTranscript).mockResolvedValue({
      name: 'note.md', meta: {}, body: 'Texte', content: '', has_audio: false,
      voices: { 'Speaker 2': { voice_id: 7, label: null, label_source: null, display: 'Voix 7' } },
    } as never)
    render(
      <LangProvider initiale={lang}>
        <AudioProvider>
          <TranscriptReader name="note.md" documentScope="projet-1" onBack={vi.fn()} />
        </AudioProvider>
      </LangProvider>,
    )
    expect(await screen.findByText(attendu)).toBeInTheDocument()
    expect(screen.queryByText(autre)).toBeNull()
    expect(screen.queryByText(/7/)).toBeNull()
  })

it('un clic sur une citation place le lecteur à la seconde citée et lance la lecture', async () => {
  const play = vi.spyOn(HTMLMediaElement.prototype, 'play').mockImplementation(() => Promise.resolve())
  vi.mocked(getTranscript).mockResolvedValue({
    name: 'note.md', meta: {}, body: '[00:10] Introduction\n[01:15] Décision', content: '', has_audio: true,
  } as never)
  const { container } = render(
    <AudioProvider>
      <TranscriptReader name="note.md" documentScope="projet-1" initialTimestamp="01:15" lancer onBack={vi.fn()} />
    </AudioProvider>,
  )
  await waitFor(() => expect((container.querySelector('audio') as HTMLAudioElement | null)?.currentTime).toBe(75))
  await waitFor(() => expect(play).toHaveBeenCalled())
  play.mockRestore()
})
