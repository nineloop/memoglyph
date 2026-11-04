import { useCallback, useEffect, useState } from 'react'
import { ModeleGratuitError, getConfig, getFaitsReunion, getJobStatus, relancerFaitsReunion, type FaitEcl } from '../api'
import { Badge } from '../design/primitives'
import { useCloudUltra } from '../lib/cloudUltra'
import { traduireEtape } from '../lib/i18n'
import { useLang } from '../lib/langContext'
import type { MessageKey } from '../lib/messages'

type Props = {
  /** `meeting_id` de la réunion. */
  meetingId: string
  viewScope: string
  /** Ouvre le transcript du fait à son horodatage. */
  onSource: (document: string, horodatage: string) => void
  /** Partage reçu : aucune relance (l'API la refuse de toute façon). */
  lectureSeule?: boolean
  /** Réunion ultra-confidentielle : la relance ne propose que les fournisseurs locaux. */
  ultra?: boolean
}

const TYPES: Record<string, MessageKey> = {
  decision: 'meeting.facts.type.decision',
  action: 'meeting.facts.type.action',
  probleme: 'meeting.facts.type.probleme',
  chiffre: 'meeting.facts.type.chiffre',
}

const ATTENTE_MS = 1500

/** L'échec d'un traitement de fond : son texte brut, et le CODE que l'interface traduit. */
class EchecTraitement extends Error {
  code: string | null
  params: Record<string, string | number>
  constructor(message: string, code: string | null, params: Record<string, string | number>) {
    super(message)
    this.code = code
    this.params = params
  }
}

/** Motif du contrôle échoué d'un fait périmé (`ecl.MOTIFS`), lisible. */
const MOTIFS: Record<string, MessageKey> = {
  citation_absente: 'meeting.facts.stale.reason.citation_absente',
  contenance: 'meeting.facts.stale.reason.contenance',
  citation_trop_courte: 'meeting.facts.stale.reason.citation_trop_courte',
  horodatage_illisible: 'meeting.facts.stale.reason.horodatage_illisible',
  type_inconnu: 'meeting.facts.stale.reason.type_inconnu',
  incomplet: 'meeting.facts.stale.reason.incomplet',
}

/** Libellé lisible d'un fournisseur ; un fournisseur sans libellé garde son identifiant. */
const NOMS: Record<string, MessageKey> = {
  mlx: 'app.provider.mlxLocal',
  mistral: 'app.provider.mistralCloud',
  openai: 'app.provider.openaiCloud',
  anthropic: 'app.provider.anthropicCloud',
  compatible: 'app.provider.compatible',
  openrouter: 'app.provider.openrouter',
  groq: 'app.provider.groq',
}

/** Proposé d'abord pour relancer en cloud : Anthropic sert Sonnet pour les faits (`ECL_ANTHROPIC_MODEL`). */
const CLOUD_PREFERE = 'anthropic'

/**
 * Les faits de l'ECL d'une réunion. La citation, mot pour mot, est le fait affiché, et
 * sa suite (affichage C, en local) est visiblement distincte ; l'énoncé du modèle vient
 * en second, marqué « reformulé ». Un fait hors cloud reconnu porte « à vérifier », une mise
 * en garde ; chaque fait montre le modèle qui l'a extrait, repli compris, sans aucune mesure de
 * précision. Les faits périmés (source corrigée depuis
 * l'extraction) viennent à part, marqués comme tels avec leur motif, jamais parmi les
 * actuels. Le propriétaire peut relancer l'extraction avec un autre fournisseur de l'écran
 * (Anthropic proposé d'abord) ; le cloud demande une confirmation, l'extraction précédente
 * reste gardée.
 */
