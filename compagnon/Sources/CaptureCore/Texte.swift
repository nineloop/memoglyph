import Foundation

// Les phrases du compagnon, en français et en anglais, selon la langue du système.
//
// Règles : des phrases courtes et entières ; aucun identifiant, aucun nom de moteur ni code
// d'erreur ; la progression vient des CODES de message du serveur (`message_code`), jamais de son
// texte libre (anglais, avec des noms) ; l'état de la liaison se lit par un libellé ET une forme,
// jamais par la couleur seule.

public enum Langue: String, CaseIterable, Sendable {
    case fr, en

    /// La première langue préférée du système décide : `fr`, `fr-CA`… donnent le français, tout le
    /// reste l'anglais (la langue de repli du produit).
    public static func depuis(preferees: [String]) -> Langue {
        guard let premiere = preferees.first?.lowercased() else { return .en }
        return premiere == "fr" || premiere.hasPrefix("fr-") || premiere.hasPrefix("fr_") ? .fr : .en
    }

    public static var systeme: Langue { depuis(preferees: Locale.preferredLanguages) }
}

/// Les phrases, par clé. Un test exige les mêmes clés (et les mêmes `{paramètres}`) dans les deux langues.
public enum Cle: String, CaseIterable, Sendable {
    // Pastille de la liaison : forme + libellé.
    case pastilleLiee, pastilleHorsLigne, pastilleVerification, pastilleNonRelie, pastilleRefusee
    // Destination (ligne de tête du menu).
    case destinationCeMac, destinationProjetParDefaut, projetNonClasse
    // Traitements en cours et en attente.
    case enAttenteUn, enAttentePlusieurs, enAttenteDetail, detailAutres
    // Étapes (depuis les codes de message du serveur).
    case etapePreparation, etapeTranscription, etapeLocuteurs, etapeAnalyse, etapeCompteRendu
    case etapeEcriture, etapeTraitement, etapeEnAttente
    // États d'une capture.
    case captureSansLiaison, captureEnvoi, captureRecue, captureTraitement, capturePrete
    case capturePreteAvertissements, captureEchecTraitement, captureSuiviInterrompu
    case captureAttenteOccupe, captureAttenteInjoignable, captureAttenteIncident
    case captureNonEnvoyee
    // Raisons en une phrase (jamais un code HTTP ni un message du serveur).
    case raisonJetonRefuse, raisonIntrouvable, raisonRefus, raisonInjoignable, raisonLecture
    // Confirmation à l'acceptation.
    case confirmationRangee, confirmationAttente, actionSuivre
    // Menu.
    case menuOuvrirReunion, menuRenvoyer, menuRenvoyerCaptures, menuRelier, menuChangerLiaison
    case menuOuvrirDossier, menuQuitter, menuProjetEnvoi, menuProjetParDefaut, menuOublier
    case menuDemarrerVisio, menuDemarrerReplay, menuArreter, menuAutresProcessus
    case menuRienNeJoue, menuListeImpossible, menuReprendreSur, menuEnregistrement
    // Fenêtre d'enregistrement (accessibilité).
    case panneauArreter, panneauMicro, panneauVisio
    // Alertes en direct, rappels au démarrage, menu Visio.
    case alerteMicroDecroche, alerteSaturation, alerteEcho, alertePeripheriqueChange, alerteProcessusDisparu
    case rappelCasque, rappelParticipants, rappelUsagePrive, rappelDestination
    case visioAppelEnCours, visioToutLeSon, visioAucunAppel, visioSafariWeb
    // Boîtes de dialogue.
    case dlgDemarrerTitre, dlgDemarrer, dlgAnnuler, dlgOK, dlgFinder
    case dlgMicroRefuseTitre, dlgMicroRefuse, dlgMicroAttenteTitre, dlgMicroAttente
    case dlgCaptureImpossible, dlgPlusAudioTitre, dlgPlusAudio, dlgArretErreurTitre, dlgArretErreur
    case dlgMuetTitre, dlgMuet, dlgMicroMuetTitre, dlgMicroMuet
    case dlgSuccesTitre, dlgSuiteSansLiaison, dlgSuiteEnvoi
    case dlgRelierTitre, dlgRelierTexte, dlgAdresse, dlgJeton, dlgRelier, dlgOublier
    case dlgAdresseInvalideTitre, dlgAdresseInvalide, dlgJetonInvalideTitre, dlgJetonInvalide
    case dlgLiaisonRefusee, dlgTrousseauTitre
}

