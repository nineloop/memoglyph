import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, expect, it, vi } from 'vitest'

import type { AppConfig, LibraryItem, ListeTraitements } from '../api'
import type { Reunion } from '../lib/meetings'
import { archiveTranscript, getArchivePlan, getCloudUltra, getConfig, getFinopsSummary, getReglagesTraitement, getTranscript, lancerCompteRendu, makeReport } from '../api'
import { LangProvider } from '../components/LangProvider'
import { MeetingView } from '../components/MeetingView'
import { TraitementsContext } from '../lib/traitementsContext'
import { avecConfirmation, confirmer as confirmerLaFenetre, fenetreDeConfirmation, phraseDeLaFenetre, refuser } from './confirmation'

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return {
    ...actual,
    getTranscript: vi.fn(),
    makeReport: vi.fn(),
    archiveTranscript: vi.fn(),
    getArchivePlan: vi.fn(),
    getConfig: vi.fn(),
    getCloudUltra: vi.fn(),
    getReglagesTraitement: vi.fn(),
    getFinopsSummary: vi.fn(),
    lancerCompteRendu: vi.fn(),
  }
})

function item(name: string, modified: number, type: string | null = null): LibraryItem {
  return {
    name, id: `${name}-id`, meeting_id: 'meeting-id', project_id: 'project-id',
    title: 'weekly-team-sync', date: '2026-07-21', duration: '00:42:00', language: 'fr',
    engine: 'whisper', source: 'Weekly Team Sync.mp4', project: 'Non classé',
    diarization: type !== 'report', profiled: type !== 'report', n_speakers: type === 'report' ? null : 4,
    type, source_transcript: type === 'report' ? 'transcript-a.md' : null,
    source_document_id: type === 'report' ? 'transcript-a.md-id' : null,
    warnings: name === 'report-a.md' ? 1 : 0, size: 100, modified,
  }
}

const transcriptA = item('transcript-a.md', Date.UTC(2026, 6, 21, 8, 0, 5) / 1000)
const transcriptB = item('transcript-b.md', Date.UTC(2026, 6, 21, 8, 0, 42) / 1000)
const reportA = item('report-a.md', Date.UTC(2026, 6, 21, 10, 0, 5) / 1000, 'report')
const reportB = item('report-b.md', Date.UTC(2026, 6, 21, 10, 0, 42) / 1000, 'report')

const reunion: Reunion = {
  cle: 'meeting-id', source: 'Weekly Team Sync.mp4', titre: 'weekly-team-sync', date: '2026-07-21',
  duree: '00:42:00', langue: 'fr', projet: 'Non classé', nSpeakers: 4, avertissements: 1,
  transcripts: [transcriptA, transcriptB], comptesRendus: [reportA, reportB], retenu: transcriptA,
}

beforeEach(() => {
  vi.mocked(getConfig).mockResolvedValue({
    llm_etats: { mlx: { lieu: 'local', etat: 'pret' } }, local_providers: ['mlx'], default_llm_provider: 'mlx',
    llm_models: { mlx: 'Qwen3-8B' },
  } as unknown as AppConfig)
  vi.mocked(getCloudUltra).mockResolvedValue({ autorise: false, modifie_le: null } as never)
  vi.mocked(getReglagesTraitement).mockResolvedValue({ reglages: null })
  vi.mocked(getFinopsSummary).mockResolvedValue({ available: false })
  vi.mocked(lancerCompteRendu).mockResolvedValue({ job_id: 'j1' })
  vi.mocked(makeReport).mockResolvedValue({
    name: 'report-a.md', content: '# Nouveau compte-rendu', genre: 'town_hall',
  })
  vi.mocked(getTranscript).mockImplementation(async (name) => {
    if (name.startsWith('report')) {
      return {
        name,
        meta: name === 'report-a.md'
          ? { type: 'report', genre: 'town_hall', unsupported_numbers: ['42'] }
          : { type: 'report', genre: 'internal_briefing' },
        body: '# Synthèse\n\n## Décisions\n\n- **Lancer** le pilote\n- Préparer le bilan',
        content: '# Synthèse',
      }
    }
    return { name, meta: {}, body: 'Transcript', content: '[00:00] Speaker 1 : Bonjour', has_audio: true }
  })
})

