import XCTest
@testable import HomeKitMCPCore

final class HTTPRequestBufferTests: XCTestCase {
    private let body = #"{"jsonrpc":"2.0","id":1,"method":"tools/list"}"#

    private func post(_ body: String, contentLength: String? = nil) -> String {
        let length = contentLength ?? String(body.utf8.count)
        return "POST /mcp HTTP/1.1\r\nHost: 127.0.0.1:8080\r\nContent-Type: application/json\r\n"
            + "Content-Length: \(length)\r\n\r\n\(body)"
    }

    private func feed(_ chunks: [String]) -> [HTTPParseResult] {
        var buffer = HTTPRequestBuffer()
        return chunks.map { buffer.append(Data($0.utf8)) }
    }

    private func completed(_ result: HTTPParseResult?, file: StaticString = #filePath,
                           line: UInt = #line) -> HTTPRequest? {
        guard case .complete(let request)? = result else {
            XCTFail("Expected a complete request, got \(String(describing: result))", file: file, line: line)
            return nil
        }
        return request
    }

    func testWholeRequestInOneChunk() throws {
        let request = try XCTUnwrap(completed(feed([post(body)]).last))
        XCTAssertEqual(request.method, "POST")
        XCTAssertEqual(request.path, "/mcp")
        XCTAssertEqual(request.header("Content-Type"), "application/json")
        XCTAssertEqual(String(data: request.body, encoding: .utf8), body)
    }

    func testHeadersSplitAcrossChunks() throws {
        let raw = post(body)
        let split = raw.index(raw.startIndex, offsetBy: 20)
        let results = feed([String(raw[..<split]), String(raw[split...])])
        XCTAssertEqual(results.first, .incomplete)
        let request = try XCTUnwrap(completed(results.last))
        XCTAssertEqual(String(data: request.body, encoding: .utf8), body)
    }

    func testSeparatorSplitAcrossChunks() throws {
        let raw = post(body)
        let separator = try XCTUnwrap(raw.range(of: "\r\n\r\n"))
        let split = raw.index(separator.lowerBound, offsetBy: 2)
        let results = feed([String(raw[..<split]), String(raw[split...])])
        XCTAssertEqual(results.first, .incomplete)
        XCTAssertNotNil(completed(results.last))
    }

    func testBodyInSeveralChunks() throws {
        let raw = post(body)
        let separator = try XCTUnwrap(raw.range(of: "\r\n\r\n"))
        let headers = String(raw[..<separator.upperBound])
        let results = feed([headers, "{\"jsonrpc\":", "\"2.0\",\"id\":1,", "\"method\":\"tools/list\"}"])
        XCTAssertEqual(Array(results.dropLast()), [.incomplete, .incomplete, .incomplete])
        let request = try XCTUnwrap(completed(results.last))
        XCTAssertEqual(String(data: request.body, encoding: .utf8), body)
    }

    func testContentLengthCountsBytesNotCharacters() throws {
        let unicodeBody = #"{"name":"Café"}"#
        let request = try XCTUnwrap(completed(feed([post(unicodeBody)]).last))
        XCTAssertEqual(String(data: request.body, encoding: .utf8), unicodeBody)
    }

    func testBytesBeyondContentLengthAreIgnored() throws {
        let request = try XCTUnwrap(completed(feed([post(body) + "GET / HTTP/1.1\r\n\r\n"]).last))
        XCTAssertEqual(String(data: request.body, encoding: .utf8), body)
    }

    func testGetWithoutContentLengthHasEmptyBody() throws {
        let request = try XCTUnwrap(completed(feed(["GET /mcp HTTP/1.1\r\nHost: localhost:8080\r\n\r\n"]).last))
        XCTAssertEqual(request.method, "GET")
        XCTAssertTrue(request.body.isEmpty)
    }

    func testPostWithoutContentLengthIsRejected() {
        let raw = "POST /mcp HTTP/1.1\r\nHost: 127.0.0.1:8080\r\n\r\n\(body)"
        XCTAssertEqual(feed([raw]).last, .failed(HTTPFailure(status: 411, message: "Content-Length required")))
    }

    func testInvalidContentLengthIsRejected() {
        for value in ["abc", "-1", "+5", "1.5", "", "99999999999999999999999"] {
            guard case .failed(let failure)? = feed([post(body, contentLength: value)]).last else {
                XCTFail("Content-Length \(value) was accepted")
                continue
            }
            XCTAssertEqual(failure.status, 400, "Content-Length \(value)")
        }
    }

    func testConflictingContentLengthIsRejected() {
        let raw = "POST /mcp HTTP/1.1\r\nContent-Length: 2\r\nContent-Length: 3\r\n\r\n{}"
        guard case .failed(let failure)? = feed([raw]).last else {
            return XCTFail("Conflicting Content-Length was accepted")
        }
        XCTAssertEqual(failure.status, 400)
    }

    func testOversizeBodyIsRejectedBeforeItArrives() {
        let length = String(HTTPRequestBuffer.maxBodyBytes + 1)
        guard case .failed(let failure)? = feed([post("", contentLength: length)]).last else {
            return XCTFail("Oversize body was accepted")
        }
        XCTAssertEqual(failure.status, 413)
    }

    func testBodyOfExactlyTheLimitIsAccepted() throws {
        let large = String(repeating: "a", count: HTTPRequestBuffer.maxBodyBytes)
        let request = try XCTUnwrap(completed(feed([post(large)]).last))
        XCTAssertEqual(request.body.count, HTTPRequestBuffer.maxBodyBytes)
    }

