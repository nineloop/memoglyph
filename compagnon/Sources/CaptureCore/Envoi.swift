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
    /// `GET /api/jobs` : avec un jeton d'appareil, les comptes du compte et ses seules captures.
    case traitements

    public var methode: String { self == .envoi ? "POST" : "GET" }

    public var chemin: String {
        switch self {
        case .sante: return "/api/health"
        case .projets: return "/api/projects"
        case .envoi: return "/api/transcribe"
        case .travail(let id): return "/api/jobs/\(id.addingPercentEncoding(withAllowedCharacters: .alphanumerics) ?? "")"
        case .traitements: return "/api/jobs"
        }
    }
}

/// Un projet du compte, tel que `GET /api/projects` le décrit.
public struct Projet: Equatable, Sendable {
    public let id: String
    public let nom: String
    /// `inbox` pour le « Non classé » du compte : son nom stocké est une constante, affichée traduite.
    public let kind: String
    public init(id: String, nom: String, kind: String = "") { self.id = id; self.nom = nom; self.kind = kind }
}

/// Ce qu'une capture emporte vers Memoglyph.
///
/// `captureId` (un UUID en minuscules) est fixé à la création et reste le même à travers les renvois :
/// c'est lui qui permet au serveur de reconnaître une capture déjà reçue (réponse perdue, renvoi).
public struct Capture: Equatable, Codable, Sendable {
    public let fichier: String        // nom du fichier dans le dossier des captures
    public let visio: Bool
    public let application: String    // provenance : `capture_application`
    public let captureId: String
    public init(fichier: String, visio: Bool, application: String, captureId: String = Capture.nouvelIdentifiant()) {
        self.fichier = fichier; self.visio = visio; self.application = application
        self.captureId = captureId.lowercased()
    }

    public static func nouvelIdentifiant() -> String { UUID().uuidString.lowercased() }

    /// Une file écrite avant ce chantier n'a pas de `captureId` : en lui en donnant un à la lecture,
    /// la file se relit entière au lieu d'être jetée (ce qui perdrait des captures en attente).
    public init(from decodeur: Decoder) throws {
        let c = try decodeur.container(keyedBy: Cles.self)
        self.init(fichier: try c.decode(String.self, forKey: .fichier),
                  visio: try c.decode(Bool.self, forKey: .visio),
                  application: try c.decode(String.self, forKey: .application),
                  captureId: (try c.decodeIfPresent(String.self, forKey: .captureId))
                      .flatMap { $0.isEmpty ? nil : $0 } ?? Capture.nouvelIdentifiant())
    }

    private enum Cles: String, CodingKey { case fichier, visio, application, captureId }
}

/// Issue d'un appel, sans jamais le jeton dans le texte.
public enum Issue: Equatable, Sendable {
    case accepte(travail: String)
    case jetonRefuse
    case introuvable
    case refuse(code: Int, raison: String)
    case injoignable(String)
    /// Le serveur a déjà cette capture (réponse perdue, renvoi) : la réunion, ou le traitement en cours.
    /// `meetingId` et `projet` : de quoi ouvrir la réunion elle-même.
    case dejaRecue(reunion: String?, travail: String?, meetingId: String? = nil, projet: String? = nil)

    public var texte: String {
        switch self {
        case .accepte, .dejaRecue: return "Capture reçue par Memoglyph."
        case .jetonRefuse: return "Jeton refusé : créez-en un dans « Appareils », puis reliez à nouveau le compagnon."
        case .introuvable: return "Memoglyph ne connaît pas cette adresse ou ce projet."
        case .refuse(let code, let raison): return "Memoglyph a refusé l'envoi (\(code)) : \(raison)"
        case .injoignable(let raison): return "Memoglyph est injoignable : \(raison)"
        }
    }
}