export function EclFaits({ meetingId, viewScope, onSource, lectureSeule, ultra: ultraDocument }: Props) {
  // Le compte peut autoriser le cloud pour son ultra-confidentiel (panneau Confidentialité).
  const cloudUltra = useCloudUltra()
  const ultra = Boolean(ultraDocument) && !cloudUltra
  const { t, lang } = useLang()
  const [faits, setFaits] = useState<FaitEcl[] | null>(null)
  const [perimes, setPerimes] = useState<FaitEcl[]>([])
  const [erreur, setErreur] = useState('')
  const [fournisseurs, setFournisseurs] = useState<string[]>([])
  const [locaux, setLocaux] = useState<string[]>([])
  const [prives, setPrives] = useState<string[]>([])
  const [choix, setChoix] = useState('')
  const [enCours, setEnCours] = useState(false)
  const [etat, setEtat] = useState('')

  const charger = useCallback(() => {
    let courant = true
    setErreur('')
    getFaitsReunion(meetingId, viewScope)
      .then((corps) => {
        if (!courant) return
        setFaits(corps.faits)
        setPerimes(corps.perimes ?? [])
      })
      .catch((raison) => { if (courant) setErreur(String(raison)) })
    return () => { courant = false }
  }, [meetingId, viewScope])

  useEffect(() => { setFaits(null); return charger() }, [charger])

  useEffect(() => {
    if (lectureSeule) return
    let courant = true
    getConfig().then((config) => {
      if (!courant) return
      const locaux = config.local_providers || ['mlx']
      const liste = (config.llm_providers || [])
        .filter((p) => p !== 'cloud' && (!ultra || locaux.includes(p)))
      setFournisseurs(liste)
      setLocaux(locaux)
      setPrives(config.private_cloud_providers || [])
      const prefere = liste.includes(CLOUD_PREFERE) && config.llm_ready?.[CLOUD_PREFERE] !== false
        ? CLOUD_PREFERE : ''
      setChoix((c) => (c && liste.includes(c) ? c : prefere || config.default_llm_provider || liste[0] || ''))
    }).catch(() => {})
    return () => { courant = false }
  }, [lectureSeule, ultra])

  // Un serveur hébergé par l'utilisateur hors de cette machine est nommé « cloud privé ».
  const nomDe = (p: string) => (NOMS[p] ? t(NOMS[p]) : p)
    + (prives.includes(p) ? ` (${t('app.mode.privateCloud')})` : '')

  async function relancer() {
    if (!choix) return
    if (!locaux.includes(choix) && !window.confirm(t('meeting.facts.rerun.cloudConfirm', { provider: nomDe(choix) }))) return
    setEnCours(true)
    setEtat(t('meeting.facts.rerun.running'))
    try {
      const { job_id: jobId } = await relancerFaitsReunion(meetingId, viewScope, choix)
      for (;;) {
        const statut = await getJobStatus(jobId)
        if (statut.status === 'error') throw new EchecTraitement(statut.error || statut.message, statut.error_code ?? null, statut.error_params ?? {})
        if (statut.status === 'done') break
        await new Promise((fin) => setTimeout(fin, ATTENTE_MS))
      }
      setEtat(t('meeting.facts.rerun.kept'))
      charger()
    } catch (raison) {
      // Un échec de traitement se dit par son code traduit ; le texte brut du serveur n'est montré que sans code.
      const dit = raison instanceof EchecTraitement && raison.code
        ? traduireEtape(lang, { code: raison.code, params: raison.params, message: raison.message })
        : raison instanceof ModeleGratuitError ? t('job.step.error.freeModelRestricted') : String(raison)
      setEtat(t('meeting.facts.rerun.error', { reason: dit }))
    } finally {
      setEnCours(false)
    }
  }

  const relance = !lectureSeule && fournisseurs.length > 0 && (
    <div className="ecl-relance">
      <label>
        {t('meeting.facts.rerun.provider')}{' '}
        <select className="mini-select" value={choix} onChange={(e) => setChoix(e.target.value)} disabled={enCours}>
          {fournisseurs.map((p) => <option key={p} value={p}>{nomDe(p)}</option>)}
        </select>
      </label>{' '}
      <button type="button" className="btn" onClick={relancer} disabled={enCours || !choix}
        aria-label={t('meeting.facts.rerun')}>
        {t('meeting.facts.rerun.start')}
      </button>
      {ultra && <p className="muted small">{t('meeting.facts.rerun.localOnly')}</p>}
      {etat && <p className="muted small" role="status">{etat}</p>}
    </div>
  )

  const typeDe = (fait: FaitEcl) => (TYPES[fait.type] ? t(TYPES[fait.type]) : fait.type)
  const motifDe = (motif?: string | null) => (motif && MOTIFS[motif]
    ? t(MOTIFS[motif]) : t('meeting.facts.stale.reason.other', { reason: motif || '—' }))

  const sectionPerimes = perimes.length > 0 && (
    <section className="ecl-perimes" aria-label={t('meeting.facts.stale.title')}>
      <p className="muted small">
        {t('meeting.facts.stale.title')}{!lectureSeule && <> · {t('meeting.facts.stale.hint')}</>}
      </p>
      <ol className="ecl-faits">
        {perimes.map((fait) => (
          <li key={fait.id} className="ecl-fait ecl-fait-perime">
            <div className="badges">
              <Badge tone="warn">{t('meeting.facts.stale')}</Badge>
              <Badge tone="neutral">{typeDe(fait)}</Badge>
              <span className="muted small">{motifDe(fait.motif_peremption)}</span>
              <span className="muted small">{fait.horodatage}</span>
            </div>
            <blockquote className="ecl-citation muted"><s>« {fait.citation} »</s></blockquote>
            <p className="muted small">{fait.enonce}</p>
          </li>
        ))}
      </ol>
    </section>
  )

  if (erreur) return <>{relance}<p className="muted small">{t('meeting.facts.error', { reason: erreur })}</p></>
  if (faits === null) return <p className="muted small">{t('meeting.facts.loading')}</p>
  if (faits.length === 0) return <>{relance}<p className="muted">{t('meeting.facts.empty')}</p>{sectionPerimes}</>

  // Extraction partielle (des tranches en échec) : dite au-dessus des faits, la relance la complète.
  const partielle = faits.find((fait) => fait.partielle)?.partielle
  return (
    <>
      {relance}
      {partielle && (
        <p className="muted small" role="status">
          {t(partielle.tranches_en_echec === 1 ? 'meeting.facts.partial.one' : 'meeting.facts.partial.other', {
            failed: String(partielle.tranches_en_echec), total: String(partielle.tranches),
          })}
        </p>
      )}
      <ol className="ecl-faits">
        {faits.map((fait) => {
          const suite = fait.affiche && fait.affiche.startsWith(fait.citation)
            ? fait.affiche.slice(fait.citation.length).trim() : ''
          return (
            <li key={fait.id} className="ecl-fait">
              <div className="badges">
                <Badge tone="accent">{typeDe(fait)}</Badge>
                {fait.a_verifier && <Badge tone="warn">{t('meeting.facts.toVerify')}</Badge>}
                {fait.mode && (
                  <span className="muted small">
                    {t('meeting.facts.source', {
                      mode: t(fait.mode === 'local' ? 'meeting.facts.mode.local'
                        : prives.includes(fait.fournisseur || '') ? 'meeting.facts.mode.privateCloud'
                          : 'meeting.facts.mode.cloud'),
                      provider: fait.fournisseur || fait.modele || '—',
                    })}
                  </span>
                )}
                {fait.modele && (
                  <span className="muted small">{t('meeting.facts.model', { model: fait.modele })}</span>
                )}
                <button type="button" className="chip" onClick={() => onSource(fait.document, fait.horodatage)}
                  aria-label={t('meeting.facts.goToSource', { timestamp: fait.horodatage })}>
                  {fait.horodatage}
                </button>
              </div>
              <blockquote className="ecl-citation">
                « {fait.citation} »
                {suite && <span className="ecl-suite muted" title={t('meeting.facts.extended')}> … {suite}</span>}
              </blockquote>
              <p className="muted small">
                <Badge tone="neutral">{t('meeting.facts.reformulated')}</Badge> {fait.enonce}
              </p>
              {fait.porteur && <p className="muted small">{t('meeting.facts.owner', { owner: fait.porteur })}</p>}
            </li>
          )
        })}
      </ol>
      {sectionPerimes}
    </>
  )
}