public enum Texte {
    /// Remplace chaque `{nom}` par sa valeur ; une valeur absente laisse le marqueur (vu par les tests).
    public static func remplir(_ modele: String, _ valeurs: [String: String]) -> String {
        valeurs.reduce(modele) { $0.replacingOccurrences(of: "{\($1.key)}", with: $1.value) }
    }

    public static func phrase(_ cle: Cle, _ langue: Langue = .systeme, _ valeurs: [String: String] = [:]) -> String {
        remplir(table(langue)[cle.rawValue] ?? cle.rawValue, valeurs)
    }

    public static func table(_ langue: Langue) -> [String: String] { langue == .fr ? francais : anglais }

    // MARK: - Liaison

    public enum Pastille: Equatable, Sendable { case liee, horsLigne, verification, nonRelie, refusee }

    /// La forme et le libellé, ensemble : ● liée, ○ hors ligne, ◌ vérification.
    public static func pastille(_ etat: Pastille, _ langue: Langue = .systeme) -> String {
        switch etat {
        case .liee: return phrase(.pastilleLiee, langue)
        case .horsLigne: return phrase(.pastilleHorsLigne, langue)
        case .verification: return phrase(.pastilleVerification, langue)
        case .nonRelie: return phrase(.pastilleNonRelie, langue)
        case .refusee: return phrase(.pastilleRefusee, langue)
        }
    }

    /// `joignable` : nil tant que la santé n'a pas répondu. `derniereIssue` : l'issue du dernier appel avec le
    /// jeton ; un jeton refusé (révoqué, expiré) passe la liaison à « refusée » jusqu'à une nouvelle liaison,
    /// même si Memoglyph répond bien à la santé.
    public static func etatDeLaLiaison(lie: Bool, joignable: Bool?, derniereIssue: Issue? = nil) -> Pastille {
        guard lie else { return .nonRelie }
        if derniereIssue == .jetonRefuse { return .refusee }
        switch joignable {
        case true?: return .liee
        case false?: return .horsLigne
        case nil: return .verification
        }
    }

    // MARK: - Noms et typographie

    /// Le nom d'un projet tel qu'on l'AFFICHE : « Non classé » est un libellé du système (le nom stocké est
    /// une constante), traduit comme dans l'interface web ; un projet nommé par l'utilisateur n'est jamais touché.
    public static func nomDeProjet(_ nom: String, kind: String = "", _ langue: Langue = .systeme) -> String {
        kind == "inbox" || nom == "Non classé" || nom == "Unclassified" ? phrase(.projetNonClasse, langue) : nom
    }

    /// « nom : texte » en français (espace avant les deux-points), « nom: texte » en anglais.
    public static func etiquette(_ nom: String, _ texte: String, _ langue: Langue = .systeme) -> String {
        langue == .fr ? "\(nom) : \(texte)" : "\(nom): \(texte)"
    }

    // MARK: - Étapes

    /// Le groupe d'une étape, comme `core/etapes.py` : du plus précis au plus général.
    static let groupes: [(prefixe: String, cle: Cle)] = [
        ("step.report", .etapeCompteRendu),
        ("step.profiling", .etapeAnalyse),
        ("step.diarization", .etapeLocuteurs),
        ("step.voiceprints", .etapeLocuteurs),
        ("step.markdown", .etapeEcriture),
        ("step.done", .etapeEcriture),
        ("step.audio", .etapePreparation),
        ("step.model", .etapeTranscription),
        ("step.transcription", .etapeTranscription),
        ("step.upload", .etapeTranscription),
        ("step.segments", .etapeTranscription),
        ("step.languageInferred", .etapeTranscription),
    ]

    /// La phrase d'une étape. Un code inconnu (ou absent) donne « Traitement en cours… », jamais le
    /// texte libre du serveur.
    public static func etape(code: String?, enAttente: Bool = false, _ langue: Langue = .systeme) -> String {
        if enAttente { return phrase(.etapeEnAttente, langue) }
        guard let code, !code.isEmpty else { return phrase(.etapeTraitement, langue) }
        for (prefixe, cle) in groupes where code == prefixe || code.hasPrefix(prefixe + ".") {
            return phrase(cle, langue)
        }
        return phrase(.etapeTraitement, langue)
    }

