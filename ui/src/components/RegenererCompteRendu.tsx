import { useEffect, useMemo, useRef, useState } from 'react'
import { ModeleGratuitError, getConfig, getReglagesTraitement, lancerCompteRendu, type AppConfig } from '../api'
import { useLang } from '../lib/langContext'
import { useConfirmer } from '../design/Confirmation'
import { phraseDErreur } from '../lib/erreurs'
import { useCloudUltra } from '../lib/cloudUltra'
import { LIBELLES, estEnLigne, estInterdit, estPret, fournisseursConnus } from '../lib/fournisseurs'
import { fournisseurLocal, fournisseurRetenuIndisponible, reglagesApplicables } from '../lib/reglagesTraitement'
import { useTraitementsCourants } from '../lib/traitementsContext'
import { Icon } from './Icon'
import { SelecteurFournisseur } from './SelecteurFournisseur'

type Props = {
  /** Le transcript dont on régénère le compte rendu, et la portée où il se lit. */
  name: string
  documentScope: string
  /** Appelé quand le traitement est fini : le nom du nouveau compte rendu. */
  surTermine?: (nomDuCompteRendu: string) => void
  /** La réunion est ultra-confidentielle : sauf autorisation du compte, aucun fournisseur en ligne. */
  ultra?: boolean
  /** Le panneau s'affiche ouvert (état vide : « Rédiger le compte rendu » ouvre directement le choix). */
  ouvertInitial?: boolean
  /** Une première rédaction, pas une régénération : seuls les libellés du bouton changent. */
  redaction?: boolean
  /** Chaque CHANGEMENT de ce nombre, après l'affichage, ouvre le panneau (un autre onglet de la réunion le demande). */
  demandeOuverture?: number
  /** Le transcript est sans horodatage : un compte rendu n'y citerait rien. L'entrée est désactivée, avec l'explication. */
  sansHorodatage?: boolean
}

/**
 * « Régénérer » un compte rendu en choisissant le modèle. Lancé comme un TRAITEMENT : il
 * se suit dans l'indicateur de la barre haute et la vue des traitements, et sa nouvelle version s'AJOUTE aux
 * autres. La règle d'envoi reste celle du serveur : une réunion ultra-confidentielle refuse un fournisseur en
 * ligne, avec une phrase claire — l'interface ne fait que la relayer.
 */
