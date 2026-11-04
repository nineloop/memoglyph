import { useEffect, useId, useMemo, useRef, useState } from 'react'
import { ModeleGratuitError, getConfig, lancerCompteRendu, type AppConfig } from '../api'
import { useLang } from '../lib/langContext'
import { useCloudUltra } from '../lib/cloudUltra'
import { getLlmProvider } from '../lib/llmProvider'
import type { MessageKey } from '../lib/messages'
import { modelesPrets } from '../lib/modelesPrets'
import { useTraitementsCourants } from '../lib/traitementsContext'
import { Icon } from './Icon'

type Props = {
  /** Le transcript dont on régénère le compte rendu, et la portée où il se lit. */
  name: string
  documentScope: string
  /** Appelé quand le traitement est fini : le nom du nouveau compte rendu. */
  surTermine?: (nomDuCompteRendu: string) => void
  /** La réunion est ultra-confidentielle : sauf autorisation du compte, aucun fournisseur en ligne. */
  ultra?: boolean
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

/**
 * « Régénérer » un compte rendu en choisissant le modèle (chantier 003, tranche E). Lancé comme un TRAITEMENT : il
 * se suit dans l'indicateur de la barre haute et la vue des traitements, et sa nouvelle version s'AJOUTE aux
 * autres. La règle d'envoi reste celle du serveur : une réunion ultra-confidentielle refuse un fournisseur en
 * ligne, avec une phrase claire — l'interface ne fait que la relayer.
 */
export function RegenererCompteRendu({ name, documentScope, surTermine, ultra: ultraDocument }: Props) {
  const { t } = useLang()
  // Le compte peut autoriser le cloud pour son ultra-confidentiel (panneau Confidentialité).
  const cloudUltra = useCloudUltra()
  const ultra = Boolean(ultraDocument) && !cloudUltra
  const { liste, rafraichir, suivre } = useTraitementsCourants()
  const [ouvert, setOuvert] = useState(false)
  const [config, setConfig] = useState<AppConfig | null>(null)
  const [choix, setChoix] = useState('')
  const [envoi, setEnvoi] = useState(false)
  const [message, setMessage] = useState('')
  const [erreur, setErreur] = useState('')
  const [jobId, setJobId] = useState('')
  const idLegende = useId()
  const declencheur = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (!ouvert || config) return
    let courant = true
    getConfig().then((c) => {
      if (!courant) return
      setConfig(c)
      const prets = modelesPrets(c)
      const permis = prets.filter((p) => !ultra || (c.local_providers ?? ['mlx']).includes(p))
      const habituel = getLlmProvider(c.default_llm_provider || 'mlx')
      setChoix((precedent) => (precedent && permis.includes(precedent) ? precedent
        : permis.includes(habituel) ? habituel : permis[0] ?? ''))
    }).catch(() => { if (courant) setErreur(t('regenerer.refused.other', { erreur: '—' })) })
    return () => { courant = false }
  }, [ouvert, config, t, ultra])

  const prets = useMemo(() => (config ? modelesPrets(config) : []), [config])
  const locaux = config?.local_providers ?? ['mlx']
  // « En ligne » = hors des fournisseurs locaux (un serveur hébergé hors de cette machine en est un aussi).
  const interdit = (p: string) => ultra && !locaux.includes(p)
  const nomDe = (p: string) => (NOMS[p] ? t(NOMS[p]) : p)
  const modeleDe = (p: string) => config?.llm_models?.[p]
  const unSeulLocal = config !== null && prets.length > 0 && prets.every((p) => locaux.includes(p))

  // Le traitement lancé, suivi dans la liste partagée : fini → la nouvelle version ; en échec → on le dit.
  const suivi = jobId ? liste?.traitements.find((x) => x.id === jobId) : undefined
  const enCours = Boolean(jobId) && (!suivi || suivi.statut === 'pending' || suivi.statut === 'running')
  useEffect(() => {
    if (!suivi) return
    if (suivi.statut === 'done') {
      setJobId('')
      setMessage(t('regenerer.done'))
      if (suivi.meeting?.name) surTermine?.(suivi.meeting.name)
    } else if (suivi.statut === 'error') {
      setJobId('')
      setMessage('')
      setErreur(t('regenerer.failed'))
    }
  }, [suivi, surTermine, t])

  async function lancer() {
    if (!choix || envoi || enCours) return
    if (!locaux.includes(choix) && !window.confirm(t('regenerer.cloudConfirm', { provider: nomDe(choix) }))) return
    setEnvoi(true)
    setErreur('')
    setMessage('')
    try {
      const { job_id: id } = await lancerCompteRendu(name, documentScope, choix)
      // Le choix vaut pour CETTE régénération seulement : le fournisseur par défaut des prochains envois,
      // du compte rendu à la demande et de l'Assistant ne change pas (aucun contrôle ne change de sens selon l'écran).
      setJobId(id)
      setMessage(t('regenerer.started', { provider: nomDe(choix) }))
      rafraichir()          // le traitement apparaît tout de suite dans l'indicateur et le panneau
    } catch (raison) {
      const statut = /HTTP (\d{3})/.exec(String(raison))?.[1]
      // Un modèle gratuit refuse ce document : SA phrase, pas celle de l'ultra-confidentiel.
      setErreur(raison instanceof ModeleGratuitError ? t('job.step.error.freeModelRestricted')
        : statut === '403' ? t('regenerer.refused.confidential')
        : statut === '429' ? t('regenerer.refused.busy')
          : t('regenerer.refused.other', { erreur: String(raison) }))
    } finally {
      setEnvoi(false)
    }
  }

  return (
    <div className="regenerer"
         onKeyDown={(e) => {
           // Échap referme le panneau et rend le focus à son bouton.
           if (e.key === 'Escape' && ouvert) { e.stopPropagation(); setOuvert(false); declencheur.current?.focus() }
         }}>
      <button type="button" className="btn" ref={declencheur} aria-expanded={ouvert} onClick={() => setOuvert((o) => !o)}>
        <Icon name="report" /> {ouvert ? t('regenerer.close') : t('regenerer.open')}
      </button>
      {ouvert && (
        <div className="regenerer-panneau">
          {!config && !erreur && <p className="muted">{t('regenerer.loading')}</p>}
          {config && prets.length === 0 && <p className="muted">{t('regenerer.none')}</p>}
          {config && prets.length > 0 && (
            <fieldset className="regenerer-choix" aria-labelledby={idLegende}>
              <legend id={idLegende}>{t('regenerer.legend')}</legend>
              {prets.map((p) => (
                <label key={p} className={`regenerer-option ${interdit(p) ? 'interdit' : ''}`}>
                  <input type="radio" name={`regenerer-${name}`} value={p} checked={choix === p}
                         onChange={() => setChoix(p)} disabled={envoi || enCours || interdit(p)}
                         aria-describedby={interdit(p) ? `${idLegende}-raison-${p}` : undefined} />
                  <span>
                    <strong>{nomDe(p)}</strong>
                    {modeleDe(p) && <small> · <code>{modeleDe(p)}</code></small>}
                    {/* La raison est du TEXTE, lié à l'option : une option désactivée ne reçoit pas le focus. */}
                    {interdit(p) && <small id={`${idLegende}-raison-${p}`} className="regenerer-raison"> — {t('regenerer.notAllowed')}</small>}
                  </span>
                </label>
              ))}
            </fieldset>
          )}
          {unSeulLocal && <p className="muted regenerer-aide">{t('regenerer.noOnline')}</p>}
          {config && prets.length > 0 && (
            <button type="button" className="btn accent llm" onClick={lancer} disabled={!choix || envoi || enCours}>
              {t('regenerer.launch')}
            </button>
          )}
          {message && (
            <p className="regenerer-etat" role="status">
              {message}{' '}
              {jobId && <button type="button" className="btn sm" onClick={suivre}>{t('traitements.confirm.follow')}</button>}
            </p>
          )}
          {erreur && <p className="error" role="alert">{erreur}</p>}
        </div>
      )}
    </div>
  )
}