    // MARK: - Attente et confirmation

    /// « en attente : 2 traitements en cours » ; aucun titre d'une autre réunion.
    public static func enAttente(actifs: Int, _ langue: Langue = .systeme) -> String {
        actifs <= 1 ? phrase(.enAttenteUn, langue) : phrase(.enAttentePlusieurs, langue, ["n": String(actifs)])
    }

    /// « Rangé dans <projet>. », puis l'attente éventuelle.
    public static func confirmation(projet: String?, autresEnCours: Int, _ langue: Langue = .systeme) -> String {
        let lieu = (projet?.isEmpty == false ? projet : nil) ?? phrase(.destinationProjetParDefaut, langue)
        let rangee = phrase(.confirmationRangee, langue, ["projet": lieu])
        return autresEnCours > 0 ? rangee + " " + phrase(.confirmationAttente, langue) : rangee
    }

    // MARK: - Raisons

    /// Une raison en une phrase pour une issue qui n'a pas abouti : aucun code HTTP, aucun jeton, aucun
    /// message libre du serveur.
    public static func raison(_ issue: Issue, _ langue: Langue = .systeme) -> String {
        switch issue {
        case .jetonRefuse: return phrase(.raisonJetonRefuse, langue)
        case .introuvable: return phrase(.raisonIntrouvable, langue)
        case .injoignable: return phrase(.raisonInjoignable, langue)
        case .refuse(let code, _): return phrase(code == 0 ? .raisonLecture : .raisonRefus, langue)
        case .accepte, .dejaRecue: return phrase(.captureRecue, langue)
        }
    }

    // MARK: - État d'une capture

    /// La ligne du menu pour une capture. `fichier` ne sert qu'à l'identifier pour l'utilisateur.
    public static func ligne(_ etat: EtatCapture, actifs: Int = 0, _ langue: Langue = .systeme) -> String {
        switch etat {
        case .sansLiaison: return phrase(.captureSansLiaison, langue)
        case .envoi: return phrase(.captureEnvoi, langue)
        case .recue: return phrase(.captureRecue, langue)
        case .attente(_, let dans, let cause):
            let delai = duree(dans, langue)
            switch cause {
            case .occupe:
                let debut = actifs > 0 ? enAttente(actifs: actifs, langue) : phrase(.etapeEnAttente, langue)
                return phrase(.captureAttenteOccupe, langue, ["attente": debut, "delai": delai])
            case .injoignable: return phrase(.captureAttenteInjoignable, langue, ["delai": delai])
            case .incident: return phrase(.captureAttenteIncident, langue, ["delai": delai])
            }
        case .traitement(let progression, let code, let enAttente):
            guard !enAttente else { return phrase(.etapeEnAttente, langue) }
            return phrase(.captureTraitement, langue, ["etape": etape(code: code, enAttente: false, langue),
                                                        "pct": String(Int((max(0, min(1, progression)) * 100).rounded()))])
        case .prete(let avertissements):
            return avertissements > 0
                ? phrase(.capturePreteAvertissements, langue, ["n": String(avertissements)])
                : phrase(.capturePrete, langue)
        case .echecTraitement: return phrase(.captureEchecTraitement, langue)
        case .nonEnvoyee(let issue): return phrase(.captureNonEnvoyee, langue, ["raison": raison(issue, langue)])
        case .suiviInterrompu(let issue): return phrase(.captureSuiviInterrompu, langue, ["raison": raison(issue, langue)])
        }
    }

    /// 15 s, 30 s, 1 min, 5 min : arrondie vers le haut à la seconde, puis à la minute.
    public static func duree(_ secondes: TimeInterval, _ langue: Langue = .systeme) -> String {
        let s = max(1, Int(secondes.rounded(.up)))
        return s < 60 ? "\(s) s" : "\((s + 59) / 60) min"
    }

    // MARK: - Dictionnaires

