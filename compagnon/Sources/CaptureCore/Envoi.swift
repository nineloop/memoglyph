import Foundation

// Client de Memoglyph pour le compagnon : requêtes et lecture des réponses, sans réseau, pour
// que chaque cas se teste. Le jeton d'appareil n'ouvre que les routes du compagnon ;
// il ne voyage que dans l'en-tête Authorization et ne figure dans aucun message.

/// Les seules routes que le compagnon appelle : celles du jeton d'appareil, plus la santé.
/// Il ne crée jamais de projet.
public enum Route: Equatable, Sendable {
    case sante
    case projets
    case envoi
    case travail(String)

    public var methode: String { self == .envoi ? "POST" : "GET" }

    public var chemin: String {
        switch self {
        case .sante: return "/api/health"
        case .projets: return "/api/projects"
        case .envoi: return "/api/transcribe"
        case .travail(let id): return "/api/jobs/\(id.addingPercentEncoding(withAllowedCharacters: .alphanumerics) ?? "")"
        }
    }
}

/// Un projet du compte, tel que `GET /api/projects` le décrit.
public struct Projet: Equatable, Sendable {
    public let id: String
    public let nom: String
    public init(id: String, nom: String) { self.id = id; self.nom = nom }
}

/// Ce qu'une capture emporte vers Memoglyph.
public struct Capture: Equatable, Codable, Sendable {
    public let fichier: String        // nom du fichier dans le dossier des captures
    public let visio: Bool
    public let application: String    // provenance : `capture_application`
    public init(fichier: String, visio: Bool, application: String) {
        self.fichier = fichier; self.visio = visio; self.application = application
    }
}

/// Issue d'un appel, sans jamais le jeton dans le texte.
public enum Issue: Equatable, Sendable {
    case accepte(travail: String)
    case jetonRefuse
    case introuvable
    case refuse(code: Int, raison: String)
    case injoignable(String)

    public var texte: String {
        switch self {
        case .accepte: return "Capture reçue par Memoglyph."
        case .jetonRefuse: return "Jeton refusé : créez-en un dans « Appareils », puis reliez à nouveau le compagnon."
        case .introuvable: return "Memoglyph ne connaît pas cette adresse ou ce projet."
        case .refuse(let code, let raison): return "Memoglyph a refusé l'envoi (\(code)) : \(raison)"
        case .injoignable(let raison): return "Memoglyph est injoignable : \(raison)"
        }
    }
}

/// État d'un travail suivi par `GET /api/jobs/{job_id}`.
public enum EtatTravail: Equatable, Sendable {
    case enCours(progression: Double, message: String)
    /// `meetingId` et `projet` : l'identité de la réunion, pour l'ouvrir (`lien`).
    case fini(reunion: String, avertissements: [String], meetingId: String? = nil, projet: String? = nil)
    case echec(String)
    case perdu(Issue)
}

public enum ClientMemoglyph {
    public static let prefixeJeton = "mgj_"

    /// Un jeton d'appareil a la forme `mgj_` suivie de caractères URL sûrs.
    public static func jetonValide(_ jeton: String) -> Bool {
        let corps = jeton.dropFirst(prefixeJeton.count)
        return jeton.hasPrefix(prefixeJeton) && corps.count >= 16
            && corps.allSatisfy { $0.isASCII && ($0.isLetter || $0.isNumber || $0 == "-" || $0 == "_") }
    }

    /// Une adresse http(s) avec un hôte, sans chemin : celle de l'interface, qui relaie /api.
    public static func adresse(_ texte: String) -> URL? {
        let net = texte.trimmingCharacters(in: .whitespaces).trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        guard let url = URL(string: net), let schema = url.scheme?.lowercased(),
              ["http", "https"].contains(schema), url.host != nil, url.path.isEmpty, url.query == nil else { return nil }
        return url
    }

    public static func requete(_ route: Route, adresse: URL, jeton: String?) -> URLRequest {
        var r = URLRequest(url: adresse.appendingPathComponent(String(route.chemin.dropFirst())))
        r.httpMethod = route.methode
        r.timeoutInterval = route == .envoi ? 600 : 10
        if let jeton, route != .sante { r.setValue("Bearer \(jeton)", forHTTPHeaderField: "Authorization") }
        r.setValue("application/json", forHTTPHeaderField: "Accept")
        return r
    }