it('ajoute immédiatement un compte-rendu généré à la réunion ouverte', async () => {
  const user = userEvent.setup()
  const changed = vi.fn()
  const withoutReport = { ...reunion, comptesRendus: [], avertissements: 0 }
  const fin: ListeTraitements = {
    actifs: 0, en_attente: 0,
    traitements: [{ id: 'j1', nature: 'compte-rendu', statut: 'done', en_attente: false, progression: 1, titre: 'weekly-team-sync',
      projet_id: 'project-id', projet_nom: 'Non classé', etape: 3, etapes: 3, message_code: null, message_params: {}, debut: 0,
      meeting: { name: 'report-a.md' }, erreur: null, erreur_code: null, erreur_params: {} } as never],
  }
  const rendu = (reunionCourante: Reunion, liste: ListeTraitements | null) => (
    <TraitementsContext.Provider value={{ liste, rafraichir: vi.fn(), suivre: vi.fn() }}>
      <MeetingView reunion={reunionCourante} onBack={vi.fn()} onChanged={changed} />
    </TraitementsContext.Provider>
  )
  const { rerender } = render(rendu(withoutReport, null))
  await screen.findByText('Transcript')

  // Le chemin de la personne : l'onglet Transcript, « Rédiger le compte rendu », le choix du fournisseur, le lancement.
  await user.click(screen.getByRole('button', { name: 'Rédiger le compte rendu' }))
  await user.click(await screen.findByRole('button', { name: 'Rédiger ce compte rendu' }))
  expect(lancerCompteRendu).toHaveBeenCalledWith('transcript-a.md', 'project-id', 'mlx')
  expect(makeReport).not.toHaveBeenCalled()                         // plus de route synchrone

  // Le traitement se termine : la réunion est prévenue, une seule fois, et le nouveau compte rendu s'ajoute.
  rerender(rendu(withoutReport, fin))
  await waitFor(() => expect(changed).toHaveBeenCalledOnce())

  rerender(rendu({ ...withoutReport, comptesRendus: [reportA] }, fin))
  await user.click(screen.getByRole('tab', { name: /Compte rendu 1/ }))

  expect(await screen.findByRole('heading', { name: 'Décisions', level: 2 })).toBeInTheDocument()
})

it('rend le compte-rendu en Markdown et sépare les quatre espaces', async () => {
  render(<MeetingView reunion={reunion} onBack={vi.fn()} onVoirDoublons={vi.fn()} />)

  expect(await screen.findByRole('heading', { name: 'Décisions', level: 2 })).toBeInTheDocument()
  // Le H1 de tête répète le titre de la réunion : il n'est pas affiché.
  expect(screen.queryByRole('heading', { name: 'Synthèse' })).not.toBeInTheDocument()
  expect(screen.getByText('Lancer').closest('li')).toHaveTextContent('Lancer le pilote')
  expect(screen.getByText('Réunion plénière interne')).toBeInTheDocument()
  expect(screen.queryByText('# Synthèse')).not.toBeInTheDocument()

  for (const tab of ['Compte rendu', 'Transcript', 'Versions', 'Informations']) {
    expect(screen.getByRole('tab', { name: new RegExp(tab) })).toBeInTheDocument()
  }
  expect(screen.getByLabelText('Actions de la réunion')).toBeInTheDocument()
})

it('distingue les versions par date et heure et ouvre le document choisi', async () => {
  const user = userEvent.setup()
  render(<MeetingView reunion={reunion} onBack={vi.fn()} />)
  await screen.findByRole('heading', { name: 'Décisions' })

  const reportChoices = screen.getAllByRole('button', { name: /Afficher le compte rendu \d du/ })
  expect(reportChoices).toHaveLength(2)
  expect(reportChoices[0]).not.toHaveAccessibleName(reportChoices[1].getAttribute('aria-label') || '')

  await user.click(screen.getByRole('tab', { name: /Versions/ }))
  const panel = screen.getByRole('tabpanel', { name: 'Versions' })
  const timestamps = within(panel).getAllByText(/21\/07\/2026.*\d{2}:\d{2}:\d{2}/)
  expect(new Set(timestamps.map((node) => node.textContent)).size).toBeGreaterThan(1)

  await user.click(within(panel).getAllByRole('button', { name: 'Afficher' })[0])
  await waitFor(() => expect(getTranscript).toHaveBeenCalledWith('transcript-b.md', 'project-id'))
  expect(screen.getByRole('tabpanel', { name: 'Transcript' })).toBeVisible()
})

it('rattache chaque réserve au document et à son heure exacts', async () => {
  const user = userEvent.setup()
  render(<MeetingView reunion={reunion} onBack={vi.fn()} />)
  await screen.findByRole('heading', { name: 'Décisions' })

  await user.click(screen.getByRole('button', { name: /1 réserve de qualité/ }))
  expect(await screen.findByText(/Compte rendu · 21\/07\/2026.*\d{2}:\d{2}:\d{2} · report-a\.md/)).toBeInTheDocument()
  expect(screen.getByText(/1 chiffre non retrouvé/)).toBeInTheDocument()
})

