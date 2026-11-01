/// Un processus qui a ouvert l'audio, tel que Core Audio le décrit.
public struct ProcessusDecrit: Equatable, Sendable {
    public let objet: UInt32          // AudioObjectID du processus
    public let pid: Int32
    public let nom: String            // nom de l'exécutable : `zoom.us`, `afplay`
    public let identifiant: String    // identifiant de paquet, vide pour un outil
    public let joue: Bool             // produit du son en ce moment
    public let prendMicro: Bool       // lit une entrée audio en ce moment (le micro)

    public init(objet: UInt32, pid: Int32, nom: String, identifiant: String, joue: Bool, prendMicro: Bool = false) {
        self.objet = objet
        self.pid = pid
        self.nom = nom
        self.identifiant = identifiant
        self.joue = joue
        self.prendMicro = prendMicro
    }

    /// Les processus visés par `cible`.
    ///
    /// Un identifiant de paquet couvre aussi ses auxiliaires (`com.microsoft.teams2`
    /// couvre `com.microsoft.teams2.helper`) : une application de visio joue souvent
    /// son son depuis un processus auxiliaire. Un nom désigne l'exécutable, à la casse
    /// près, pour les outils sans paquet comme `afplay`.
    public static func choisir(_ cible: Cible, parmi processus: [ProcessusDecrit]) -> [ProcessusDecrit] {
        switch cible {
        case .pid(let pid):
            return processus.filter { $0.pid == pid }
        case .identifiant(let texte):
            let minuscule = texte.lowercased()
            return processus.filter {
                let paquet = $0.identifiant.lowercased()
                return (!paquet.isEmpty && (paquet == minuscule || paquet.hasPrefix(minuscule + ".")))
                    || $0.nom.lowercased() == minuscule
            }
        }
    }
}
