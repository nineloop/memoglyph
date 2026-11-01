import AVFoundation
import Foundation

/// Autorisation du micro, par l'API publique d'AVFoundation.
///
/// macOS n'offre pas d'API publique pour savoir si la capture du son d'une autre
/// application est autorisée : un tap non autorisé rend du silence. C'est donc le
/// bilan de l'enregistrement (`Enregistreur.Bilan.creteSysteme`) qui le révèle.
public enum Autorisations {
    public enum Etat: Equatable {
        case accordee
        case refusee
        case enAttente   // la demande est affichée, personne n'y a répondu
    }

    /// Demande l'accès au micro si macOS ne l'a jamais demandé, et attend la réponse
    /// au plus `attente` secondes. Ne contourne rien : c'est l'utilisateur qui répond.
    public static func micro(attente: TimeInterval) -> Etat {
        switch AVCaptureDevice.authorizationStatus(for: .audio) {
        case .authorized:
            return .accordee
        case .denied, .restricted:
            return .refusee
        case .notDetermined:
            let reponse = DispatchSemaphore(value: 0)
            var accordee = false
            AVCaptureDevice.requestAccess(for: .audio) { ok in
                accordee = ok
                reponse.signal()
            }
            guard reponse.wait(timeout: .now() + attente) == .success else { return .enAttente }
            return accordee ? .accordee : .refusee
        @unknown default:
            return .refusee
        }
    }
}