it('permet d’archiver unitairement un compte-rendu ou un transcript', async () => {
  const user = userEvent.setup()
  const changed = vi.fn()
  vi.mocked(getArchivePlan).mockResolvedValue({ requested: 'report-a.md', documents: [{ name: 'report-a.md', title: 'report-a.md', type: 'report', document_id: 'doc-1' }] })
  vi.mocked(archiveTranscript).mockResolvedValue({ archived: 'report-a.md', documents: ['report-a.md'] })

  render(<MeetingView reunion={reunion} onBack={vi.fn()} onChanged={changed} />, { wrapper: avecConfirmation })
  await screen.findByRole('heading', { name: 'Décisions' })

  await user.click(screen.getByRole('button', { name: 'Archiver ce compte rendu' }))
  expect(await fenetreDeConfirmation()).toHaveAccessibleName(/^Archiver .* « report-a\.md » \?$/)
  expect(await phraseDeLaFenetre()).toBe('Le document sera placé dans la corbeille et pourra être supprimé dans l’onglet Maintenance.')
  expect(archiveTranscript).not.toHaveBeenCalled()
  await confirmerLaFenetre(user)
  expect(archiveTranscript).toHaveBeenCalledWith('report-a.md', 'project-id')
  expect(changed).toHaveBeenCalledOnce()
})


it('plusieurs documents archivés ensemble : leurs titres sont rendus en liste dans la fenêtre', async () => {
  const user = userEvent.setup()
  vi.mocked(getArchivePlan).mockResolvedValue({ requested: 'report-a.md', documents: [
    { name: 'report-a.md', title: 'Compte rendu A', type: 'report', document_id: 'doc-1' },
    { name: 'transcript-a.md', title: 'Transcript A', type: 'transcript', document_id: 'doc-2' },
  ] })
  render(<MeetingView reunion={reunion} onBack={vi.fn()} />, { wrapper: avecConfirmation })
  await screen.findByRole('heading', { name: 'Décisions' })

  await user.click(screen.getByRole('button', { name: 'Archiver ce compte rendu' }))
  const fenetre = await fenetreDeConfirmation()

  expect(await phraseDeLaFenetre()).toBe('Les documents suivants seront placés dans la corbeille :')
  expect(within(within(fenetre).getByRole('list')).getAllByRole('listitem').map((li) => li.textContent)).toEqual(['Compte rendu A', 'Transcript A'])
  await refuser(user)
  expect(archiveTranscript).not.toHaveBeenCalled()
})

/**
 * L'onglet de réunion vit dans l'URL (voir `lib/urlState.ts`).
 *
 * Défaut visible à l'écran, qu'aucun test ne voyait : le bouton retour
 * changeait l'URL et laissait l'écran sur l'onglet précédent. `initialTab`
 * n'était lu qu'au montage, le même piège que pour la réunion elle-même.
 */
it('ouvre sur l’onglet imposé par l’URL', () => {
  render(<MeetingView reunion={reunion} onBack={vi.fn()} initialTab="versions" />)
  const actif = document.querySelector('[role="tab"][aria-selected="true"]')
  expect(actif?.textContent).toMatch(/Versions/)
})

it('SUIT un changement d’onglet imposé après le montage', () => {
  // C'est ce que fait le bouton retour : le parent relit l'URL et impose un
  // onglet à un composant déjà monté.
  const { rerender } = render(
    <MeetingView reunion={reunion} onBack={vi.fn()} initialTab="versions" />)
  expect(document.querySelector('[role="tab"][aria-selected="true"]')?.textContent)
    .toMatch(/Versions/)

  rerender(<MeetingView reunion={reunion} onBack={vi.fn()} initialTab="info" />)
  expect(document.querySelector('[role="tab"][aria-selected="true"]')?.textContent)
    .toMatch(/Informations/)
})

it('remonte le changement d’onglet au parent', () => {
  // Sans cet écho, l'URL ne saurait pas que l'utilisateur a changé d'onglet.
  const change = vi.fn()
  render(<MeetingView reunion={reunion} onBack={vi.fn()} onTabChange={change} />)
  const onglets = Array.from(document.querySelectorAll('[role="tab"]'))
  const versions = onglets.find((t) => /Versions/.test(t.textContent ?? ''))
  versions?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
  expect(change).toHaveBeenCalledWith('versions')
})

