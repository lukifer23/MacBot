import Foundation
import Security

enum KeychainStore {
    static let service = "local.macbot.history"
    static let account = "history-key"

    static func ensureHistoryKey() throws {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var item: CFTypeRef?
        let found = SecItemCopyMatching(query as CFDictionary, &item)
        if found == errSecSuccess { return }
        guard found == errSecItemNotFound else {
            throw NSError(domain: NSOSStatusErrorDomain, code: Int(found))
        }
        var bytes = [UInt8](repeating: 0, count: 32)
        guard SecRandomCopyBytes(kSecRandomDefault, bytes.count, &bytes) == errSecSuccess else {
            throw NSError(domain: "MacBot", code: 1, userInfo: [NSLocalizedDescriptionKey: "Could not generate the history key"])
        }
        let encoded = Data(bytes).base64EncodedString().data(using: .utf8)!
        let add: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlock,
            kSecValueData as String: encoded,
        ]
        let status = SecItemAdd(add as CFDictionary, nil)
        guard status == errSecSuccess || status == errSecDuplicateItem else {
            throw NSError(domain: NSOSStatusErrorDomain, code: Int(status))
        }
    }
}