    static let francais: [String: String] = [
        "pastilleLiee": "● Liée", "pastilleHorsLigne": "○ Hors ligne",
        "pastilleVerification": "◌ Vérification…", "pastilleNonRelie": "○ Non reliée",
        "pastilleRefusee": "⚠︎ Liaison refusée",
        "destinationCeMac": "ce Mac", "destinationProjetParDefaut": "projet par défaut",
        "projetNonClasse": "Non classé", "menuOublier": "Oublier la liaison",
        "enAttenteUn": "en attente : 1 traitement en cours",
        "enAttentePlusieurs": "en attente : {n} traitements en cours",
        "enAttenteDetail": "Détail : {detail}", "detailAutres": "+ {n} autre(s) traitement(s) en cours",
        "etapePreparation": "Préparation de l'audio…", "etapeTranscription": "Transcription en cours…",
        "etapeLocuteurs": "Identification des voix…", "etapeAnalyse": "Analyse de la réunion…",
        "etapeCompteRendu": "Compte-rendu en cours…", "etapeEcriture": "Écriture de la réunion…",
        "etapeTraitement": "Traitement en cours…", "etapeEnAttente": "En attente d'une place…",
        "captureSansLiaison": "en attente d'une liaison", "captureEnvoi": "envoi…",
        "captureRecue": "Capture reçue", "captureTraitement": "{etape} {pct} %",
        "capturePrete": "prête", "capturePreteAvertissements": "prête ({n} avertissement(s))",
        "captureEchecTraitement": "le traitement a échoué", "captureSuiviInterrompu": "suivi interrompu : {raison}",
        "captureAttenteOccupe": "{attente} · nouvel essai dans {delai}",
        "captureAttenteInjoignable": "Memoglyph est injoignable · nouvel essai dans {delai}",
        "captureAttenteIncident": "Memoglyph a eu un incident · nouvel essai dans {delai}",
        "captureNonEnvoyee": "Capture non envoyée : {raison}",
        "raisonJetonRefuse": "le jeton est refusé, reliez à nouveau le compagnon",
        "raisonIntrouvable": "Memoglyph ne connaît pas cette adresse ou ce projet",
        "raisonRefus": "Memoglyph a refusé cette capture",
        "raisonInjoignable": "Memoglyph est injoignable",
        "raisonLecture": "le fichier de la capture est introuvable ou illisible",
        "confirmationRangee": "Rangé dans {projet}.",
        "confirmationAttente": "En attente : un traitement est déjà en cours.",
        "actionSuivre": "Suivre",
        "menuOuvrirReunion": "Ouvrir « {reunion} » dans Memoglyph", "menuRenvoyer": "Renvoyer",
        "menuRenvoyerCaptures": "Renvoyer les captures en attente ({n})",
        "menuRelier": "Relier à Memoglyph…", "menuChangerLiaison": "Changer la liaison à Memoglyph…",
        "menuOuvrirDossier": "Ouvrir le dossier des captures", "menuQuitter": "Quitter",
        "menuProjetEnvoi": "Projet d'envoi", "menuProjetParDefaut": "Projet par défaut du compte",
        "menuDemarrerVisio": "Démarrer en Visio", "menuDemarrerReplay": "Démarrer en Replay",
        "menuArreter": "Arrêter", "menuAutresProcessus": "Autres processus…",
        "menuRienNeJoue": "Rien ne joue : lancez la vidéo, puis rouvrez ce menu",
        "menuListeImpossible": "Liste impossible : {detail}",
        "menuReprendreSur": "Reprendre sur {nom} (PID {pid})", "menuEnregistrement": "Enregistrement",
        "panneauArreter": "Arrêter l'enregistrement", "panneauMicro": "Micro", "panneauVisio": "Visio",
        "alerteMicroDecroche": "Le micro ne capte plus rien : vérifiez le casque ou le micro.",
        "alerteSaturation": "Le micro sature : baissez le volume ou éloignez-le.",
        "alerteEcho": "Le micro reprend le son de la visio : mettez un casque.",
        "alertePeripheriqueChange": "Le micro a changé pendant l'enregistrement.",
        "alerteProcessusDisparu": "Le son suivi a disparu (page rechargée ?) : reprenez depuis le menu.",
        "rappelCasque": "Mettez un casque : sans lui, le micro reprend le son de la visio et les voix se mêlent.",
        "rappelParticipants": "Prévenez les participants que la réunion est enregistrée.",
        "rappelUsagePrive": "Un replay enregistré reste pour votre usage privé.",
        "rappelDestination": "Destination : {lieu}",
        "visioAppelEnCours": "Appel en cours : {nom}", "visioToutLeSon": "Tout le son du Mac",
        "visioAucunAppel": "Aucun appel détecté : lancez l'appel, puis rouvrez ce menu",
        "visioSafariWeb": "Safari (son des pages web)",
        "dlgDemarrerTitre": "Enregistrer {nom} en {mode} ?", "dlgDemarrer": "Démarrer", "dlgAnnuler": "Annuler",
        "dlgOK": "OK", "dlgFinder": "Afficher dans le Finder",
        "dlgMicroRefuseTitre": "Micro refusé par macOS",
        "dlgMicroRefuse": "Réglages Système > Confidentialité et sécurité > Microphone : autorisez « Memoglyph compagnon » (ou, lancé hors de son paquet .app, l'application qui l'a lancé, comme le Terminal), ou démarrez en Replay (sans micro).",
        "dlgMicroAttenteTitre": "Autorisation du micro en attente",
        "dlgMicroAttente": "Répondez à la demande de macOS, puis démarrez à nouveau.",
        "dlgCaptureImpossible": "Capture impossible",
        "dlgPlusAudioTitre": "{nom} n'a plus d'audio ouvert",
        "dlgPlusAudio": "Relancez le son de l'application, puis démarrez à nouveau.",
        "dlgArretErreurTitre": "Arrêt de la capture en erreur",
        "dlgArretErreur": "{detail}\n\nLe fichier écrit jusque-là se répare avec « memoglyph-capture reparer {chemin} ».",
        "dlgMuetTitre": "Aucun son reçu de {nom}",
        "dlgMuet": "Soit l'application n'a rien joué, soit macOS n'autorise pas la capture du son des applications (Réglages Système > Confidentialité et sécurité > Enregistrement de l'écran et de l'audio du système). Le fichier ({duree}) est gardé, mais il est muet.",
        "dlgMicroMuetTitre": "Le micro n'a rien envoyé",
        "dlgMicroMuet": "Vérifiez son autorisation et l'entrée par défaut. Le fichier ({duree}) n'a que le son de la visio ; il n'est pas envoyé.",
        "dlgSuccesTitre": "Enregistrement réussi ({duree})",
        "dlgSuiteSansLiaison": "Reliez le compagnon à Memoglyph pour l'envoyer ; elle attend dans le menu.",
        "dlgSuiteEnvoi": "Envoi à Memoglyph en cours : son état est dans le menu.",
        "dlgRelierTitre": "Relier à Memoglyph",
        "dlgRelierTexte": "Créez un jeton dans Memoglyph, « Appareils », puis collez-le ici. Il est rangé dans le trousseau de macOS, nulle part ailleurs.",
        "dlgAdresse": "Adresse de l'interface", "dlgJeton": "Jeton d'appareil", "dlgRelier": "Relier",
        "dlgOublier": "Oublier la liaison",
        "dlgAdresseInvalideTitre": "Adresse invalide", "dlgAdresseInvalide": "Attendu : http://127.0.0.1:8770, sans chemin.",
        "dlgJetonInvalideTitre": "Jeton invalide",
        "dlgJetonInvalide": "Un jeton d'appareil commence par mgj_ ; copiez-le en entier depuis « Appareils ».",
        "dlgLiaisonRefusee": "Liaison refusée", "dlgTrousseauTitre": "Le trousseau refuse le jeton",
    ]

