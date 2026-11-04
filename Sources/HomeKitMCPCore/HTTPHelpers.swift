import Foundation

public struct HTTPRequest: Equatable {
    public let method: String
    public let path: String
    public let headers: [String: String]
    public let body: Data

    public init(method: String, path: String, headers: [String: String], body: Data) {
        self.method = method
        self.path = path
        self.headers = headers
        self.body = body
    }

    public func header(_ name: String) -> String? {
        headers[name.lowercased()]
    }
}

public struct HTTPFailure: Error, Equatable {
    public let status: Int
    public let message: String

    public init(status: Int, message: String) {
        self.status = status
        self.message = message
    }
}

public enum HTTPParseResult: Equatable {
    case incomplete
    case complete(HTTPRequest)
    case failed(HTTPFailure)
}

public struct HTTPRequestBuffer {
    public static let maxHeaderBytes = 64 * 1024
    public static let maxBodyBytes = 1024 * 1024

    private struct Head {
        let method: String
        let path: String
        let headers: [String: String]
        let bodyStart: Int
        let contentLength: Int
    }

    private var data = Data()
    private var head: Head?

    public init() {}

    public var isEmpty: Bool {
        data.isEmpty
    }

    public mutating func append(_ chunk: Data) -> HTTPParseResult {
        data.append(chunk)
        if head == nil {
            guard let separator = data.range(of: Data("\r\n\r\n".utf8)) else {
                if data.count > Self.maxHeaderBytes {
                    return .failed(HTTPFailure(status: 431, message: "Request headers too large"))
                }
                return .incomplete
            }
            switch Self.parseHead(data.subdata(in: 0..<separator.lowerBound), bodyStart: separator.upperBound) {
            case .success(let parsed):
                head = parsed
            case .failure(let failure):
                return .failed(failure)
            }
        }
        guard let head = head else {
            return .incomplete
        }
        guard data.count - head.bodyStart >= head.contentLength else {
            return .incomplete
        }
        let body = data.subdata(in: head.bodyStart..<(head.bodyStart + head.contentLength))
        return .complete(HTTPRequest(method: head.method, path: head.path, headers: head.headers, body: body))
    }

    public func endOfStream() -> HTTPFailure {
        HTTPFailure(status: 400, message: "Connection closed before the request was complete")
    }

    private static func parseHead(_ bytes: Data, bodyStart: Int) -> Result<Head, HTTPFailure> {
        let badRequest = HTTPFailure(status: 400, message: "Bad Request")
        guard let text = String(data: bytes, encoding: .utf8) else {
            return .failure(badRequest)
        }
        let lines = text.components(separatedBy: "\r\n")
        let requestLine = lines[0].split(separator: " ", omittingEmptySubsequences: false)
        guard requestLine.count == 3, !requestLine[0].isEmpty, requestLine[1].hasPrefix("/"),
              requestLine[2].hasPrefix("HTTP/1.") else {
            return .failure(badRequest)
        }

        var headers: [String: String] = [:]
        for line in lines.dropFirst() {
            guard let colon = line.firstIndex(of: ":") else {
                return .failure(badRequest)
            }
            let name = line[..<colon].lowercased()
            let value = line[line.index(after: colon)...].trimmingCharacters(in: .whitespaces)
            guard !name.isEmpty, !name.contains(" ") else {
                return .failure(badRequest)
            }
            if let existing = headers[name], name == "content-length", existing != value {
                return .failure(HTTPFailure(status: 400, message: "Conflicting Content-Length"))
            }
            headers[name] = value
        }

        if headers["transfer-encoding"] != nil {
            return .failure(HTTPFailure(status: 501, message: "Transfer-Encoding is not supported"))
        }

        let method = String(requestLine[0])
        let contentLength: Int
        if let raw = headers["content-length"] {
            guard !raw.isEmpty, raw.allSatisfy({ $0.isASCII && $0.isNumber }), let value = Int(raw) else {
                return .failure(HTTPFailure(status: 400, message: "Invalid Content-Length"))
            }
            contentLength = value
        } else if method == "POST" {
            return .failure(HTTPFailure(status: 411, message: "Content-Length required"))
        } else {
            contentLength = 0
        }
        guard contentLength <= maxBodyBytes else {
            return .failure(HTTPFailure(status: 413, message: "Request body larger than 1 MiB"))
        }

        return .success(Head(method: method, path: String(requestLine[1]), headers: headers,
                             bodyStart: bodyStart, contentLength: contentLength))
    }
}

public enum HTTPRequestPolicy {
    public static func check(_ request: HTTPRequest, port: UInt16) -> HTTPFailure? {
        if request.header("origin") != nil {
            return HTTPFailure(status: 403, message: "Browser requests are not accepted")
        }
        let allowedHosts = ["127.0.0.1:\(port)", "localhost:\(port)"]
        guard let host = request.header("host")?.lowercased(), allowedHosts.contains(host) else {
            return HTTPFailure(status: 403, message: "Host must be 127.0.0.1:\(port) or localhost:\(port)")
        }
        if request.method == "POST" {
            let mediaType = request.header("content-type")?
                .split(separator: ";", maxSplits: 1).first?
                .trimmingCharacters(in: .whitespaces).lowercased()
            guard mediaType == "application/json" else {
                return HTTPFailure(status: 415, message: "Content-Type must be application/json")
            }
        }
        return nil
    }
}

public enum HTTPResponse {
    public static func reasonPhrase(_ status: Int) -> String {
        switch status {
        case 200: return "OK"
        case 400: return "Bad Request"
        case 403: return "Forbidden"
        case 404: return "Not Found"
        case 411: return "Length Required"
        case 413: return "Payload Too Large"
        case 415: return "Unsupported Media Type"
        case 431: return "Request Header Fields Too Large"
        case 501: return "Not Implemented"
        default: return "Internal Server Error"
        }
    }

    public static func serialize(status: Int, contentType: String, body: Data,
                                 extraHeaders: [String] = []) -> Data {
        var lines = [
            "HTTP/1.1 \(status) \(reasonPhrase(status))",
            "Content-Type: \(contentType)",
            "Content-Length: \(body.count)",
            "Connection: close"
        ]
        lines += extraHeaders
        var response = Data((lines.joined(separator: "\r\n") + "\r\n\r\n").utf8)
        response.append(body)
        return response
    }

    public static func failure(_ failure: HTTPFailure) -> Data {
        serialize(status: failure.status, contentType: "text/plain; charset=utf-8", body: Data(failure.message.utf8))
    }
}
