"""Orchestrateur : enchaîne extraction → STT → (diarisation) → écriture .md."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from . import artifact_policy
from . import config
from . import docid
from . import llm
from . import media
from . import messages
from . import projects
from . import provenance
from . import resources
from . import sensitivity as sens
from . import telemetry
from . import writer_md
from .stt import DEFAULT_ENGINE, engine_spec, get_engine
from .stt import est_local as stt_est_local
from .stt_base import Segment, Transcript

# Callback de progression : (fraction 0..1, message)
Progress = Callable[[float, str], None]


def _project_id_for_output(output_dir: Path) -> str:
    """Identité autoritaire du projet de destination.

    Le dossier est un emplacement ; le manifeste porte l'identité. Une
    destination sans manifeste est refusée plutôt que d'inventer un projet au
    milieu d'une transcription.
    """
    return projects.load(Path(output_dir)).id


def _proprietaire_de(output_dir: Path) -> Optional[str]:
    """Le compte propriétaire du projet de destination, ou None (aucune autorisation).

    Comme `library.proprietaire_du_document` : le manifeste du dossier fait foi.
    """
    try:
        return projects.load(Path(output_dir)).owner
    except Exception:  # noqa: BLE001 — sans manifeste lisible, personne n'autorise
        return None


def _scaled(progress: Optional[Progress], lo: float, hi: float) -> Progress:
    """Renvoie un callback qui mappe une fraction locale 0..1 vers [lo, hi]."""
    def _cb(frac: float, msg: str) -> None:
        if progress:
            progress(lo + (hi - lo) * max(0.0, min(1.0, frac)), msg)
    return _cb


def _etat_cache_hf(repo: str) -> str:
    """« download » si le repo n'est prouvablement PAS dans le cache Hugging Face,
    « load » s'il l'est, « unknown » si on ne peut pas trancher.

    Lecture SEULE du cache — jamais le cycle de vie MLX. Sert à distinguer, au
    premier usage du modèle LOCAL de compte-rendu, un téléchargement (plusieurs
    Go) d'un simple chargement, et seulement quand le code peut le PROUVER.
    """
    if Path(repo).exists():          # modèle sur disque (chemin) : rien à tirer
        return "load"
    try:
        from huggingface_hub import try_to_load_from_cache
    except ImportError:  # paquet absent (ex. CI sans stack ML) → on ne prétend rien
        return "unknown"
    for nom in ("config.json", "model.safetensors", "weights.npz"):
        try:
            trouve = try_to_load_from_cache(repo_id=repo, filename=nom)
        except Exception:  # noqa: BLE001
            return "unknown"
        if isinstance(trouve, str) and trouve:
            return "load"
    return "download"


def _annoncer_modele_local(report: Callable, frac: float, provider: str) -> None:
    """Avant le PREMIER appel au modèle LOCAL, dire qu'il se prépare.

    Sans cette annonce, la barre paraît figée pendant que `mlx_lm.server`
    télécharge (~2 Go) puis charge Qwen au premier compte-rendu — le pendant CR
    de l'annonce côté STT. Un fournisseur cloud n'a rien à télécharger
    localement.

    On lit le cache Hugging Face (lecture SEULE) pour distinguer téléchargement
    de chargement quand c'est prouvable ; sinon un message honnête générique. On
    ne touche PAS `core/llm_mlx.py` ni le cycle de vie MLX.
    """
    if not llm.est_local(provider):
        return
    repo = config.MLX_LLM_MODEL
    nom = repo.rsplit("/", 1)[-1]
    etat = _etat_cache_hf(repo)
    if etat == "download":
        report(frac, messages.telechargement_modele(nom))
    elif etat == "load":
        report(frac, messages.chargement_modele(nom))
    else:
        report(frac, messages.preparation_modele_local())


def run(
    src: Path,
    **kwargs,
) -> tuple[Path, str]:
    """Traite un fichier média et écrit le .md. Retourne (chemin_md, contenu_md).

    Enveloppe `_run` d'une réservation de ressource : STT, diarisation et LLM se
    disputent la même RAM unifiée. Tâche de FOND, donc elle **attend son tour**
    plutôt que d'échouer — une transcription lancée doit aboutir, quitte à
    démarrer plus tard. Les chemins interactifs (CR, RAG), eux, refusent tout de
    suite plutôt que de faire patienter sans signal.
    """
    with resources.heavy(wait=900, label="traitement"):
        return _run(Path(src), **kwargs)


def _run(
    src: Path,
    *,
    engine_name: str = DEFAULT_ENGINE,
    language: Optional[str] = None,
    mistral_api_key: Optional[str] = None,
    llm_provider: str = llm.FOURNISSEUR_DEFAUT,
    diarize_on: bool = False,
    profile_on: bool = False,
    report_on: bool = False,
    timestamps: bool = True,
    sensitivity: str = sens.SENSIBILITE_DEFAUT,
    capture: Optional[dict] = None,
    progress: Optional[Progress] = None,
    output_dir: Optional[Path] = None,
    on_partial: Optional[Callable[[str], None]] = None,
    derived_from_document_id: Optional[str] = None,
) -> tuple[Path, str]:
    """Traite un fichier média et écrit le .md. Retourne (chemin_md, contenu_md).

    `capture` : provenance d'un enregistrement du compagnon de capture
    (`application`, `mode`), validée par l'appelant et recopiée telle quelle.
    """
    config.load_env()
    src = Path(src)

    # CONTRAT D'ABORD, travail ensuite.
    #
    # La spécification du moteur — dont sa politique d'artefacts — est résolue
    # avant la moindre I/O : un moteur qui ne déclare rien fait échouer l'appel
    # ici, sans qu'on ait extrait l'audio ni chargé un modèle. C'est ce qu'on
    # appelle une erreur DURE de contrat, par opposition à un détecteur
    # qui échoue en cours de route (traité plus bas, transcript conservé).
    spec = engine_spec(engine_name)

    def report(frac: float, msg: str) -> None:
        # `msg` est un `messages.Message` : une chaîne qui porte en plus son code
        # et ses paramètres, pour que l'interface traduise sans deviner.
        if progress:
            progress(frac, msg)

    wav_path: Optional[Path] = None
    try:
        # 1. Extraction / normalisation audio (0–10 %)
        report(0.01, messages.preparation_audio())
        media.validate_input(src)
        duration = media.probe_duration(src)
        wav_path = media.to_wav16k(src)
        input_pcm_sha256 = media.pcm_sha256(wav_path)
        report(0.10, messages.audio_pret())

        # 2. Transcription (10–50 %)
        # Identité STABLE du document, MINTÉE AVANT la transcription pour que l'exécution FinOps
        # `stt` porte le `document_id` du transcript (sinon l'exécution était persistée sans
        # rattachement — la meta la réutilise plus bas, elle n'est plus re-mintée). Mintée avant
        # le moteur, aussi, pour que la règle d'envoi la trace. N'affecte aucun comportement
        # produit : mêmes ULID, générés un peu plus tôt.
        document_id = docid.nouveau()
        meeting_id = docid.nouveau()
        # La règle de sensibilité AVANT le moteur cloud (`sensitivity.verifier_envoi`) :
        # l'autorisation est celle du propriétaire du projet de destination. Contrôlée
        # ici sans rien construire, puis relue juste avant l'envoi de l'audio : un
        # travail qui a attendu son tour n'envoie pas sur un réglage désactivé entre-temps.
        envoi = sens.Envoi(sensibilite=sensitivity,
                           proprietaire=_proprietaire_de(output_dir or config.OUTPUT_DIR),
                           document_id=document_id)
        stt_local = stt_est_local(engine_name)
        sens.controler(envoi, local=stt_local)
        # Vrai quand un ultra-confidentiel est parti vers le cloud avec autorisation
        # (transcription ou profil) : la provenance du transcript le dit.
        ultra_autorise = False
        stt_kwargs: dict[str, str | None] = {"language": language}
        if mistral_api_key:
            stt_kwargs["api_key"] = mistral_api_key
        # `diarize_on` doit atteindre les moteurs qui diarisent eux-mêmes,
        # sinon l'option de l'utilisateur est sans effet en mode cloud.
        engine = get_engine(engine_name, diarize=diarize_on, **stt_kwargs)
        # Texte poussé au fil de l'eau (delta) si le moteur sait le faire.
        on_seg = None
        if on_partial:
            def on_seg(segs):  # noqa: E306
                chunk = " ".join(s.text for s in segs).strip()
                if chunk:
                    on_partial(chunk)
        # Nom imposé par `stt_base.STTEngine` — voir le contrat, et le test qui
        # le vérifie. Passé seulement si utile : un moteur sans callback n'a pas
        # à recevoir un kwarg dont il ne fait rien.
        kw = {"on_partial": on_seg} if on_seg else {}
        # `project_id` du projet de DESTINATION, résolu TÔT et porté par l'exécution `stt`,
        # exactement comme le CR (`app_report`). Sans lui, l'exécution STT restait à
        # `project_id=NULL` et son coût — dominant pour une transcription locale — n'apparaissait
        # dans AUCUNE vue Usage bornée par projet, seulement en global : la dépense de
        # transcription d'un projet devenait invisible. Best-effort : la résolution du projet ne
        # doit JAMAIS casser la transcription (un dossier sans manifeste → NULL, comme avant).
        try:
            stt_project_id = _project_id_for_output(output_dir or config.OUTPUT_DIR)
        except Exception:  # noqa: BLE001 — la télémétrie ne casse jamais la génération
            stt_project_id = None
        # Une EXÉCUTION FinOps `stt` (best-effort, dark) borne la transcription :
        # wall-clock ici, et l'appel fournisseur (usage Mistral STT s'il l'expose)
        # enregistré depuis le moteur. Ne change rien au contrat de transcription.
        with telemetry.execution(
            kind="stt",
            document_id=document_id,
            meeting_id=meeting_id,
            project_id=stt_project_id,
            provider=getattr(engine, "name", engine_name),
            model=getattr(engine, "model_name", None),
            # Quantité STT MESURÉE (durée audio, déjà sondée) : métrique NON-jeton pour un
            # éventuel coût imputé (Whisper local n'a pas de jetons). Best-effort.
            audio_seconds=(float(duration) if isinstance(duration, (int, float)) else None),
        ):
            ultra_autorise = sens.autoriser_envoi(
                envoi, local=stt_local, fournisseur=getattr(engine, "name", engine_name))
            transcript = engine.transcribe(wav_path, progress=_scaled(progress, 0.10, 0.50), **kw)
        engine_label = getattr(engine, "name", engine_name)
        # Le modèle SERVI l'emporte sur le modèle DEMANDÉ quand le fournisseur
        # le déclare. `voxtral-mini-latest` est un alias : l'inscrire dans la
        # provenance nommait l'intention, pas le fait, et deux documents
        # produits à six mois d'écart portaient la même chaîne sans venir du
        # même modèle. Un moteur qui ne déclare rien laisse `None`, et l'on
        # retombe sur le nom demandé — en le sachant.
        engine_model = (
            getattr(transcript, "model_servi", None)
            or getattr(engine, "model_name", config.WHISPER_MODEL)
        )

        # Segments vides écartés avant le détecteur : il compte des mots, un
        # segment sans texte ne dit rien et fausserait les ratios.
        segments_propres = [
            Segment(start=s.start, end=s.end, text=s.text, speaker=s.speaker)
            for s in transcript.segments
            if s.text and s.text.strip()
        ]
        if len(segments_propres) != len(transcript.segments):
            transcript = Transcript(
                segments=segments_propres,
                full_text=" ".join(s.text for s in segments_propres),
                language=transcript.language,
                model_servi=transcript.model_servi,
            )

        # REPLI de langue : uniquement quand le moteur n'en déclare aucune.
        #
        # Voxtral ne la déclare JAMAIS. `profiling._lang_directive(None)` retombe
        # alors sur une consigne générique, là où les directives par langue sont
        # rédigées DANS la langue cible — et `core/profiling.py` documente
        # pourquoi ça compte : « une consigne en français le fait répondre en
        # français, même si elle dit réponds en anglais ».
        #
        # MESURÉ : un entretien d'embauche ANGLAIS transcrit par Voxtral
        # a été profilé en FRANÇAIS. Le même média en local, où Whisper déclare
        # `en`, rend des rôles anglais.
        #
        # On ne DEVINE pas : `langue_probable` rend `None` dès que l'écriture est
        # latine sans mots-outils tranchants, et le repli générique s'applique
        # comme avant. Se tromper de langue est pire que ne pas en imposer — un
        # mauvais ancrage produit un document entier dans la mauvaise langue,
        # avec l'assurance d'une consigne explicite.
        #
        # Et on n'ÉCRASE jamais : un moteur qui déclare `ar` est cru.
        if not transcript.language:
            from . import quality

            deduite = quality.langue_probable(s.text for s in segments_propres)
            if deduite:
                report(0.50, messages.langue_deduite(deduite))
                transcript = Transcript(
                    segments=transcript.segments,
                    full_text=transcript.full_text,
                    language=deduite,
                    model_servi=transcript.model_servi,
                )
        engine = None  # on n'en a plus besoin

        # LA politique déclarée par le registre, toujours appliquée, jamais
        # devinée. Plus de `hasattr` : c'est la déclaration qui est obligatoire,
        # pas une seconde méthode sur chaque moteur.
        #
        # Un échec ICI ne perd pas le transcript — il produit
        # `artifact_status: failed` avec son erreur. Voir `apply_policy`.
        transcript, evaluation = artifact_policy.apply_policy(
            spec.artifact_policy, transcript
        )

        # Si le moteur STT a fourni sa propre diarisation (ex: Voxtral Mini native)
        already_diarized = any(s.speaker for s in transcript.segments if s.text and s.text.strip())
        diarized = already_diarized
        embeddings: dict[str, list[float]] = {}
        erreur_empreintes: Optional[str] = None
        # Même statut que le profilage et le compte-rendu : la diarisation est un
        # enrichissement OPTIONNEL, son échec ne coûte pas la transcription.
        erreur_diarisation: Optional[str] = None
        erreur_profilage: Optional[str] = None
        profile_data: Optional[dict] = None

        # ── Persistance IMMÉDIATE après STT, puis mise à jour EN PLACE ──
        #
        # Le travail irremplaçable — la transcription — est écrit sur le disque
        # AVANT les enrichissements, et réécrit AU MÊME endroit une fois enrichi.
        # Les échecs de diarisation/profilage/CR sont déjà rattrapés (`try`
        # partout), mais un CRASH NATIF Metal/MLX pendant le
        # profilage ou le CR tue le processus sans passer par un `except` : sans
        # cette écriture précoce, une transcription réussie serait perdue. Ces
        # crashs sont réels — c'est la raison des processus de test isolés et des
        # serveurs MLX qui se figent.
        #
        # Identité, chemin et horodatage FIGÉS une seule fois → persistance
        # idempotente. `unique_path` n'est appelé qu'ICI (deux appels
        # choisiraient deux noms et créeraient un doublon) ; `write_md` écrase.
        # L'indexation se fait dans l'appelant APRÈS le retour de `run()` : le
        # fichier précoce n'est donc jamais indexé deux fois.
        modele_profilage = llm.modele_servi(llm_provider)
        now = datetime.now()
        target_dir = output_dir or config.OUTPUT_DIR
        # `document_id`/`meeting_id` sont MINTÉS plus haut (avant l'exécution FinOps `stt`) et
        # réutilisés tels quels ici — pas de re-génération (deux ULID divergeraient).
        out_path = writer_md.unique_path(src.stem, target_dir)

        def _ecrire(*, complet: bool) -> str:
            """Construit le front-matter, rend le Markdown et l'écrit sur `out_path`.

            Appelée DEUX fois — transcription seule (`complet=False`), puis
            enrichie (`complet=True`) — sur le MÊME chemin, avec le MÊME
            `document_id`. Une SEULE fonction construit la meta : deux
            constructions divergeraient, hantise documentée partout ici. Elle
            LIT `transcript`, `diarized`, `profile_data` et les `erreur_*` à
            l'appel, donc le second appel voit l'état enrichi sans qu'on ait à
            le lui passer.

            `complet` gouverne le marqueur `interrupted` : l'écriture précoce le
            pose, l'écriture finale l'omet. Un document trouvé sur le disque
            avec `interrupted: true` a donc été écrit mais jamais finalisé — un
            crash natif l'a arrêté entre les deux écritures.
            """
            n_speakers = (
                len({s.speaker for s in transcript.segments if s.speaker})
                if diarized else None
            )
            meta = {
                "id": document_id,
                "meeting_id": meeting_id,
                "project_id": _project_id_for_output(target_dir),
                "title": writer_md.slugify(src.stem),
                "date": now.strftime("%Y-%m-%d"),
                "duration": writer_md.fmt_duration(duration) if duration else None,
                "language": transcript.language,
                "engine": engine_label,
                "model": engine_model,
                "source": src.name,
                # Sensibilité CHOISIE par l'utilisateur — métadonnée pure (aucun
                # effet sur l'exécution aujourd'hui). Toujours écrite (même le
                # défaut) pour qu'un futur étage de politique la trouve
                # explicitement ; un document sans le champ lit `standard`.
                "sensitivity": sens.normaliser(sensitivity),
                # Absente hors du compagnon de capture (writer_md écarte les `None`).
                "capture": capture or None,
                "diarization": diarized,
                # Présents SEULEMENT en cas d'échec (writer_md écarte les `None`) :
                # un champ absent ne prétend rien, `null` obligerait à distinguer
                # « pas essayé » de « réussi ».
                "voiceprint_error": erreur_empreintes,
                "diarization_error": erreur_diarisation,
                "profiling_error": erreur_profilage,
                # Présent SEULEMENT tant que le pipeline n'a pas été finalisé
                # (writer_md écarte les `None`). L'écriture précoce le pose,
                # l'écriture finale l'omet : un document complet ne le porte
                # jamais, un document interrompu par un crash le conserve. Le
                # statut du JOB dit déjà « error » ; ce marqueur rend le DOCUMENT
                # lui-même honnête, y compris après une réindexation qui le
                # ferait réapparaître sans son job.
                "interrupted": None if complet else True,
                # Présent SEULEMENT quand un document ultra-confidentiel est parti vers un
                # fournisseur cloud (transcription ou profil) sous l'autorisation de son
                # propriétaire : la provenance le dit, comme l'exécution FinOps.
                "envoi_ultra_autorise": True if ultra_autorise else None,
                "n_speakers": n_speakers,
                # Publication DÉTERMINISTE de l'évaluation : état, politique et
                # version écrits quoi qu'il arrive.
                **evaluation.front_matter(),
                "profiled": bool(profile_data),
                # Le MÊME modèle que le fingerprint, calculé une seule fois
                # (un profilage Mistral ne doit pas annoncer Qwen au
                # premier niveau).
                "llm_model": modele_profilage if profile_data else None,
                "generated_at": now.strftime("%Y-%m-%dT%H:%M:%S"),
                "derived_from_document_id": derived_from_document_id,
                "pipeline": provenance.transcript_fingerprint(
                    engine=engine_label,
                    model=engine_model,
                    language_requested=language,
                    timestamps=timestamps,
                    diarized=diarized,
                    profiled=bool(profile_data),
                    input_pcm_sha256=input_pcm_sha256,
                    derived_from_document_id=derived_from_document_id,
                    # Ce qui a RÉELLEMENT tourné, pas ce qui est configuré.
                    artifact_status=evaluation.status.value,
                    artifact_policy=evaluation.policy_id,
                    artifact_policy_version=evaluation.policy_version,
                    diarization_model=(
                        f"{engine_label}:{engine_model}" if already_diarized
                        else (config.DIARIZATION_MODEL if diarized else None)
                    ),
                    profiling_model=modele_profilage,
                ),
            }
            content = writer_md.render_markdown(
                transcript, meta, timestamps=timestamps, diarized=diarized,
                profile=profile_data,
            )
            writer_md.write_md(content, out_path)
            return content

        # Écriture précoce : la transcription seule, sur le disque immédiatement,
        # marquée `interrupted` jusqu'à la finalisation.
        _ecrire(complet=False)

        # 3. Diarisation optionnelle Pyannote (50–65 %) — si non déjà diarisé nativement
        has_timestamps = any(s.start != 0.0 or s.end != 0.0 for s in transcript.segments)
        if diarize_on and has_timestamps and not already_diarized:
            report(0.50, messages.diarisation())
            from .diarization import diarize
            from .merge import merge_speakers

            try:
                diar = diarize(wav_path, config.HF_TOKEN, progress=_scaled(progress, 0.50, 0.65))
                # Le mapping vient de la fusion elle-même : le recalculer ici à
                # partir des tours donnerait une numérotation différente et
                # décalerait les empreintes par rapport aux « Speaker N » du .md.
                segments, label_map = merge_speakers(transcript.segments, diar.turns)
                transcript = Transcript(
                    segments=segments,
                    full_text=transcript.full_text,
                    language=transcript.language,
                )
                embeddings = {
                    label_map[raw]: vec
                    for raw, vec in diar.embeddings.items()
                    if raw in label_map
                }
                diarized = True
            except Exception as exc:  # noqa: BLE001 — le transcript reste généré
                # Ce bloc était le SEUL des trois enrichissements optionnels sans
                # garde, alors que
                # `profiling` et `make_report` en ont une depuis longtemps avec
                # le commentaire « le transcript reste généré ». Une diarisation
                # qui lève — dépendance `--group diar` absente, HF_TOKEN refusé,
                # crash pyannote — emportait une transcription déjà réussie. Le
                # texte reste bon sans locuteurs ; le `.md` porte la trace, pas
                # seulement stdout (même règle que `voiceprint_error`).
                erreur_diarisation = f"{type(exc).__name__}: {exc}"
                report(0.65, messages.diarisation_echouee(str(exc)))
                print(f"[diarisation] impossible sur {src.name} : {exc}")

        # 3 bis. Le moteur a diarisé lui-même (Voxtral) : il rend des étiquettes
        # de locuteur, jamais de vecteurs. Sans ce bloc, une réunion transcrite
        # en cloud laisse l'écran Voix VIDE et aucune de ses voix n'est
        # rapprochable d'une réunion locale — c'était le trou de l'étape B.
        # On ré-embarque donc sur SES bornes, avec le même checkpoint que le
        # chemin local : espace vectoriel partagé, vérifié avant d'écrire ceci.
        elif diarize_on and already_diarized and has_timestamps:
            report(0.50, messages.empreintes_moteur_diarise())
            from .diarization import empreintes_par_locuteur, unload_empreinte

            tours_moteur: dict[str, list[tuple[float, float]]] = {}
            for s in transcript.segments:
                if s.speaker and s.end > s.start:
                    tours_moteur.setdefault(s.speaker, []).append((s.start, s.end))
            try:
                embeddings = empreintes_par_locuteur(
                    wav_path, tours_moteur, hf_token=config.HF_TOKEN,
                )
            except Exception as exc:  # noqa: BLE001
                # Le texte reste bon sans empreintes : on n'annule pas une
                # transcription réussie parce que l'annexe a échoué. Mais on le
                # dit AILLEURS QUE DANS STDOUT : un échec qui ne sort que dans les
                # logs n'est pas dit. Le `.md` fait foi : c'est lui qui doit porter
                # la trace, sinon un écran Voix vide ressemble à un corpus sans
                # voix plutôt qu'à une extraction ratée.
                erreur_empreintes = f"{type(exc).__name__}: {exc}"
                print(f"[empreinte] extraction impossible sur {src.name} : {exc}")
                embeddings = {}
            finally:
                unload_empreinte()

        # `modele_profilage` est résolu par le MOTEUR (seul à savoir ce qu'il
        # sert), une seule fois, plus haut — avant l'écriture précoce. Le
        # calculer deux fois laissait la provenance annoncée diverger du moteur réel.

        # Libère les poids STT dès qu'ils ont servi, AVANT toute étape LLM.
        #
        # Ce déchargement était à l'intérieur du `if profile_on:` : un parcours
        # « profilage désactivé, compte-rendu activé » — parfaitement courant —
        # n'essayait donc même pas de rendre Whisper avant de charger Qwen, et
        # les deux se disputaient la RAM unifiée.
        #
        # Le placer ici le rend inconditionnel : la transcription est terminée à
        # ce point, quoi qu'il arrive ensuite, et rien ne justifie de garder
        # 2,6 Go de poids qui ne resserviront pas dans ce passage.
        import gc

        from . import stt
        stt.unload()
        gc.collect()

        # 4. Profilage LLM optionnel (65–75 %)
        #
        # OPTIONNEL veut dire que son échec ne coûte pas le transcript. Ça n'a
        # pas toujours été le cas : cet appel n'avait aucun `try`, et
        # `profiling.profile` lève quand le fournisseur ne répond pas
        # (`core/profiling.py`). Or on est ici à l'étape 4, et le Markdown ne
        # s'écrit qu'à l'étape 5 — une transcription DÉJÀ RÉUSSIE, diarisation
        # comprise, était donc perdue parce qu'un serveur LLM était éteint.
        #
        # L'asymétrie était dans cette fonction même : le compte-rendu de
        # l'étape 6 est enveloppé depuis toujours, avec le commentaire « le
        # transcript reste généré ». Deux enrichissements optionnels du même
        # fournisseur, deux traitements opposés, et c'est celui d'AVANT la
        # publication qui n'était pas protégé. (`profile_data` / `erreur_profilage`
        # sont initialisés plus haut, avant l'écriture précoce.)
        if profile_on:
            # Le profilage est le PREMIER usage du modèle local (avant le CR) :
            # c'est ici que Qwen se télécharge/charge. On l'annonce juste avant.
            _annoncer_modele_local(report, 0.64, llm_provider)
            report(0.65, messages.profilage(modele_profilage))
            from . import profiling

            try:
                # Une exécution FinOps `profile` à elle, comme le CR (`app_report`) : hors
                # exécution, la télémétrie est un no-op et les appels du profil n'étaient
                # ni comptés ni chiffrés. Même document, réunion et projet que la
                # transcription : son coût apparaît dans la vue Usage de ce projet.
                with telemetry.execution(
                    kind="profile",
                    document_id=document_id,
                    meeting_id=meeting_id,
                    project_id=stt_project_id,
                    provider=llm_provider,
                    model=modele_profilage,
                ), telemetry.stage("profile"):
                    profile_data = profiling.profile(
                        transcript,
                        diarized=diarized,
                        provider=llm_provider,
                        mistral_api_key=mistral_api_key,
                        progress=_scaled(progress, 0.65, 0.75),
                        envoi=envoi,
                    )
                # Le profil a abouti : s'il est ultra-confidentiel et parti en cloud, c'est
                # que la façade l'a autorisé (`llm.chat_json`, `envoi=`). Déduit, pas relu :
                # un réglage désactivé après coup ne change pas ce qui est déjà parti.
                ultra_autorise = ultra_autorise or (
                    sens.normaliser(sensitivity) == sens.ULTRA_CONFIDENTIEL
                    and not llm.est_local(llm_provider))
            except Exception as exc:  # noqa: BLE001 — le transcript reste généré
                # Même forme que `erreur_empreintes` : le `.md` porte la trace,
                # pas seulement stdout. Un profil absent ressemble sinon à un
                # enregistrement sans contenu analysable plutôt qu'à une panne.
                erreur_profilage = f"{type(exc).__name__}: {exc}"
                report(0.75, messages.profilage_echoue(str(exc)))
                print(f"[profilage] impossible sur {src.name} : {exc}")

        # 5. Ré-écriture ENRICHIE (75–80 %) — MÊME document, MÊME chemin que
        #    l'écriture précoce plus haut. `_ecrire` reconstruit la meta avec la
        #    diarisation et le profil désormais disponibles, et `write_md`
        #    écrase. La transcription était déjà sur le disque ; on ne fait ici
        #    que l'enrichir.
        report(0.76, messages.ecriture_markdown())
        content = _ecrire(complet=True)  # finalise : retire le marqueur `interrupted`
        # Les artefacts durables suivent le MÊME dossier de sortie que le .md :
        # un appelant isolé (tests) ne doit pas écrire dans les emplacements
        # globaux — c'est ce qui avait pollué l'annexe d'empreintes.
        corpus_root = target_dir if target_dir == config.OUTPUT_DIR else target_dir.parent
        if embeddings:  # empreintes vocales : fichier annexe, pas le .md
            from . import voiceprint
            voiceprint.save_embeddings(document_id, embeddings, root=corpus_root)
        # Copie audio compressée conservée à côté du corpus (re-traitement futur,
        # backfill des empreintes, lecture) — best-effort, jamais bloquant.
        media.archive_audio(src, document_id, root=corpus_root)      # écoute
        media.archive_faithful(src, document_id, root=corpus_root)   # retraitement
        media.keep_original(src, document_id, root=corpus_root)  # si KEEP_UPLOAD=1

        # 6. Compte-rendu LLM optionnel en fin de pipeline (80–98 %)
        if report_on:
            # Si le profilage a déjà tourné, le modèle local est chaud : ne pas
            # ré-annoncer. Sinon, le CR est le premier usage — on l'annonce.
            if not profile_on:
                _annoncer_modele_local(report, 0.79, llm_provider)
            report(0.80, messages.compte_rendu())
            from . import report as report_mod
            try:
                # Seam UNIQUE : route locale → CR riche, cloud → CR hérité. Même
                # contrat, même architecture de job — pas de second pipeline.
                report_mod.generate_report(
                    out_path.name,
                    provider=llm_provider,
                    mistral_api_key=mistral_api_key,
                    progress=_scaled(progress, 0.80, 0.98)
                )
            except Exception as exc:  # noqa: BLE001 — le transcript reste généré
                report(0.98, messages.compte_rendu_echoue(str(exc)))
                # Réserve DURABLE : le message de progression disparaît avec la
                # page ; le front-matter reste, et l'onglet Information l'affiche.
                # Levée par le prochain CR réussi.
                from . import library  # noqa: PLC0415
                cause = " ".join(str(exc).split())[:300] or type(exc).__name__
                library.ecrire_champ_meta(out_path, "report_error", cause)

        # Dernier usage du LLM : on lui rend sa place. Mesuré, un modèle chargé
        # occupe ~6,1 Go et retombe à ~55 Mo une fois déchargé — récupérés juste
        # avant l'indexation sémantique, qui charge son propre modèle. Symétrique
        # de `stt.unload()` plus haut : sur une RAM unifiée, chaque famille de
        # modèles doit rendre sa place. Best-effort, jamais bloquant.
        # La condition « pas en mode cloud » a disparu d'ici : le déchargement
        # est une CAPACITÉ du moteur, et c'est au registre de savoir lequel en
        # a une. Un moteur distant ne tient rien à rendre, `llm.unload` ne fait
        # donc rien — sans que ce module ait à nommer une implémentation.
        if profile_on or report_on:
            # L'import est REMONTÉ en tête de module. Ici, il faisait de `llm`
            # une variable locale pour TOUTE la fonction : la résolution du
            # modèle, 140 lignes plus haut, levait alors `UnboundLocalError`.
            # Un import paresseux au milieu d'une fonction n'est pas neutre.
            llm.unload(provider=llm_provider)

        report(1.0, messages.termine(out_path.name))
        return out_path, content

    finally:
        # Nettoyage du WAV temporaire (même en cas d'erreur)
        if wav_path is not None:
            wav_path.unlink(missing_ok=True)