it('IGNORE un onglet inconnu et retombe sur le contenu disponible', () => {
  render(<MeetingView reunion={reunion} onBack={vi.fn()} initialTab="nimporte" />)
  const actif = document.querySelector('[role="tab"][aria-selected="true"]')
  expect(actif?.textContent).toMatch(/Compte rendu|Transcript/)
})

/**
 * L'en-tête se compacte en lecture.
 *
 * Le point qui compte n'est PAS la classe CSS : c'est qu'aucun fait ne
 * disparaisse au passage. Compacter en perdant la langue ou le nombre de
 * locuteurs serait une régression déguisée en gain de place.
 */
const FAITS_ATTENDUS = ['21/07/2026', '00:42:00', 'FR', '4 locuteurs', 'Non classé']

it('compacte l’en-tête en lecture SANS perdre un seul fait', () => {
  render(<MeetingView reunion={reunion} onBack={vi.fn()} initialTab="transcript" />)

  const entete = document.querySelector('.meeting-hero')
  expect(entete).toHaveClass('compact')
  // La grille libellée cède la place à une ligne — sinon on aurait les deux.
  expect(document.querySelector('.meeting-hero-facts')).toBeNull()

  const ligne = document.querySelector('.meeting-hero-line')
  for (const fait of FAITS_ATTENDUS) expect(ligne).toHaveTextContent(fait)
})

it('garde l’en-tête PLEIN hors lecture, avec les mêmes faits libellés', async () => {
  const user = userEvent.setup()
  render(<MeetingView reunion={reunion} onBack={vi.fn()} initialTab="info" />)

  expect(document.querySelector('.meeting-hero')).not.toHaveClass('compact')
  expect(document.querySelector('.meeting-hero-line')).toBeNull()

  const grille = screen.getByLabelText('Informations principales')
  for (const libelle of ['Date', 'Durée', 'Langue', 'Locuteurs', 'Projet']) {
    expect(within(grille).getByText(libelle)).toBeInTheDocument()
  }
  expect(within(grille).getByText('4')).toBeInTheDocument()

  // Et le basculement se fait bien À CHAUD, pas seulement au montage.
  await user.click(screen.getByRole('tab', { name: /Transcript/ }))
  expect(document.querySelector('.meeting-hero')).toHaveClass('compact')
})

it('dit « inconnu » plutôt que « — » quand un fait sort de sa colonne', () => {
  // Un tiret cadratin se lit dans une grille libellée ; seul dans une phrase,
  // il ne dit rien. C'est pourquoi chaque fait porte une forme autonome.
  const creuse: Reunion = {
    ...reunion, date: '', duree: null, langue: null, nSpeakers: null, projet: '',
  }
  render(<MeetingView reunion={creuse} onBack={vi.fn()} initialTab="transcript" />)

  const ligne = document.querySelector('.meeting-hero-line')
  expect(ligne).toHaveTextContent(
    'Date inconnue · Durée inconnue · Langue inconnue · Locuteurs inconnus · Non classé')
})

it('un locuteur reste au SINGULIER', () => {
  render(
    <MeetingView reunion={{ ...reunion, nSpeakers: 1 }} onBack={vi.fn()} initialTab="transcript" />)
  expect(document.querySelector('.meeting-hero-line')).toHaveTextContent('1 locuteur ·')
})

it('le bandeau de réserves SURVIT au mode compact', () => {
  // Gagner de la place en masquant un avertissement de qualité serait le pire
  // des gains : l'utilisateur lirait un document sans savoir ce qu'on lui doit.
  render(<MeetingView reunion={reunion} onBack={vi.fn()} initialTab="transcript" />)
  expect(document.querySelector('.meeting-hero')).toHaveClass('compact')
  expect(screen.getByRole('button', { name: /1 réserve de qualité/ })).toBeInTheDocument()
})


it('en anglais, le jour de la réunion et la date de chaque version se lisent en anglais, jamais « jj/mm/aaaa » ni en français', async () => {
  const user = userEvent.setup()
  render(<LangProvider initiale="en"><MeetingView reunion={reunion} onBack={vi.fn()} initialTab="transcript" /></LangProvider>)
  expect(document.querySelector('.meeting-hero-line')).toHaveTextContent('21 Jul 2026')

  await user.click(screen.getByRole('tab', { name: /Versions/ }))
  const panel = screen.getByRole('tabpanel', { name: 'Versions' })
  expect(within(panel).getAllByText(/21 Jul 2026.*\d{2}:\d{2}:\d{2}/).length).toBeGreaterThan(0)
  expect(panel.textContent).not.toMatch(/\d{2}\/07\/2026/)
})


