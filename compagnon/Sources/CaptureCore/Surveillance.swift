import Foundation

// Surveillance en direct d'un enregistrement : logique pure, sans Core Audio ni fenêtre, pour
// que chaque alerte se teste à son seuil. Le bilan de fin (`Enregistreur.Bilan`) arrive trop
// tard pour sauver une réunion : un micro décroché ou saturé doit se voir pendant la prise.

/// Une alerte affichée pendant l'enregistrement.
public enum Alerte: Equatable, Sendable {
    /// Le canal micro est à zéro exact depuis `Seuils.zeroSecondes` (casque retiré, micro coupé).
    case microDecroche
    /// Le micro dépasse le seuil de saturation ; l'écriture écrête proprement (`Melange.versInt16`).
    case saturation
    /// L'enveloppe du micro suit celle du système : haut-parleurs sans casque, écho.
    case echo
    /// Le périphérique d'entrée a changé depuis le démarrage.
    case peripheriqueChange
    /// Le processus enregistré a disparu (page rechargée) : `ChoixReplay.remplacants`.
    case processusDisparu

    public var texte: String {
        switch self {
        case .microDecroche: return "Le micro ne capte plus rien : vérifiez le casque ou le micro."
        case .saturation: return "Le micro sature : baissez le volume ou éloignez-le."
        case .echo: return "Le micro reprend le son de la visio : mettez un casque."
        case .peripheriqueChange: return "Le micro a changé pendant l'enregistrement."
        case .processusDisparu: return "Le son suivi a disparu (page rechargée ?) : reprenez depuis le menu."
        }
    }
}

/// Seuils des alertes, en secondes d'audio et en amplitude (1 = pleine échelle).
public struct Seuils: Sendable {
    public var zeroSecondes: Double = 3
    public var saturation: Float = 0.99
    public var saturationRepos: Double = 5
    /// L'écho se juge sur les enveloppes (niveau efficace par tampon) d'une fenêtre glissante :
    /// le trajet haut-parleur, pièce, micro décale et déforme l'onde, pas son enveloppe.
    public var echoCorrelation: Float = 0.8
    public var echoSecondes: Double = 5
    /// Sous ce niveau efficace moyen, le système est silencieux : pas d'écho à chercher.
    public var echoNiveauSysteme: Float = 0.01

    public init() {}
}

/// Suit les tampons d'un enregistrement et rend les alertes nouvelles, une fois par épisode.
public struct Surveillant {
    public let seuils: Seuils
    private let peripheriqueInitial: String?
    private var zero: Double = 0
    private var microDecrocheSignale = false
    private var depuisSaturation: Double = .infinity
    private var enveloppe: [(micro: Float, systeme: Float, duree: Double)] = []
    private var dureeEnveloppe: Double = 0
    private var echoSignale = false
    private var peripheriqueSignale = false

    public init(seuils: Seuils = Seuils(), peripherique: String? = nil) {
        self.seuils = seuils
        self.peripheriqueInitial = peripherique
    }

    /// Un tampon de `duree` secondes : micro (nil sans micro), système, périphérique d'entrée courant.
    public mutating func observer(micro: [Float]?, systeme: [Float], duree: Double,
                                  peripherique: String? = nil) -> [Alerte] {
        var alertes: [Alerte] = []
        if let initial = peripheriqueInitial, let courant = peripherique, courant != initial, !peripheriqueSignale {
            peripheriqueSignale = true
            alertes.append(.peripheriqueChange)
        }
        guard let micro, !micro.isEmpty else { return alertes }
        let crete = Melange.crete(micro)
        if crete == 0 {
            zero += duree
            if zero >= seuils.zeroSecondes && !microDecrocheSignale {
                microDecrocheSignale = true
                alertes.append(.microDecroche)
            }
        } else {
            zero = 0
            microDecrocheSignale = false
        }
        depuisSaturation += duree
        if crete >= seuils.saturation {
            if depuisSaturation >= seuils.saturationRepos { alertes.append(.saturation) }
            depuisSaturation = 0
        }
        enveloppe.append((Surveillant.efficace(micro), Surveillant.efficace(systeme), duree))
        dureeEnveloppe += duree
        while let premier = enveloppe.first, dureeEnveloppe - premier.duree >= seuils.echoSecondes {
            dureeEnveloppe -= premier.duree
            enveloppe.removeFirst()
        }
        if dureeEnveloppe >= seuils.echoSecondes {
            let systemes = enveloppe.map(\.systeme)
            let r = Surveillant.correlation(enveloppe.map(\.micro), systemes)
            let joue = systemes.reduce(0, +) / Float(systemes.count) >= seuils.echoNiveauSysteme
            if joue && r >= seuils.echoCorrelation && !echoSignale {
                echoSignale = true
                alertes.append(.echo)
            } else if !joue || r < seuils.echoCorrelation / 2 {
                echoSignale = false   // hystérésis : pas de rafale autour du seuil
            }
        }
        return alertes
    }

