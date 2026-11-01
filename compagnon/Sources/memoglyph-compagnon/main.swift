import AppKit

// Application de barre de menus : `.accessory`, donc ni icône dans le Dock ni menu
// d'application, en plus de LSUIElement dans l'Info.plist embarqué.
MainActor.assumeIsolated {
    let application = NSApplication.shared
    let compagnon = Compagnon()
    application.delegate = compagnon
    application.setActivationPolicy(.accessory)
    application.run()
}
