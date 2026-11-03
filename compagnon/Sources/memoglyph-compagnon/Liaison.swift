import CaptureCore
import Foundation
import Security

/// Le jeton d'appareil et l'adresse de Memoglyph, dans le trousseau macOS seulement : un
/// élément « mot de passe générique » dont le compte est l'adresse et le secret le jeton.
enum Trousseau {
    /// `org.memoglyph.compagnon` par défaut ; `MEMOGLYPH_COMPAGNON_SERVICE` le remplace pour un essai
    /// sur une instance de démonstration (voir `Espace`). Le nom seul change : aucune entrée n'est lue
    /// ni écrite ici avant que le compagnon ne soit lancé par son utilisateur.
    static let service = Espace.service(environnement: ProcessInfo.processInfo.environment)

    static func lire() -> (adresse: URL, jeton: String)? {
        let requete: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecReturnAttributes as String: true,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var resultat: CFTypeRef?
        guard SecItemCopyMatching(requete as CFDictionary, &resultat) == errSecSuccess,
              let element = resultat as? [String: Any],
              let compte = element[kSecAttrAccount as String] as? String,
              let adresse = ClientMemoglyph.adresse(compte),
              let donnees = element[kSecValueData as String] as? Data,
              let jeton = String(data: donnees, encoding: .utf8) else { return nil }
        return (adresse, jeton)
    }

    /// Remplace la liaison précédente. Rend le statut du trousseau, jamais le jeton.
    static func ecrire(adresse: URL, jeton: String) -> OSStatus {
        oublier()
        let element: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: adresse.absoluteString,
            kSecAttrLabel as String: "Memoglyph compagnon",
            kSecValueData as String: Data(jeton.utf8),
        ]
        return SecItemAdd(element as CFDictionary, nil)
    }

    @discardableResult
    static func oublier() -> OSStatus {
        SecItemDelete([kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service] as CFDictionary)
    }

    static func message(_ statut: OSStatus) -> String {
        (SecCopyErrorMessageString(statut, nil) as String?) ?? "erreur \(statut)"
    }
}

/// Les appels réseau, sur `CaptureCore.ClientMemoglyph`. Chaque réponse revient sur la file
/// principale ; aucun message ne porte le jeton (`ClientMemoglyph.masquer`).
enum Liaison {
    private static let session: URLSession = {
        let c = URLSessionConfiguration.ephemeral   // ni cookie ni cache sur disque
        c.httpShouldSetCookies = false
        return URLSession(configuration: c)
    }()

    private static func appeler(_ requete: URLRequest, fichier: URL? = nil,
                                fin: @escaping @Sendable (Int?, Data?, String?) -> Void) {
        let rappel: @Sendable (Data?, URLResponse?, Error?) -> Void = { corps, reponse, erreur in
            let statut = (reponse as? HTTPURLResponse)?.statusCode
            DispatchQueue.main.async { fin(statut, corps, erreur?.localizedDescription) }
        }
        if let fichier {
            session.uploadTask(with: requete, fromFile: fichier, completionHandler: rappel).resume()
        } else {
            session.dataTask(with: requete, completionHandler: rappel).resume()
        }
    }

    static func sante(_ adresse: URL, fin: @escaping @Sendable (Bool) -> Void) {
        appeler(ClientMemoglyph.requete(.sante, adresse: adresse, jeton: nil)) { statut, _, _ in
            fin(statut.map { (200..<300).contains($0) } ?? false)
        }
    }

    static func projets(_ adresse: URL, jeton: String, fin: @escaping @Sendable (Result<[Projet], IssueErreur>) -> Void) {
        appeler(ClientMemoglyph.requete(.projets, adresse: adresse, jeton: jeton)) { statut, corps, erreur in
            fin(ClientMemoglyph.lireProjets(statut: statut, corps: corps, erreur: erreur, jeton: jeton))
        }
    }

    static func comptes(_ adresse: URL, jeton: String, fin: @escaping @Sendable (Result<Comptes, IssueErreur>) -> Void) {
        appeler(ClientMemoglyph.requete(.traitements, adresse: adresse, jeton: jeton)) { statut, corps, erreur in
            fin(ClientMemoglyph.lireComptes(statut: statut, corps: corps, erreur: erreur, jeton: jeton))
        }
    }

    static func travail(_ id: String, adresse: URL, jeton: String, fin: @escaping @Sendable (EtatTravail) -> Void) {
        appeler(ClientMemoglyph.requete(.travail(id), adresse: adresse, jeton: jeton)) { statut, corps, erreur in
            fin(ClientMemoglyph.lireTravail(statut: statut, corps: corps, erreur: erreur, jeton: jeton))
        }
    }

    /// Envoie `capture` depuis `dossier`. Le corps multipart est écrit à côté, dans un fichier
    /// temporaire lu en flux par `uploadTask` : une capture d'une heure pèse près de 700 Mo.
    static func envoyer(_ capture: Capture, dossier: URL, projet: String?, adresse: URL, jeton: String,
                        fin: @escaping @Sendable (Issue) -> Void) {
        let source = dossier.appendingPathComponent(capture.fichier)
        let corps = FileManager.default.temporaryDirectory
            .appendingPathComponent("memoglyph-envoi-\(UUID().uuidString).multipart")
        let frontiere = "memoglyph-\(UUID().uuidString)"
        DispatchQueue.global(qos: .utility).async {
            do {
                try ecrireCorps(capture, source: source, vers: corps, projet: projet, frontiere: frontiere)
            } catch {
                DispatchQueue.main.async { fin(.refuse(code: 0, raison: "lecture de la capture : \(error.localizedDescription)")) }
                return
            }
            var requete = ClientMemoglyph.requete(.envoi, adresse: adresse, jeton: jeton)
            requete.setValue("multipart/form-data; boundary=\(frontiere)", forHTTPHeaderField: "Content-Type")
            appeler(requete, fichier: corps) { statut, reponse, erreur in
                try? FileManager.default.removeItem(at: corps)
                fin(ClientMemoglyph.lireEnvoi(statut: statut, corps: reponse, erreur: erreur, jeton: jeton))
            }
        }
    }

    private static func ecrireCorps(_ capture: Capture, source: URL, vers corps: URL, projet: String?,
                                    frontiere: String) throws {
        let (entete, pied) = ClientMemoglyph.multipart(capture, projet: projet, frontiere: frontiere)
        FileManager.default.createFile(atPath: corps.path, contents: nil, attributes: [.posixPermissions: 0o600])
        let sortie = try FileHandle(forWritingTo: corps)
        defer { try? sortie.close() }
        let entree = try FileHandle(forReadingFrom: source)
        defer { try? entree.close() }
        try sortie.write(contentsOf: entete)
        while let bloc = try entree.read(upToCount: 1 << 20), !bloc.isEmpty { try sortie.write(contentsOf: bloc) }
        try sortie.write(contentsOf: pied)
    }
}
