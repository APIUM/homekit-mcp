public final class SingleUseCallback<Value> {
    private var callback: ((Value) -> Void)?

    public init(_ callback: @escaping (Value) -> Void) {
        self.callback = callback
    }

    public var isUsed: Bool {
        callback == nil
    }

    public func callAsFunction(_ value: Value) {
        guard let callback = callback else {
            return
        }
        self.callback = nil
        callback(value)
    }
}
