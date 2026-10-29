import CryptoKit
import Foundation

typealias EnclaveKey = SecureEnclave.P256.Signing.PrivateKey

func loadKey(_ path: String) throws -> EnclaveKey {
    let blob = try Data(contentsOf: URL(fileURLWithPath: path))
    return try EnclaveKey(dataRepresentation: blob)
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
    case ("public", 2):
        output = try loadKey(args[1]).publicKey.compressedRepresentation
    case ("sign", 2):
        let message = FileHandle.standardInput.readDataToEndOfFile()
        output = try loadKey(args[1]).signature(for: message).rawRepresentation
    default:
        fail("usage: agentmesh-se available | create | public KEY | sign KEY", status: 2)
    }
    FileHandle.standardOutput.write(output)
} catch {
    fail("\(error)", status: 1)
}
