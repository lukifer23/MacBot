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

    static func hasSearchCredential() -> Bool {
        SecItemCopyMatching(query(service: "local.macbot.brave-search", returnData: false) as CFDictionary, nil) == errSecSuccess
    }

    static func setSearchCredential(_ value: String) throws {
        let data = Data(value.utf8)
        let service = "local.macbot.brave-search"
        let existing = query(service: service, returnData: false)
        let update = [kSecValueData as String: data]
        let status: OSStatus
        if SecItemCopyMatching(existing as CFDictionary, nil) == errSecSuccess {
            status = SecItemUpdate(existing as CFDictionary, update as CFDictionary)
        } else {
            var add = existing
            add[kSecAttrAccount as String] = "api-key"
            add[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlock
            add[kSecValueData as String] = data
            status = SecItemAdd(add as CFDictionary, nil)
        }
        guard status == errSecSuccess else {
            throw NSError(domain: NSOSStatusErrorDomain, code: Int(status))
        }
    }

    static func deleteSearchCredential() throws {
        let status = SecItemDelete(query(service: "local.macbot.brave-search", returnData: false) as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else {
            throw NSError(domain: NSOSStatusErrorDomain, code: Int(status))
        }
    }

    private static func query(service: String, returnData: Bool) -> [String: Any] {
        var value: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
        ]
        if returnData {
            value[kSecReturnData as String] = true
            value[kSecMatchLimit as String] = kSecMatchLimitOne
        }
        return value
    }
}