    func testOversizeHeadersAreRejected() {
        let raw = "GET / HTTP/1.1\r\nX-Filler: " + String(repeating: "a", count: HTTPRequestBuffer.maxHeaderBytes)
        guard case .failed(let failure)? = feed([raw]).last else {
            return XCTFail("Oversize headers were accepted")
        }
        XCTAssertEqual(failure.status, 431)
    }

    func testTransferEncodingIsRejected() {
        let raw = "POST /mcp HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n"
        guard case .failed(let failure)? = feed([raw]).last else {
            return XCTFail("Chunked request was accepted")
        }
        XCTAssertEqual(failure.status, 501)
    }

    func testMalformedRequestLineIsRejected() {
        for raw in ["INVALID REQUEST\r\n\r\n", "GET mcp HTTP/1.1\r\n\r\n", "GET /mcp SPDY/3\r\n\r\n"] {
            guard case .failed(let failure)? = feed([raw]).last else {
                XCTFail("\(raw.debugDescription) was accepted")
                continue
            }
            XCTAssertEqual(failure.status, 400)
        }
    }

    func testHeaderWithoutColonIsRejected() {
        guard case .failed(let failure)? = feed(["GET / HTTP/1.1\r\nHost 127.0.0.1\r\n\r\n"]).last else {
            return XCTFail("Header without colon was accepted")
        }
        XCTAssertEqual(failure.status, 400)
    }

    func testEndOfStreamBeforeBodyCompletes() {
        var buffer = HTTPRequestBuffer()
        XCTAssertEqual(buffer.append(Data(post(body, contentLength: "500").utf8)), .incomplete)
        XCTAssertFalse(buffer.isEmpty)
        XCTAssertEqual(buffer.endOfStream().status, 400)
    }
}

final class HTTPRequestPolicyTests: XCTestCase {
    private func request(method: String = "POST", headers: [String: String]) -> HTTPRequest {
        HTTPRequest(method: method, path: "/mcp", headers: headers, body: Data())
    }

    private let jsonHeaders = ["host": "127.0.0.1:8080", "content-type": "application/json"]

    func testLoopbackHostsAreAccepted() {
        XCTAssertNil(HTTPRequestPolicy.check(request(headers: jsonHeaders), port: 8080))
        var localhost = jsonHeaders
        localhost["host"] = "LOCALHOST:8080"
        XCTAssertNil(HTTPRequestPolicy.check(request(headers: localhost), port: 8080))
    }

    func testOriginHeaderIsRejected() {
        var headers = jsonHeaders
        headers["origin"] = "http://127.0.0.1:8080"
        XCTAssertEqual(HTTPRequestPolicy.check(request(headers: headers), port: 8080)?.status, 403)
    }

    func testOtherHostsAreRejected() {
        for host in ["attacker.example:8080", "127.0.0.1", "localhost:80", "192.168.1.5:8080", "127.0.0.1:8080.evil"] {
            var headers = jsonHeaders
            headers["host"] = host
            XCTAssertEqual(HTTPRequestPolicy.check(request(headers: headers), port: 8080)?.status, 403, host)
        }
    }

    func testMissingHostIsRejected() {
        XCTAssertEqual(HTTPRequestPolicy.check(request(headers: ["content-type": "application/json"]),
                                               port: 8080)?.status, 403)
    }

    func testPostRequiresJSONContentType() {
        XCTAssertNil(HTTPRequestPolicy.check(
            request(headers: ["host": "127.0.0.1:8080", "content-type": "Application/JSON; charset=utf-8"]),
            port: 8080))
        for contentType in ["text/plain", "application/x-www-form-urlencoded", "multipart/form-data"] {
            let headers = ["host": "127.0.0.1:8080", "content-type": contentType]
            XCTAssertEqual(HTTPRequestPolicy.check(request(headers: headers), port: 8080)?.status, 415, contentType)
        }
        XCTAssertEqual(HTTPRequestPolicy.check(request(headers: ["host": "127.0.0.1:8080"]), port: 8080)?.status, 415)
    }

    func testGetDoesNotNeedContentType() {
        XCTAssertNil(HTTPRequestPolicy.check(request(method: "GET", headers: ["host": "localhost:8080"]), port: 8080))
    }

    func testParsedHeadersFeedThePolicy() throws {
        var buffer = HTTPRequestBuffer()
        let raw = "POST /mcp HTTP/1.1\r\nHOST: 127.0.0.1:8080\r\nOrigin: https://example.com\r\n"
            + "Content-Type: application/json\r\nContent-Length: 2\r\n\r\n{}"
        guard case .complete(let parsed) = buffer.append(Data(raw.utf8)) else {
            return XCTFail("Request did not parse")
        }
        XCTAssertEqual(HTTPRequestPolicy.check(parsed, port: 8080)?.status, 403)
    }
}

final class HTTPResponseTests: XCTestCase {
    func testContentLengthIsByteCount() throws {
        let body = Data("Café ✓".utf8)
        let response = HTTPResponse.serialize(status: 200, contentType: "application/json", body: body)
        let text = try XCTUnwrap(String(data: response, encoding: .utf8))
        XCTAssertTrue(text.hasPrefix("HTTP/1.1 200 OK\r\n"))
        XCTAssertTrue(text.contains("\r\nContent-Length: \(body.count)\r\n"))
        XCTAssertTrue(text.hasSuffix("\r\n\r\nCafé ✓"))
    }

    func testNoCORSHeaderIsSent() throws {
        let response = HTTPResponse.failure(HTTPFailure(status: 403, message: "Forbidden"))
        let text = try XCTUnwrap(String(data: response, encoding: .utf8))
        XCTAssertTrue(text.hasPrefix("HTTP/1.1 403 Forbidden\r\n"))
        XCTAssertFalse(text.lowercased().contains("access-control"))
    }
}