    static let anglais: [String: String] = [
        "pastilleLiee": "● Linked", "pastilleHorsLigne": "○ Offline",
        "pastilleVerification": "◌ Checking…", "pastilleNonRelie": "○ Not linked",
        "pastilleRefusee": "⚠︎ Link refused",
        "destinationCeMac": "this Mac", "destinationProjetParDefaut": "default project",
        "projetNonClasse": "Unclassified", "menuOublier": "Forget the link",
        "enAttenteUn": "waiting: 1 job running",
        "enAttentePlusieurs": "waiting: {n} jobs running",
        "enAttenteDetail": "Detail: {detail}", "detailAutres": "+ {n} other job(s) running",
        "etapePreparation": "Preparing the audio…", "etapeTranscription": "Transcribing…",
        "etapeLocuteurs": "Identifying voices…", "etapeAnalyse": "Analysing the meeting…",
        "etapeCompteRendu": "Writing the report…", "etapeEcriture": "Saving the meeting…",
        "etapeTraitement": "Processing…", "etapeEnAttente": "Waiting for a free slot…",
        "captureSansLiaison": "waiting for a link", "captureEnvoi": "sending…",
        "captureRecue": "Capture received", "captureTraitement": "{etape} {pct}%",
        "capturePrete": "ready", "capturePreteAvertissements": "ready ({n} warning(s))",
        "captureEchecTraitement": "processing failed", "captureSuiviInterrompu": "tracking stopped: {raison}",
        "captureAttenteOccupe": "{attente} · trying again in {delai}",
        "captureAttenteInjoignable": "Memoglyph is unreachable · trying again in {delai}",
        "captureAttenteIncident": "Memoglyph had a problem · trying again in {delai}",
        "captureNonEnvoyee": "Capture not sent: {raison}",
        "raisonJetonRefuse": "the token is refused, link the companion again",
        "raisonIntrouvable": "Memoglyph does not know this address or project",
        "raisonRefus": "Memoglyph refused this capture",
        "raisonInjoignable": "Memoglyph is unreachable",
        "raisonLecture": "the capture file is missing or unreadable",
        "confirmationRangee": "Saved to {projet}.",
        "confirmationAttente": "Waiting: another job is already running.",
        "actionSuivre": "Follow",
        "menuOuvrirReunion": "Open “{reunion}” in Memoglyph", "menuRenvoyer": "Send again",
        "menuRenvoyerCaptures": "Send waiting captures again ({n})",
        "menuRelier": "Link to Memoglyph…", "menuChangerLiaison": "Change the link to Memoglyph…",
        "menuOuvrirDossier": "Open the captures folder", "menuQuitter": "Quit",
        "menuProjetEnvoi": "Upload project", "menuProjetParDefaut": "Account default project",
        "menuDemarrerVisio": "Start in Visio mode", "menuDemarrerReplay": "Start in Replay mode",
        "menuArreter": "Stop", "menuAutresProcessus": "Other processes…",
        "menuRienNeJoue": "Nothing is playing: start the video, then reopen this menu",
        "menuListeImpossible": "Cannot list: {detail}",
        "menuReprendreSur": "Resume on {nom} (PID {pid})", "menuEnregistrement": "Recording",
        "panneauArreter": "Stop recording", "panneauMicro": "Mic", "panneauVisio": "Call",
        "alerteMicroDecroche": "The microphone picks up nothing: check the headset or the microphone.",
        "alerteSaturation": "The microphone is clipping: lower the volume or move it away.",
        "alerteEcho": "The microphone is picking up the call audio: wear a headset.",
        "alertePeripheriqueChange": "The microphone changed during recording.",
        "alerteProcessusDisparu": "The tracked audio is gone (page reloaded?): resume from the menu.",
        "rappelCasque": "Wear a headset: without one, the microphone picks up the call audio and voices overlap.",
        "rappelParticipants": "Tell the participants that the meeting is being recorded.",
        "rappelUsagePrive": "A recorded replay stays for your private use.",
        "rappelDestination": "Destination: {lieu}",
        "visioAppelEnCours": "Call in progress: {nom}", "visioToutLeSon": "All Mac audio",
        "visioAucunAppel": "No call detected: start the call, then reopen this menu",
        "visioSafariWeb": "Safari (web page audio)",
        "dlgDemarrerTitre": "Record {nom} in {mode} mode?", "dlgDemarrer": "Start", "dlgAnnuler": "Cancel",
        "dlgOK": "OK", "dlgFinder": "Show in Finder",
        "dlgMicroRefuseTitre": "Microphone refused by macOS",
        "dlgMicroRefuse": "System Settings > Privacy & Security > Microphone: allow “Memoglyph compagnon” (or, if run outside its .app bundle, the application that launched it, such as Terminal), or start in Replay mode (no microphone).",
        "dlgMicroAttenteTitre": "Waiting for the microphone permission",
        "dlgMicroAttente": "Answer the macOS prompt, then start again.",
        "dlgCaptureImpossible": "Cannot capture",
        "dlgPlusAudioTitre": "{nom} has no audio open any more",
        "dlgPlusAudio": "Restart the application's audio, then start again.",
        "dlgArretErreurTitre": "Error while stopping the capture",
        "dlgArretErreur": "{detail}\n\nThe file written so far can be repaired with “memoglyph-capture reparer {chemin}”.",
        "dlgMuetTitre": "No audio received from {nom}",
        "dlgMuet": "Either the application played nothing, or macOS does not allow capturing application audio (System Settings > Privacy & Security > Screen & System Audio Recording). The file ({duree}) is kept, but it is silent.",
        "dlgMicroMuetTitre": "The microphone sent nothing",
        "dlgMicroMuet": "Check its permission and the default input. The file ({duree}) only has the call audio; it is not sent.",
        "dlgSuccesTitre": "Recording saved ({duree})",
        "dlgSuiteSansLiaison": "Link the companion to Memoglyph to send it; it waits in the menu.",
        "dlgSuiteEnvoi": "Sending to Memoglyph: its status is in the menu.",
        "dlgRelierTitre": "Link to Memoglyph",
        "dlgRelierTexte": "Create a token under “Devices” in Memoglyph, then paste it here. It is stored in the macOS keychain, nowhere else.",
        "dlgAdresse": "Interface address", "dlgJeton": "Device token", "dlgRelier": "Link",
        "dlgOublier": "Forget the link",
        "dlgAdresseInvalideTitre": "Invalid address", "dlgAdresseInvalide": "Expected: http://127.0.0.1:8770, without a path.",
        "dlgJetonInvalideTitre": "Invalid token",
        "dlgJetonInvalide": "A device token starts with mgj_; copy it in full from “Devices”.",
        "dlgLiaisonRefusee": "Link refused", "dlgTrousseauTitre": "The keychain refuses the token",
    ]
}