    /// Le corps multipart d'un envoi, en trois morceaux : l'en-tête (champs puis début du
    /// fichier), le fichier lui-même, lu par l'appelant, et le pied.
    public static func multipart(_ capture: Capture, projet: String?, frontiere: String) -> (entete: Data, pied: Data) {
        var champs: [(String, String)] = [
            ("capture_mode", capture.visio ? "visio" : "replay"),
            ("capture_application", String(capture.application.prefix(80))),
        ]
        if let projet, !projet.isEmpty { champs.append(("destination_project_id", projet)) }
        var entete = ""
        for (nom, valeur) in champs {
            entete += "--\(frontiere)\r\nContent-Disposition: form-data; name=\"\(nom)\"\r\n\r\n\(valeur)\r\n"
        }
        let fichier = capture.fichier.replacingOccurrences(of: "\"", with: "_")
        entete += "--\(frontiere)\r\nContent-Disposition: form-data; name=\"file\"; filename=\"\(fichier)\"\r\n"
            + "Content-Type: audio/wav\r\n\r\n"
        return (Data(entete.utf8), Data("\r\n--\(frontiere)--\r\n".utf8))
    }

    /// Réponse de `POST /api/transcribe`. `statut` nil : pas de réponse (`erreur` dit pourquoi).
    public static func lireEnvoi(statut: Int?, corps: Data?, erreur: String?, jeton: String?) -> Issue {
        if let issue = commune(statut: statut, corps: corps, erreur: erreur, jeton: jeton) { return issue }
        guard let id = objet(corps)?["job_id"] as? String, !id.isEmpty else {
            return .refuse(code: statut ?? 0, raison: "réponse sans travail")
        }
        return .accepte(travail: id)
    }

    /// Réponse de `GET /api/projects` : les projets qui ont une identité (les dossiers à migrer n'en ont pas).
    public static func lireProjets(statut: Int?, corps: Data?, erreur: String?, jeton: String?) -> Result<[Projet], IssueErreur> {
        if let issue = commune(statut: statut, corps: corps, erreur: erreur, jeton: jeton) { return .failure(IssueErreur(issue)) }
        let liste = (try? JSONSerialization.jsonObject(with: corps ?? Data())) as? [[String: Any]] ?? []
        return .success(liste.compactMap { p in
            guard let id = p["id"] as? String, !id.isEmpty else { return nil }
            return Projet(id: id, nom: (p["project"] as? String) ?? (p["name"] as? String) ?? id)
        })
    }

    /// Réponse de `GET /api/jobs/{job_id}`.
    public static func lireTravail(statut: Int?, corps: Data?, erreur: String?, jeton: String?) -> EtatTravail {
        if let issue = commune(statut: statut, corps: corps, erreur: erreur, jeton: jeton) { return .perdu(issue) }
        let o = objet(corps) ?? [:]
        switch o["status"] as? String {
        case "done":
            let resultat = o["result"] as? [String: Any] ?? [:]
            return .fini(reunion: resultat["name"] as? String ?? "",
                         avertissements: (resultat["warnings"] as? [String] ?? []).map { masquer($0, jeton: jeton) },
                         meetingId: (resultat["meeting_id"] as? String).flatMap { $0.isEmpty ? nil : $0 },
                         projet: (resultat["project_id"] as? String).flatMap { $0.isEmpty ? nil : $0 })
        case "error":
            return .echec(masquer(o["error"] as? String ?? o["message"] as? String ?? "erreur inconnue", jeton: jeton))
        default:
            return .enCours(progression: (o["progress"] as? NSNumber)?.doubleValue ?? 0,
                            message: masquer(o["message"] as? String ?? "", jeton: jeton))
        }
    }

    /// Lien vers la réunion dans l'interface, où l'on se connecte ; sans son identité,
    /// vers la bibliothèque du projet.
    public static func lien(adresse: URL, projet: String?, reunion: String? = nil) -> URL {
        var c = URLComponents(url: adresse, resolvingAgainstBaseURL: false)!
        c.path = "/"
        c.queryItems = [URLQueryItem(name: "vue", value: "library")]
            + (projet.map { [URLQueryItem(name: "projet", value: $0)] } ?? [])
            + (reunion.map { [URLQueryItem(name: "reunion", value: $0)] } ?? [])
        return c.url!
    }