it('l’échec d’un archivage dit la phrase du contexte : ni « HTTP », ni le texte de l’exception', async () => {
  const user = userEvent.setup()
  vi.mocked(getArchivePlan).mockResolvedValue({ requested: 'report-a.md', documents: [{ name: 'report-a.md', title: 'report-a.md', type: 'report', document_id: 'doc-1' }] })
  vi.mocked(archiveTranscript).mockRejectedValue(new Error('HTTP 500: {"detail":"archivage-brut"}'))
  render(<MeetingView reunion={reunion} onBack={vi.fn()} />, { wrapper: avecConfirmation })
  await screen.findByRole('heading', { name: 'Décisions' })

  await user.click(screen.getByRole('button', { name: 'Archiver ce compte rendu' }))
  await confirmerLaFenetre(user)

  expect(await screen.findByText('L’archivage n’a pas abouti.')).toBeInTheDocument()
  expect(screen.getByRole('alert')).toHaveTextContent('L’archivage n’a pas abouti.')   // annoncée
  expect(document.body.textContent).not.toMatch(/HTTP|archivage-brut/)
})


it('la fenêtre d’archivage nomme le document par son titre lisible, pas par le nom de son fichier', async () => {
  const user = userEvent.setup()
  vi.mocked(getArchivePlan).mockResolvedValue({ requested: 'report-a.md', documents: [{ name: 'report-a.md', title: 'Compte rendu du comité', type: 'report', document_id: 'doc-1' }] })
  render(<MeetingView reunion={reunion} onBack={vi.fn()} />, { wrapper: avecConfirmation })
  await screen.findByRole('heading', { name: 'Décisions' })

  await user.click(screen.getByRole('button', { name: 'Archiver ce compte rendu' }))

  expect(await fenetreDeConfirmation()).toHaveAccessibleName(/« Compte rendu du comité »/)
  expect(await fenetreDeConfirmation()).not.toHaveAccessibleName(/report-a\.md/)
  await refuser(user)
})


it('le nom du fichier n’est montré sous le titre que s’il apprend quelque chose', () => {
  const { rerender } = render(<MeetingView reunion={{ ...reunion, titre: 'Comité budget', source: 'Comité budget.wav' }} onBack={vi.fn()} initialTab="info" />)
  expect(document.querySelector('.meeting-hero-source')).toBeNull()   // le titre est déjà ce nom, à l'extension près

  rerender(<MeetingView reunion={{ ...reunion, titre: 'Comité budget', source: 'enregistrement-0412.wav' }} onBack={vi.fn()} initialTab="info" />)
  expect(document.querySelector('.meeting-hero-source')).toHaveTextContent('enregistrement-0412.wav')
})


it('les onglets de la réunion se parcourent aux flèches : la sélection et le focus suivent, un seul onglet reste dans l’ordre de tabulation', async () => {
  const user = userEvent.setup()
  render(<MeetingView reunion={reunion} onBack={vi.fn()} initialTab="cr" />)
  const rendu = await screen.findByRole('tab', { name: /Compte rendu/ })
  rendu.focus()

  await user.keyboard('{ArrowRight}')
  const transcript = screen.getByRole('tab', { name: /Transcript/ })
  expect(transcript).toHaveAttribute('aria-selected', 'true')
  expect(transcript).toHaveFocus()                                   // le focus suit la sélection
  expect(rendu).toHaveAttribute('tabindex', '-1')
  expect(transcript).toHaveAttribute('aria-controls', screen.getByRole('tabpanel', { name: 'Transcript' }).id)

  await user.keyboard('{End}')
  expect(screen.getByRole('tab', { name: /Informations/ })).toHaveFocus()
})


it('la carte « Usage et coût » dit qu’aucun usage n’est enregistré, au lieu de n’afficher que son titre', async () => {
  const user = userEvent.setup()
  render(<MeetingView reunion={reunion} onBack={vi.fn()} />)
  await screen.findByRole('heading', { name: 'Décisions' })
  await user.click(screen.getByRole('tab', { name: /Informations/ }))

  const carte = (await screen.findByText('Usage et coût')).closest('.finops-card') as HTMLElement
  expect(await within(carte).findAllByText('Aucun usage enregistré.')).toHaveLength(2)   // le compte rendu actif, puis la transcription
  expect(within(carte).getByText('Compte rendu')).toBeInTheDocument()
  expect(within(carte).getByText('Transcription')).toBeInTheDocument()
})