export function RegenererCompteRendu({ name, documentScope, surTermine, ultra: ultraDocument, ouvertInitial = false, redaction = false, demandeOuverture = 0, sansHorodatage = false }: Props) {
  const { t, lang } = useLang()
  const confirmer = useConfirmer()
  // Le compte peut autoriser le cloud pour son ultra-confidentiel (panneau Confidentialité).
  const cloudUltra = useCloudUltra()
  const { liste, rafraichir, suivre } = useTraitementsCourants()
  const [ouvertDemande, setOuvert] = useState(ouvertInitial)
  // Sans horodatage, rien ne s'ouvre : l'entrée reste fermée, désactivée, avec son explication.
  const ouvert = ouvertDemande && !sansHorodatage
  const [config, setConfig] = useState<AppConfig | null>(null)
  const [choix, setChoix] = useState('')
  // Le fournisseur retenu par le compte mais pas configuré : dit en une ligne tant que la personne n'a rien choisi.
  const [retenuIndisponible, setRetenuIndisponible] = useState('')
  const [envoi, setEnvoi] = useState(false)
  const [message, setMessage] = useState('')
  const [erreur, setErreur] = useState('')
  const [jobId, setJobId] = useState('')
  // Le dernier traitement qui a ÉCHOUÉ : « Suivre » reste affiché à côté du message, qui renvoie à la vue des traitements.
  const [dernierJob, setDernierJob] = useState('')
  const declencheur = useRef<HTMLButtonElement>(null)

  // Seul un CHANGEMENT après l'affichage ouvre le panneau : une valeur déjà non nulle au montage (la barre d'outils qui
  // apparaît après une première rédaction) ne l'ouvre pas.
  const derniereDemande = useRef(demandeOuverture)
  useEffect(() => {
    if (demandeOuverture === derniereDemande.current) return
    derniereDemande.current = demandeOuverture
    setOuvert(true)
  }, [demandeOuverture])

  useEffect(() => {
    if (!ouvert || config) return
    let courant = true
    Promise.all([getConfig(), getReglagesTraitement().catch(() => ({ reglages: null }))]).then(([c, retenus]) => {
      if (!courant) return
      setConfig(c)
      setRetenuIndisponible(fournisseurRetenuIndisponible(retenus.reglages, c) ?? '')
      // Le fournisseur de départ : celui que le compte a retenu s'il est prêt, sinon le modèle local (`reglagesApplicables`).
      const permis = fournisseursConnus(c).filter((f) => estPret(f) && !estInterdit(f, Boolean(ultraDocument), cloudUltra)).map((f) => f.id)
      const habituel = reglagesApplicables(retenus.reglages, c)?.fournisseur ?? fournisseurLocal(c)
      setChoix((precedent) => (precedent && permis.includes(precedent) ? precedent
        : permis.includes(habituel) ? habituel : permis[0] ?? ''))
    }).catch(() => { if (courant) setErreur(t('regenerer.refused.other')) })
    return () => { courant = false }
  }, [ouvert, config, t, ultraDocument, cloudUltra])

  // La sensibilité de la réunion (ou l'autorisation du compte) peut être connue APRÈS l'ouverture du panneau : le choix est
  // revalidé à chaque changement, sinon un fournisseur en ligne retenu resterait coché, refusé ensuite par le serveur.
  useEffect(() => {
    if (!config) return
    const permis = fournisseursConnus(config).filter((f) => estPret(f) && !estInterdit(f, Boolean(ultraDocument), cloudUltra)).map((f) => f.id)
    setChoix((precedent) => {
      if (precedent && permis.includes(precedent)) return precedent
      const local = fournisseurLocal(config)
      return permis.includes(local) ? local : permis[0] ?? ''
    })
  }, [config, ultraDocument, cloudUltra])

  const connus = useMemo(() => (config ? fournisseursConnus(config) : []), [config])
  const prets = useMemo(() => connus.filter(estPret).map((f) => f.id), [connus])
  // « En ligne » = hors des fournisseurs locaux (un serveur hébergé hors de cette machine en est un aussi).
  const enLigne = (p: string) => { const f = connus.find((x) => x.id === p); return f ? estEnLigne(f) : true }
  const nomDe = (p: string) => (LIBELLES[p] ? t(LIBELLES[p]) : p)
  const unSeulLocal = config !== null && prets.length > 0 && prets.every((p) => !enLigne(p))

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
      setDernierJob(suivi.id)
      setErreur(t('regenerer.failed'))
    }
  }, [suivi, surTermine, t])

  async function lancer() {
    if (!choix || envoi || enCours) return
    if (enLigne(choix) && !await confirmer({ titre: t('confirmation.envoiEnLigne.titre'), phrase: t('regenerer.cloudConfirm', { provider: nomDe(choix) }), libelleAction: t('confirmation.envoiEnLigne.action') })) return
    setEnvoi(true)
    setErreur('')
    setDernierJob('')
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
          : phraseDErreur(t, lang, raison, t('regenerer.refused.other')))
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
      <button type="button" className="btn" ref={declencheur} aria-expanded={ouvert} onClick={() => setOuvert((o) => !o)}
              disabled={sansHorodatage} title={sansHorodatage ? t('reader.generate.needsTimestamps') : undefined}>
        <Icon name={ouvert ? 'close' : 'report'} /> {ouvert ? t('regenerer.close') : t(redaction ? 'regenerer.write' : 'regenerer.open')}
      </button>
      {ouvert && (
        <div className="regenerer-panneau">
          {!config && !erreur && <p className="muted">{t('regenerer.loading')}</p>}
          {config && prets.length === 0 && <p className="muted">{t('regenerer.none')}</p>}
          {config && prets.length > 0 && (
            <SelecteurFournisseur config={config} valeur={choix} onChange={(p) => { setRetenuIndisponible(''); setChoix(p) }} legende={t('regenerer.legend')}
                                  nom={`regenerer-${name}`} ultra={Boolean(ultraDocument)} cloudUltra={cloudUltra}
                                  desactive={envoi || enCours} enLigneOuvert />
          )}
          {retenuIndisponible && (
            <p className="muted regenerer-aide" role="status">
              {t('reglages.retenuIndisponible', { fournisseur: LIBELLES[retenuIndisponible] ? t(LIBELLES[retenuIndisponible]) : retenuIndisponible })}
            </p>
          )}
          {unSeulLocal && (
            <p className="muted regenerer-aide">
              {t('regenerer.noOnline')} {t('regenerer.addProvider')}
            </p>
          )}
          {config && prets.length > 0 && (
            <button type="button" className="btn accent" onClick={lancer} disabled={!choix || envoi || enCours}>
              {t(redaction ? 'regenerer.writeLaunch' : 'regenerer.launch')}
            </button>
          )}
          {message && (
            <p className="regenerer-etat" role="status">
              {message}{' '}
              {jobId && <button type="button" className="btn sm" onClick={suivre}>{t('traitements.confirm.follow')}</button>}
            </p>
          )}
          {erreur && <p className="error" role="alert">{erreur}</p>}
          {erreur && dernierJob && <button type="button" className="btn sm" onClick={suivre}>{t('traitements.confirm.follow')}</button>}
        </div>
      )}
    </div>
  )
}