    /// Retire d'un texte le jeton donné et tout ce qui a la forme d'un jeton d'appareil.
    public static func masquer(_ texte: String, jeton: String?) -> String {
        var t = texte
        if let jeton, !jeton.isEmpty { t = t.replacingOccurrences(of: jeton, with: prefixeJeton + "…") }
        return t.replacingOccurrences(of: #"mgj_[A-Za-z0-9_\-]+"#, with: prefixeJeton + "…", options: .regularExpression)
    }

    private static func commune(statut: Int?, corps: Data?, erreur: String?, jeton: String?) -> Issue? {
        guard let statut else { return .injoignable(masquer(erreur ?? "pas de réponse", jeton: jeton)) }
        switch statut {
        case 200..<300: return nil
        case 401, 403: return .jetonRefuse
        case 404: return .introuvable
        default:
            let detail = objet(corps)?["detail"] as? String ?? HTTPURLResponse.localizedString(forStatusCode: statut)
            return .refuse(code: statut, raison: masquer(detail, jeton: jeton))
        }
    }

    private static func objet(_ corps: Data?) -> [String: Any]? {
        corps.flatMap { try? JSONSerialization.jsonObject(with: $0) } as? [String: Any]
    }
}

/// `Issue` comme erreur, pour `Result`.
public struct IssueErreur: Error, Equatable {
    public let issue: Issue
    public init(_ issue: Issue) { self.issue = issue }
}

/// Les captures qui attendent un envoi : rien n'est perdu si Memoglyph est arrêté.
public struct FileRenvoi: Equatable, Codable, Sendable {
    public private(set) var captures: [Capture] = []
    public init(captures: [Capture] = []) { self.captures = captures }

    public mutating func ajouter(_ c: Capture) {
        if !captures.contains(where: { $0.fichier == c.fichier }) { captures.append(c) }
    }

    public mutating func retirer(_ fichier: String) { captures.removeAll { $0.fichier == fichier } }

    public static func lire(_ donnees: Data?) -> FileRenvoi {
        donnees.flatMap { try? JSONDecoder().decode(FileRenvoi.self, from: $0) } ?? FileRenvoi()
    }

    public func donnees() -> Data { (try? JSONEncoder().encode(self)) ?? Data() }
}

/// Choix d'un processus en Replay : un par PID, jamais tout un service partagé.
public struct Candidat: Equatable, Sendable {
    public let processus: ProcessusDecrit
    public let libelle: String
}

public enum ChoixReplay {
    /// Les processus qui jouent en ce moment, chacun pour lui-même. `applications` : les
    /// applications ouvertes (identifiant, nom), pour nommer le parent d'un auxiliaire.
    public static func candidats(_ processus: [ProcessusDecrit], applications: [(String, String)]) -> [Candidat] {
        processus.filter(\.joue).sorted { $0.pid < $1.pid }.map { p in
            let parent = applications
                .filter { !$0.0.isEmpty && p.identifiant.lowercased().hasPrefix($0.0.lowercased() + ".") }
                .max { $0.0.count < $1.0.count }?.1
            let nom = parent.map { "\($0) · \(p.nom)" } ?? p.nom
            return Candidat(processus: p, libelle: "\(nom) (PID \(p.pid))")
        }
    }

    /// Le processus suivi a-t-il disparu ? Si oui, ceux qui le remplacent : même identifiant
    /// (ou même nom sans identifiant), qui jouent, un autre PID.
    public static func remplacants(de suivi: ProcessusDecrit, parmi processus: [ProcessusDecrit]) -> [ProcessusDecrit]? {
        guard !processus.contains(where: { $0.pid == suivi.pid }) else { return nil }
        return processus.filter {
            $0.joue && $0.pid != suivi.pid
                && (suivi.identifiant.isEmpty ? $0.nom == suivi.nom : $0.identifiant == suivi.identifiant)
        }
    }
}