/// État d'un travail suivi par `GET /api/jobs/{job_id}`.
public enum EtatTravail: Equatable, Sendable {
    /// `code` et `enAttente` : l'étape (code de message du serveur, que le compagnon traduit) et le
    /// créneau pas encore obtenu. `message` reste le texte du serveur, jamais affiché tel quel.
    /// `projetNom` : le projet où le traitement range la réunion, tel que le serveur le nomme.
    case enCours(progression: Double, message: String, code: String? = nil, enAttente: Bool = false, projetNom: String? = nil)
    /// `meetingId` et `projet` : l'identité de la réunion, pour l'ouvrir (`lien`).
    case fini(reunion: String, avertissements: [String], meetingId: String? = nil, projet: String? = nil, projetNom: String? = nil)
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
            ("capture_id", capture.captureId),
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
        let o = objet(corps)
        if o?["deja_recue"] as? Bool == true {
            let reunion = (o?["meeting"] as? String).flatMap { $0.isEmpty ? nil : $0 }
            let travail = (o?["job_id"] as? String).flatMap { $0.isEmpty ? nil : $0 }
            let meetingId = (o?["meeting_id"] as? String).flatMap { $0.isEmpty ? nil : $0 }
            let projet = (o?["project_id"] as? String).flatMap { $0.isEmpty ? nil : $0 }
            if reunion != nil || travail != nil {
                return .dejaRecue(reunion: reunion, travail: travail, meetingId: meetingId, projet: projet)
            }
        }
        guard let id = o?["job_id"] as? String, !id.isEmpty else {
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
            return Projet(id: id, nom: (p["project"] as? String) ?? (p["name"] as? String) ?? id,
                          kind: p["kind"] as? String ?? "")
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
                         projet: (resultat["project_id"] as? String).flatMap { $0.isEmpty ? nil : $0 },
                         projetNom: (o["projet_nom"] as? String).flatMap { $0.isEmpty ? nil : masquer($0, jeton: jeton) })
        case "error":
            return .echec(masquer(o["error"] as? String ?? o["message"] as? String ?? "erreur inconnue", jeton: jeton))
        default:
            return .enCours(progression: (o["progress"] as? NSNumber)?.doubleValue ?? 0,
                            message: masquer(o["message"] as? String ?? "", jeton: jeton),
                            code: (o["message_code"] as? String).flatMap { $0.isEmpty ? nil : $0 },
                            enAttente: (o["en_attente"] as? Bool ?? false) || o["status"] as? String == "pending",
                            projetNom: (o["projet_nom"] as? String).flatMap { $0.isEmpty ? nil : masquer($0, jeton: jeton) })
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

    /// Lien « Suivre » : la vue des traitements, depuis n'importe quel écran de l'interface.
    public static func lienSuivre(adresse: URL) -> URL {
        var c = URLComponents(url: adresse, resolvingAgainstBaseURL: false)!
        c.path = "/"
        c.queryItems = [URLQueryItem(name: "traitements", value: "1")]
        return c.url!
    }

    /// Réponse de `GET /api/jobs` avec le jeton d'appareil : les comptes et SES captures seulement
    /// (le serveur n'envoie ni titre ni état d'une autre réunion ; on ne lit rien d'autre ici).
    public static func lireComptes(statut: Int?, corps: Data?, erreur: String?, jeton: String?) -> Result<Comptes, IssueErreur> {
        if let issue = commune(statut: statut, corps: corps, erreur: erreur, jeton: jeton) { return .failure(IssueErreur(issue)) }
        let o = objet(corps) ?? [:]
        let captures = (o["traitements"] as? [[String: Any]] ?? []).compactMap { t -> TraitementSuivi? in
            guard let id = t["id"] as? String, !id.isEmpty else { return nil }
            return TraitementSuivi(id: id, titre: masquer(t["titre"] as? String ?? "", jeton: jeton),
                                   projet: masquer(t["projet_nom"] as? String ?? "", jeton: jeton),
                                   statut: t["statut"] as? String ?? "")
        }
        return .success(Comptes(actifs: (o["actifs"] as? NSNumber)?.intValue ?? 0,
                                enAttente: (o["en_attente"] as? NSNumber)?.intValue ?? 0, captures: captures))
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

/// Une capture née de ce jeton, telle que la liste des traitements la décrit.
public struct TraitementSuivi: Equatable, Sendable {
    public let id: String
    public let titre: String
    public let projet: String
    public let statut: String
    public init(id: String, titre: String, projet: String, statut: String) {
        self.id = id; self.titre = titre; self.projet = projet; self.statut = statut
    }
}

/// Ce que `GET /api/jobs` apprend au compagnon : combien de traitements tournent ou attendent,
/// et l'état de ses propres captures.
public struct Comptes: Equatable, Sendable {
    public let actifs: Int
    public let enAttente: Int
    public let captures: [TraitementSuivi]
    public init(actifs: Int, enAttente: Int, captures: [TraitementSuivi]) {
        self.actifs = actifs; self.enAttente = enAttente; self.captures = captures
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
        lireOuNil(donnees) ?? FileRenvoi()
    }

    /// `nil` quand des données existent mais ne se lisent pas : l'appelant les met alors de côté au lieu
    /// de les écraser par une file vide (ce qui perdrait des captures en attente).
    public static func lireOuNil(_ donnees: Data?) -> FileRenvoi? {
        donnees.flatMap { try? JSONDecoder().decode(FileRenvoi.self, from: $0) }
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
