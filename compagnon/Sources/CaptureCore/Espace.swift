import Foundation

// L'espace de travail du compagnon : le nom de service du trousseau et ce qui en dépend.
//
// Par défaut, rien ne change : service `org.memoglyph.compagnon`, dossier `Memoglyph/Captures`,
// réglage `projetEnvoi`. Pour un ESSAI contre une instance de démonstration, `MEMOGLYPH_COMPAGNON_SERVICE`
// donne un autre nom de service : le trousseau, le dossier des captures (donc la file de renvoi qui y vit)
// et le réglage du projet en dépendent TOUS, pour que l'essai ne touche ni au jeton, ni aux captures en
// attente, ni au projet choisi du compagnon habituel. Pure logique : ce module ne lit pas le trousseau.

public enum Espace {
    public static let variable = "MEMOGLYPH_COMPAGNON_SERVICE"
    public static let serviceParDefaut = "org.memoglyph.compagnon"

    /// Le nom de service demandé par l'environnement s'il est valide, sinon le défaut (jamais d'erreur :
    /// un nom incorrect ne doit pas faire écrire dans une entrée inattendue).
    public static func service(environnement: [String: String]) -> String {
        guard let brut = environnement[variable]?.trimmingCharacters(in: .whitespacesAndNewlines),
              !brut.isEmpty, brut.count <= 100,
              brut.unicodeScalars.allSatisfy({ $0.isASCII && (CharacterSet.alphanumerics.contains($0) || ".-_".unicodeScalars.contains($0)) })
        else { return serviceParDefaut }
        return brut
    }

    public static func estParDefaut(_ service: String) -> Bool { service == serviceParDefaut }

    /// Ce qui s'ajoute aux noms propres au compagnon pour un service autre que le défaut ; vide sinon.
    public static func suffixe(service: String) -> String {
        guard !estParDefaut(service) else { return "" }
        let net = String(service.map { $0.isLetter || $0.isNumber ? $0 : "-" })
        return "-" + net
    }

    /// `Documents/Memoglyph/Captures` par défaut ; `Captures-<suffixe>` pour un essai.
    public static func nomDuDossier(service: String) -> String { "Captures" + suffixe(service: service) }

    /// La clé du réglage du projet d'envoi.
    public static func cleDuProjet(service: String) -> String { "projetEnvoi" + suffixe(service: service) }
}
