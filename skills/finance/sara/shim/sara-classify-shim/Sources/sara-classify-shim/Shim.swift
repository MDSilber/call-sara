/// The classifier's on-device rung — Apple Foundation Models behind a pipe.
///
/// stdin:  {"categories": [...], "examples": [...], "txns": [{"id": 0, ...}]}
/// stdout: {"results": [{"index": 0, "category": "...", "confidence": 0.93,
///          "reason": "..."}]}
///
/// Guided generation constrains `category` to exactly the provided list —
/// the model cannot invent an account. `--probe` reports availability and
/// nothing else. Exit 0 on success; exit 1 with a one-line reason on stderr
/// when Apple Intelligence can't run (the SPECIFIC SystemLanguageModel
/// unavailability reason) or generation fails; exit 2 on a bad request.
/// Merchant strings never leave the machine.

import Foundation
import FoundationModels

struct Txn: Codable {
    let id: Int
    let date: String
    let payee: String
    let amount: String
    let account: String
    let hint: String
}

struct Request: Decodable {
    let categories: [String]
    let examples: [String]
    let txns: [Txn]
}

struct WireJudgment: Codable {
    let index: Int
    let category: String
    let confidence: Double
    let reason: String
}

struct Reply: Encodable {
    let results: [WireJudgment]
}

@main
struct Shim {
    static func main() async {
        switch SystemLanguageModel.default.availability {
        case .available:
            break
        case .unavailable(let reason):
            die(describe(reason))
        }
        if CommandLine.arguments.dropFirst().contains("--probe") {
            print("available")
            return
        }
        let input = FileHandle.standardInput.readDataToEndOfFile()
        guard let request = try? JSONDecoder().decode(Request.self, from: input),
              !request.categories.isEmpty, !request.txns.isEmpty else {
            die("bad request on stdin (want {categories, examples, txns})", code: 2)
        }
        do {
            let data = try JSONEncoder().encode(try await classify(request))
            print(String(decoding: data, as: UTF8.self))
        } catch {
            die("generation failed: \(error)")
        }
    }

    static func classify(_ request: Request) async throws -> Reply {
        let session = LanguageModelSession(instructions: instructions(for: request))
        let schema = try makeSchema(categories: request.categories,
                                    txnCount: request.txns.count)
        let prompt = String(decoding: try JSONEncoder().encode(request.txns),
                            as: UTF8.self)
        let response = try await session.respond(
            to: prompt, schema: schema,
            options: GenerationOptions(sampling: .greedy))
        let judged = try JSONDecoder().decode(
            [WireJudgment].self, from: Data(response.content.jsonString.utf8))
        return Reply(results: judged)
    }

    /// The same briefing the API rung gives, shortened for a small on-device
    /// context window.
    static func instructions(for request: Request) -> String {
        var parts = [
            """
            You are the bookkeeper for one household's plain-text ledger. \
            Classify each bank/card transaction into exactly one account from \
            the chart. Amounts are signed from the listed account's point of \
            view: negative = money out (an expense), positive = money in \
            (income, a refund, or an incoming transfer). Movements between the \
            household's own accounts (card autopay, brokerage funding) belong \
            in the transfers account when the chart lists one. A `hint` is an \
            aggregator's low-confidence guess — weigh it, don't trust it. If \
            nothing clearly fits, still pick the closest account but give it \
            confidence below 0.5. Answer every transaction by its id, once \
            each; `index` is that id; confidence is your 0-1 certainty; \
            reason is one short line.
            """,
            "CHART (the only legal answers):\n"
                + request.categories.joined(separator: "\n"),
        ]
        if !request.examples.isEmpty {
            parts.append("KNOWN PAYEES (payee -> account):\n"
                + request.examples.joined(separator: "\n"))
        }
        return parts.joined(separator: "\n\n")
    }

    /// Guided generation: an array of exactly `txnCount` judgments whose
    /// `category` is an enum of the vault's real chart.
    static func makeSchema(categories: [String], txnCount: Int) throws -> GenerationSchema {
        let judgment = DynamicGenerationSchema(
            name: "Judgment",
            properties: [
                .init(name: "index",
                      description: "the id of the transaction being judged",
                      schema: DynamicGenerationSchema(type: Int.self)),
                .init(name: "category",
                      description: "one account from the chart",
                      schema: DynamicGenerationSchema(name: "Category",
                                                      anyOf: categories)),
                .init(name: "confidence",
                      description: "certainty from 0 to 1",
                      schema: DynamicGenerationSchema(type: Double.self)),
                .init(name: "reason",
                      description: "one short line",
                      schema: DynamicGenerationSchema(type: String.self)),
            ])
        let root = DynamicGenerationSchema(
            arrayOf: judgment, minimumElements: txnCount, maximumElements: txnCount)
        return try GenerationSchema(root: root, dependencies: [])
    }

    static func describe(
        _ reason: SystemLanguageModel.Availability.UnavailableReason
    ) -> String {
        switch reason {
        case .deviceNotEligible:
            return "unavailable: this Mac's hardware can't run Apple Intelligence"
        case .appleIntelligenceNotEnabled:
            return "unavailable: Apple Intelligence is off — enable it in "
                + "System Settings > Apple Intelligence & Siri, then re-run"
        case .modelNotReady:
            return "unavailable: the on-device model is still downloading — "
                + "retry in a few minutes"
        @unknown default:
            return "unavailable: \(String(describing: reason))"
        }
    }

    static func die(_ message: String, code: Int32 = 1) -> Never {
        FileHandle.standardError.write(Data((message + "\n").utf8))
        exit(code)
    }
}
