import CryptoKit
import Foundation
import LocalAuthentication
import Security

typealias EnclaveKey = SecureEnclave.P256.Signing.PrivateKey

func loadKey(_ path: String, reason: String? = nil) throws -> EnclaveKey {
    let blob = try Data(contentsOf: URL(fileURLWithPath: path))
    guard let reason else {
        return try EnclaveKey(dataRepresentation: blob)
    }
    let context = LAContext()
    context.localizedReason = reason
    return try EnclaveKey(dataRepresentation: blob, authenticationContext: context)
}

// The enclave itself refuses to sign with this key until Touch ID or the login password
// succeeds, so no process running as the user can sign silently.
func presenceKey() throws -> EnclaveKey {
    var error: Unmanaged<CFError>?
    guard
        let access = SecAccessControlCreateWithFlags(
            nil, kSecAttrAccessibleWhenUnlockedThisDeviceOnly,
            [.privateKeyUsage, .userPresence], &error)
    else {
        throw error!.takeRetainedValue() as Error
    }
    return try EnclaveKey(accessControl: access)
}

func fail(_ message: String, status: Int32) -> Never {
    FileHandle.standardError.write(Data((message + "\n").utf8))
    exit(status)
}

let args = Array(CommandLine.arguments.dropFirst())
do {
    let output: Data
    switch (args.first, args.count) {
    case ("available", 1):
        if !SecureEnclave.isAvailable {
            fail("this Mac has no Secure Enclave", status: 3)
        }
        output = Data()
    case ("create", 1):
        output = try EnclaveKey().dataRepresentation
    case ("create-presence", 1):
        output = try presenceKey().dataRepresentation
    case ("public", 2):
        output = try loadKey(args[1]).publicKey.compressedRepresentation
    case ("sign", 2), ("sign", 3):
        let message = FileHandle.standardInput.readDataToEndOfFile()
        let reason = args.count == 3 ? args[2] : nil
        output = try loadKey(args[1], reason: reason).signature(for: message).rawRepresentation
    default:
        fail(
            "usage: ehor-se available | create | create-presence | public KEY | sign KEY [REASON]",
            status: 2)
    }
    FileHandle.standardOutput.write(output)
} catch {
    fail("\(error)", status: 1)
}
