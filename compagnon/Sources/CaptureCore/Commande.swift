/// Ce que l'on vise : un PID, ou un identifiant de paquet (`us.zoom.xos`) ou un nom
/// de processus (`afplay`).
public enum Cible: Equatable {
    case pid(Int32)
    case identifiant(String)
}

public enum Commande: Equatable {
    case aide
    case applications(tous: Bool)
    case enregistrer(cible: Cible, micro: Bool, sortie: String, duree: Double?)
    case reparer(String)
}

public struct ErreurArguments: Error, Equatable, CustomStringConvertible {
    public let description: String
    public init(_ description: String) { self.description = description }
}

/// Codes de sortie : un échec n'est jamais présenté comme un enregistrement réussi.
public enum CodeSortie: Int32 {
    case reussi = 0
    case usage = 1
    case cibleIntrouvable = 2
    case microRefuse = 3
    case systemeMuet = 4
    case microMuet = 5
    case erreurAudio = 6
}

public enum Arguments {
    public static let usage = """
    Usage :
      memoglyph-capture applications [--tous]
          Liste les processus qui jouent du son (PID, nom, identifiant de paquet).
          --tous : aussi ceux qui ont utilisé l'audio sans en jouer en ce moment.
      memoglyph-capture enregistrer --app <identifiant, nom ou PID> --sortie <fichier.wav>
                                    [--sans-micro] [--duree <secondes>]
          Enregistre le son de l'application (canal droit) et le micro (canal gauche),
          en WAV 16 bits écrit par tranches. Ctrl-C arrête proprement.
          --sans-micro : replay ou webinar, le canal gauche reste silencieux.
      memoglyph-capture reparer <fichier.wav>
          Recale l'en-tête d'un enregistrement interrompu sur ce qui est sur le disque.
    """

    public static func analyser(_ arguments: [String]) throws -> Commande {
        guard let sousCommande = arguments.first else { return .aide }
        var reste = Array(arguments.dropFirst())
        switch sousCommande {
        case "aide", "--aide", "-h", "--help":
            return .aide
        case "applications":
            let tous = extraireDrapeau("--tous", &reste)
            try refuserReste(reste)
            return .applications(tous: tous)
        case "reparer":
            guard reste.count == 1, let fichier = reste.first, !fichier.hasPrefix("-") else {
                throw ErreurArguments("reparer attend un seul fichier")
            }
            return .reparer(fichier)
        case "enregistrer":
            let sansMicro = extraireDrapeau("--sans-micro", &reste)
            guard let app = try extraireValeur("--app", &reste) else {
                throw ErreurArguments("--app est obligatoire (identifiant, nom ou PID)")
            }
            guard let sortie = try extraireValeur("--sortie", &reste) else {
                throw ErreurArguments("--sortie est obligatoire")
            }
            var duree: Double?
            if let texte = try extraireValeur("--duree", &reste) {
                guard let valeur = Double(texte), valeur > 0, valeur.isFinite else {
                    throw ErreurArguments("--duree attend un nombre de secondes positif, pas « \(texte) »")
                }
                duree = valeur
            }
            try refuserReste(reste)
            return .enregistrer(cible: cible(app), micro: !sansMicro, sortie: sortie, duree: duree)
        default:
            throw ErreurArguments("Commande inconnue : « \(sousCommande) »")
        }
    }

    static func cible(_ texte: String) -> Cible {
        if let pid = Int32(texte), pid > 0 { return .pid(pid) }
        return .identifiant(texte)
    }

    private static func extraireDrapeau(_ nom: String, _ reste: inout [String]) -> Bool {
        guard let i = reste.firstIndex(of: nom) else { return false }
        reste.remove(at: i)
        return true
    }

    private static func extraireValeur(_ nom: String, _ reste: inout [String]) throws -> String? {
        guard let i = reste.firstIndex(of: nom) else { return nil }
        guard i + 1 < reste.count, !reste[i + 1].hasPrefix("--") else {
            throw ErreurArguments("\(nom) attend une valeur")
        }
        let valeur = reste[i + 1]
        reste.removeSubrange(i...(i + 1))
        guard !reste.contains(nom) else { throw ErreurArguments("\(nom) donné deux fois") }
        return valeur
    }

    private static func refuserReste(_ reste: [String]) throws {
        if let inconnu = reste.first { throw ErreurArguments("Argument inattendu : « \(inconnu) »") }
    }
}
