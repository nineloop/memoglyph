/// Une ligne du menu « Démarrer en Visio ».
public struct LigneVisio: Equatable, Sendable {
    public let nom: String
    /// Ce que reçoit `ProcessusDecrit.choisir(.identifiant(_:))` : l'identifiant du parent,
    /// qui couvre ses auxiliaires, ou le nom d'un exécutable sans paquet.
    public let cible: String
    /// Un processus du groupe produit du son en ce moment.
    public let joue: Bool
    /// Le groupe joue ET prend le micro.
    public let enAppel: Bool

    public init(nom: String, cible: String, joue: Bool, enAppel: Bool) {
        self.nom = nom
        self.cible = cible
        self.joue = joue
        self.enAppel = enAppel
    }

    public var libelle: String {
        enAppel ? "Appel en cours : \(nom)" : (joue ? "● " : "") + nom
    }
}

/// Le menu calculé : les appels en tête, puis les applications ouvertes (celles qui jouent
/// d'abord), puis les processus sans application, pour « Autres processus… ».
public struct MenuVisio: Equatable, Sendable {
    public let appels: [LigneVisio]
    public let applications: [LigneVisio]
    public let autres: [LigneVisio]
}

public enum ChoixVisio {
    /// Le processus GPU de WebKit : le son des pages de Safari en sort, et celui de toute
    /// autre application qui affiche du web avec WebKit.
    public static let webkit = "com.apple.WebKit.GPU"
    public static let safari = "com.apple.Safari"
    public static let nomWebkit = "Safari (son des pages web)"
    public static let compagnon = "org.memoglyph.compagnon"
    public static let toutLeSon = "Tout le son du Mac"
    public static let aucunAppel = "Aucun appel détecté : lancez l'appel, puis rouvrez ce menu"

    /// `applications` : les applications ouvertes (identifiant, nom), celles que NSWorkspace
    /// rend avec `activationPolicy == .regular`. Chaque processus va à l'application dont
    /// l'identifiant est le sien ou son préfixe suivi d'un point (le plus long l'emporte) ;
    /// les autres vont dans `autres`. Une application est en appel si son groupe, auxiliaires
    /// compris, joue et prend le micro, éventuellement par deux auxiliaires différents : un
    /// navigateur qui joue une vidéo sans micro n'est pas en appel.
    public static func menu(_ processus: [ProcessusDecrit], applications: [(String, String)],
                            soi: String = compagnon) -> MenuVisio {
        var vues = Set<String>()
        let ouvertes = applications.filter { !$0.0.isEmpty && vues.insert($0.0.lowercased()).inserted }
        var groupes: [String: [ProcessusDecrit]] = [:]
        var orphelins: [ProcessusDecrit] = []
        for p in processus where p.identifiant.lowercased() != soi.lowercased() {
            if let parent = parent(de: p, parmi: ouvertes) {
                groupes[parent, default: []].append(p)
            } else {
                orphelins.append(p)
            }
        }

        var lignes: [LigneVisio] = []
        for (identifiant, nom) in ouvertes {
            var groupe = groupes[identifiant.lowercased()] ?? []
            var cible = identifiant
            var libelle = nom
            // Safari ne joue pas le son de ses pages : il sort de WebKit, sans lien de préfixe.
            if identifiant.lowercased() == safari.lowercased() {
                let web = orphelins.filter { $0.identifiant.lowercased() == webkit.lowercased() }
                if !web.isEmpty {
                    groupe += web
                    orphelins.removeAll { $0.identifiant.lowercased() == webkit.lowercased() }
                    cible = webkit
                    libelle = nomWebkit
                }
            }
            guard !groupe.isEmpty else { continue }
            let joue = groupe.contains(where: \.joue)
            lignes.append(LigneVisio(nom: libelle, cible: cible, joue: joue,
                                     enAppel: joue && groupe.contains(where: \.prendMicro)))
        }

        var autres: [LigneVisio] = []
        var cibles: [String: Int] = [:]
        for p in orphelins {
            let cible = p.identifiant.isEmpty ? p.nom : p.identifiant
            if let rang = cibles[cible.lowercased()] {
                let ligne = autres[rang]
                autres[rang] = LigneVisio(nom: ligne.nom, cible: ligne.cible, joue: ligne.joue || p.joue, enAppel: false)
                continue
            }
            cibles[cible.lowercased()] = autres.count
            autres.append(LigneVisio(nom: p.nom, cible: cible, joue: p.joue, enAppel: false))
        }

        return MenuVisio(appels: lignes.filter(\.enAppel).sorted(by: ordre),
                         applications: lignes.filter { !$0.enAppel }.sorted(by: ordre),
                         autres: autres.sorted(by: ordre))
    }

    /// Ce que le tap de tout le son du Mac exclut : le compagnon lui-même (ses sons
    /// d'interface), par son PID ou par son identifiant.
    public static func exclusDuTout(_ processus: [ProcessusDecrit], pid: Int32,
                                    soi: String = compagnon) -> [ProcessusDecrit] {
        processus.filter { $0.pid == pid || $0.identifiant.lowercased() == soi.lowercased() }
    }

    /// L'identifiant (en minuscules) de l'application ouverte dont `p` relève.
    static func parent(de p: ProcessusDecrit, parmi ouvertes: [(String, String)]) -> String? {
        let paquet = p.identifiant.lowercased()
        guard !paquet.isEmpty else { return nil }
        return ouvertes.map { $0.0.lowercased() }
            .filter { paquet == $0 || paquet.hasPrefix($0 + ".") }
            .max { $0.count < $1.count }
    }

    /// Ce qui joue d'abord, puis par nom.
    static func ordre(_ a: LigneVisio, _ b: LigneVisio) -> Bool {
        (a.joue ? 0 : 1, a.nom.lowercased()) < (b.joue ? 0 : 1, b.nom.lowercased())
    }
}