// MARK: - Alertes, rappels et menu Visio dans la langue de l'utilisateur
//
// Les propriétés et fonctions d'origine (`Alerte.texte`, `Rappels.auDemarrage`, `LigneVisio.libelle`)
// restent en français : leurs tests et la ligne de commande `memoglyph-capture` ne changent pas. Le
// compagnon, lui, passe par ces variantes.

public extension Alerte {
    func texte(_ langue: Langue) -> String {
        switch self {
        case .microDecroche: return Texte.phrase(.alerteMicroDecroche, langue)
        case .saturation: return Texte.phrase(.alerteSaturation, langue)
        case .echo: return Texte.phrase(.alerteEcho, langue)
        case .peripheriqueChange: return Texte.phrase(.alertePeripheriqueChange, langue)
        case .processusDisparu: return Texte.phrase(.alerteProcessusDisparu, langue)
        }
    }
}

public extension Rappels {
    static func auDemarrage(visio: Bool, _ langue: Langue) -> [String] {
        visio ? [Texte.phrase(.rappelParticipants, langue), Texte.phrase(.rappelCasque, langue)]
              : [Texte.phrase(.rappelUsagePrive, langue)]
    }
}

public extension LigneVisio {
    func libelle(_ langue: Langue) -> String {
        let affiche = ChoixVisio.nom(self, langue)
        return enAppel ? Texte.phrase(.visioAppelEnCours, langue, ["nom": affiche]) : (joue ? "● " : "") + affiche
    }
}

public extension ChoixVisio {
    static func toutLeSon(_ langue: Langue) -> String { Texte.phrase(.visioToutLeSon, langue) }
    static func aucunAppel(_ langue: Langue) -> String { Texte.phrase(.visioAucunAppel, langue) }
    /// Le nom d'affichage d'une ligne du menu Visio : celui qu'a calculé `menu`, sauf pour Safari (web).
    static func nom(_ ligne: LigneVisio, _ langue: Langue) -> String {
        ligne.nom == nomWebkit ? Texte.phrase(.visioSafariWeb, langue) : ligne.nom
    }
}