    /// Niveau efficace (RMS) d'un tampon.
    public static func efficace(_ x: [Float]) -> Float {
        guard !x.isEmpty else { return 0 }
        return (x.reduce(0) { $0 + $1 * $1 } / Float(x.count)).squareRoot()
    }

    /// Corrélation de Pearson entre deux tampons, sur leur longueur commune ; 0 si l'un est plat.
    public static func correlation(_ a: [Float], _ b: [Float]) -> Float {
        let n = min(a.count, b.count)
        guard n > 1 else { return 0 }
        let ma = a[..<n].reduce(0, +) / Float(n), mb = b[..<n].reduce(0, +) / Float(n)
        var cov: Float = 0, va: Float = 0, vb: Float = 0
        for i in 0..<n {
            let da = a[i] - ma, db = b[i] - mb
            cov += da * db; va += da * da; vb += db * db
        }
        guard va > 0, vb > 0 else { return 0 }
        return cov / (va.squareRoot() * vb.squareRoot())
    }
}

/// États de la pastille flottante : elle n'existe que pendant l'enregistrement.
public enum EtatPastille: Equatable, Sendable {
    case cachee
    case enregistrement(debut: Double)

    public var visible: Bool { if case .enregistrement = self { return true } else { return false } }

    /// Temps écoulé « mm:ss » (« h:mm:ss » au-delà d'une heure) ; vide si cachée.
    public func temps(maintenant: Double) -> String {
        guard case .enregistrement(let debut) = self else { return "" }
        let s = max(0, Int(maintenant - debut))
        return s >= 3600 ? String(format: "%d:%02d:%02d", s / 3600, s / 60 % 60, s % 60)
                         : String(format: "%02d:%02d", s / 60, s % 60)
    }
}

/// Rappels montrés au démarrage depuis la barre de menus.
public enum Rappels {
    public static let casque = "Mettez un casque : sans lui, le micro reprend le son de la visio et les voix se mêlent."
    public static let participants = "Prévenez les participants que la réunion est enregistrée."
    public static let usagePrive = "Un replay enregistré reste pour votre usage privé."

    /// En Visio, on enregistre des personnes : leur consentement d'abord, puis le casque.
    /// Un replay se capte sans micro : ni écho, ni casque.
    public static func auDemarrage(visio: Bool) -> [String] {
        visio ? [participants, casque] : [usagePrive]
    }
}

/// Une ligne de colonnes de largeurs fixes ; un texte trop long est tronqué par « … ».
public enum Colonnes {
    public static func ligne(_ cellules: [String], largeurs: [Int]) -> String {
        zip(cellules, largeurs).map { cellule, largeur in
            let texte = cellule.count >= largeur ? String(cellule.prefix(max(0, largeur - 2))) + "…" : cellule
            return texte.padding(toLength: largeur, withPad: " ", startingAt: 0)
        }.joined() + (cellules.count > largeurs.count ? cellules[largeurs.count...].joined(separator: " ") : "")
    }
}

/// Niveau affiché par un vumètre, de 0 à 1, sur une échelle de `plage` dB sous la pleine échelle :
/// une voix normale (crête vers 0,1) se voit, ce qu'une échelle linéaire écraserait.
public enum Niveau {
    public static func affiche(crete: Float, plage: Float = 60) -> Double {
        guard crete > 0, crete.isFinite else { return 0 }
        return Double(max(0, min(1, (20 * log10(crete) + plage) / plage)))
    }
}

/// Nom du fichier d'une capture : mode et heure locale de début, sans caractère interdit.
public enum NomCapture {
    public static func fichier(visio: Bool, debut: Date, fuseau: TimeZone = .current) -> String {
        var calendrier = Calendar(identifier: .gregorian)
        calendrier.timeZone = fuseau
        let c = calendrier.dateComponents([.year, .month, .day, .hour, .minute, .second], from: debut)
        return String(format: "%@-%04d-%02d-%02d-%02dh%02d%02d.wav", visio ? "visio" : "replay",
                      c.year ?? 0, c.month ?? 0, c.day ?? 0, c.hour ?? 0, c.minute ?? 0, c.second ?? 0)
    }
}
