import Foundation

// Renvoi automatique d'une capture : le calendrier et les décisions, PURS (ni réseau ni horloge),
// pour que chaque cas se teste. Le compagnon applique la décision ; il n'en prend aucune.

/// Pourquoi une capture n'est pas (encore) partie.
public enum CauseAttente: Equatable, Sendable {
    /// Memoglyph a un traitement en cours (429) : la capture part seule dès qu'une place se libère.
    case occupe
    /// Memoglyph ne répond pas (arrêté, réseau coupé).
    case injoignable
    /// Memoglyph a eu un incident passager (5xx, délai dépassé).
    case incident
}

/// Ce qu'on fait d'une capture après une réponse.
public enum DecisionRenvoi: Equatable, Sendable {
    /// Le serveur l'a : l'envoi est terminé (acceptée, ou déjà reçue : jamais de seconde réunion).
    case envoyee
    /// Nouvel essai dans `apres` secondes.
    case reessayer(apres: TimeInterval, cause: CauseAttente)
    /// Plus d'essai : la capture RESTE dans la file locale, l'utilisateur la renvoie à la main.
    case abandonner(Issue)
}

public enum Renvoi {
    /// 15 s, 30 s, 1 min, 2 min, 5 min, 5 min, 5 min, 5 min : huit renvois, environ 24 minutes en tout.
    public static let delais: [TimeInterval] = [15, 30, 60, 120, 300, 300, 300, 300]

    /// Nombre de renvois automatiques après l'envoi initial.
    public static var renvoisMax: Int { delais.count }

    /// `renvoisFaits` : combien de renvois ont déjà eu lieu (0 = l'envoi initial vient d'échouer).
    ///
    /// - Arrêt immédiat sur 401 et 403 (jeton refusé) et 404 (adresse ou projet inconnu) : réessayer ne
    ///   changerait rien, et l'utilisateur doit agir.
    /// - Arrêt immédiat sur un refus définitif du serveur (400, 413, 422… : la demande elle-même est
    ///   fautive) et sur une capture illisible en local (code 0).
    /// - Nouvel essai sur 429 (pas de place), injoignable, 408 et 5xx.
    public static func decider(_ issue: Issue, renvoisFaits: Int) -> DecisionRenvoi {
        let cause: CauseAttente
        switch issue {
        case .accepte, .dejaRecue:
            return .envoyee
        case .jetonRefuse, .introuvable:
            return .abandonner(issue)
        case .injoignable:
            cause = .injoignable
        case .refuse(let code, _):
            switch code {
            case 429: cause = .occupe
            case 408, 500...599: cause = .incident
            default: return .abandonner(issue)
            }
        }
        guard renvoisFaits >= 0, renvoisFaits < delais.count else { return .abandonner(issue) }
        return .reessayer(apres: delais[renvoisFaits], cause: cause)
    }
}

/// Où en est une capture, du côté du Mac. Le menu l'écrit en phrase courte (`Texte`).
public enum EtatCapture: Equatable, Sendable {
    /// Pas de liaison : elle attend dans le menu, jamais perdue.
    case sansLiaison
    /// Envoi en cours (le fichier part).
    case envoi
    /// Pas partie : nouvel essai dans `dans` secondes (`essai` : numéro du prochain renvoi, à partir de 1).
    case attente(essai: Int, dans: TimeInterval, cause: CauseAttente)
    /// Reçue par Memoglyph, son traitement n'a pas encore rendu d'état.
    case recue
    /// En cours de traitement côté Memoglyph : l'étape vient du code de message du serveur.
    case traitement(progression: Double, code: String?, enAttente: Bool)
    /// Prête : la réunion s'ouvre depuis le menu.
    case prete(avertissements: Int)
    /// Le traitement a échoué côté Memoglyph.
    case echecTraitement(String)
    /// Plus d'essai (ou arrêt immédiat) : la raison, en une phrase ; la capture reste sur le Mac.
    case nonEnvoyee(Issue)
    /// Le suivi du traitement a été perdu (jeton refusé, adresse inconnue…) ; le traitement continue.
    case suiviInterrompu(Issue)

    /// Un état qui ne bouge plus tout seul.
    public var final: Bool {
        switch self {
        case .prete, .echecTraitement, .nonEnvoyee, .suiviInterrompu, .sansLiaison: return true
        default: return false
        }
    }
}
