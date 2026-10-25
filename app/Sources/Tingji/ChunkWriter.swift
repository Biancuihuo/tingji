import AVFoundation

/// 把任意格式的 PCM 转成 16 kHz 单声道（和心力球一样只留「给机器听」的低保真），
/// 按一分钟一片写成 AAC：先写在 spool/<来源>/open/，写完挪到 spool/<来源>/<开始毫秒>.m4a 交给后台处理。
final class ChunkWriter {
    let source: String
    private let dir: URL
    private let openDir: URL
    private let chunkFrames = AVAudioFramePosition(16000 * 60)
    private let target = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: 16000, channels: 1, interleaved: false)!
    private let queue: DispatchQueue

    private var converter: AVAudioConverter?
    private var converterInput: AVAudioFormat?
    private var file: AVAudioFile?
    private var fileURL: URL?
    private var fileStartMs: Int64 = 0
    private var fileFrames: AVAudioFramePosition = 0
    private var lastAppend = Date.distantPast

    // 给菜单栏看的
    private(set) var levelDB: Float = -120
    private(set) var lastChunkAt: Date?

    init(source: String, root: URL) {
        self.source = source
        dir = root.appendingPathComponent(source)
        openDir = dir.appendingPathComponent("open")
        queue = DispatchQueue(label: "tingji.writer.\(source)")
        try? FileManager.default.createDirectory(at: openDir, withIntermediateDirectories: true)
        // 上次异常退出留下的半截文件没有文件尾，解不开，直接删
        for f in (try? FileManager.default.contentsOfDirectory(at: openDir, includingPropertiesForKeys: nil)) ?? [] {
            try? FileManager.default.removeItem(at: f)
        }
    }

    /// 音频线程里调用：拷一份交给串行队列，不在音频线程里做转换和写盘
    func append(_ buffer: AVAudioPCMBuffer) {
        guard let copy = ChunkWriter.copy(buffer) else { return }
        let now = Date()
        queue.async { self.write(copy, receivedAt: now) }
    }

    /// 没有新数据超过 3 秒就把手上的分片收尾（通话结束、暂停、设备断开）
    func flushIfIdle() {
        queue.async {
            if self.file != nil && Date().timeIntervalSince(self.lastAppend) > 3 { self.close() }
        }
    }

    func stop() { queue.sync { self.close() } }

    /// 随口记、声纹说完时立刻交出手上的分片，后台不用等满一分钟
    func rotate() { queue.async { self.close() } }

    private func write(_ buffer: AVAudioPCMBuffer, receivedAt: Date) {
        if converterInput != buffer.format {
            converter = AVAudioConverter(from: buffer.format, to: target)
            converter?.downmix = true
            converterInput = buffer.format
        }
        guard let converter else { return }
        let ratio = 16000 / buffer.format.sampleRate
        let cap = AVAudioFrameCount(Double(buffer.frameLength) * ratio) + 64
        guard let out = AVAudioPCMBuffer(pcmFormat: target, frameCapacity: cap) else { return }
        var fed = false
        var err: NSError?
        converter.convert(to: out, error: &err) { _, status in
            if fed { status.pointee = .noDataNow; return nil }
            fed = true
            status.pointee = .haveData
            return buffer
        }
        if err != nil || out.frameLength == 0 { return }
        updateLevel(out)

        let durMs = Int64(Double(buffer.frameLength) / buffer.format.sampleRate * 1000)
        let startMs = Int64(receivedAt.timeIntervalSince1970 * 1000) - durMs
        // 时间对不上（中间断过）就另起一片，保证每片的开始时间 + 帧数 = 真实时间
        if file != nil {
            let expected = fileStartMs + Int64(Double(fileFrames) / 16)
            if abs(startMs - expected) > 1500 || fileFrames >= chunkFrames { close() }
        }
        if file == nil { open(startMs: startMs) }
        do {
            try file?.write(from: out)
            fileFrames += AVAudioFramePosition(out.frameLength)
            lastAppend = receivedAt
        } catch {
            Log.write("[\(source)] 写入失败：\(error)")
            close()
        }
    }

    private func open(startMs: Int64) {
        let url = openDir.appendingPathComponent("\(startMs).m4a")
        let settings: [String: Any] = [
            AVFormatIDKey: kAudioFormatMPEG4AAC,
            AVSampleRateKey: 16000,
            AVNumberOfChannelsKey: 1,
            AVEncoderBitRateKey: 32000,
        ]
        do {
            file = try AVAudioFile(forWriting: url, settings: settings, commonFormat: .pcmFormatFloat32, interleaved: false)
            fileURL = url
            fileStartMs = startMs
            fileFrames = 0
        } catch {
            Log.write("[\(source)] 建分片失败：\(error)")
            file = nil
        }
    }

    private func close() {
        guard let f = file, let url = fileURL else { return }
        f.close()
        file = nil
        fileURL = nil
        if fileFrames < 1600 {  // 不到 0.1 秒，丢掉
            try? FileManager.default.removeItem(at: url)
            return
        }
        let dest = dir.appendingPathComponent(url.lastPathComponent)
        try? FileManager.default.moveItem(at: url, to: dest)
        lastChunkAt = Date()
    }

    private func updateLevel(_ b: AVAudioPCMBuffer) {
        guard let p = b.floatChannelData?[0], b.frameLength > 0 else { return }
        var sum: Float = 0
        for i in 0..<Int(b.frameLength) { sum += p[i] * p[i] }
        let rms = sqrt(sum / Float(b.frameLength))
        let db = 20 * log10(max(rms, 1e-6))
        levelDB = max(db, levelDB - 3)  // 下降慢一点，菜单里好看
    }

    static func copy(_ src: AVAudioPCMBuffer) -> AVAudioPCMBuffer? {
        guard src.frameLength > 0,
              let dst = AVAudioPCMBuffer(pcmFormat: src.format, frameCapacity: src.frameLength) else { return nil }
        dst.frameLength = src.frameLength
        let s = UnsafeMutableAudioBufferListPointer(src.mutableAudioBufferList)
        let d = UnsafeMutableAudioBufferListPointer(dst.mutableAudioBufferList)
        for i in 0..<min(s.count, d.count) {
            guard let sp = s[i].mData, let dp = d[i].mData else { continue }
            memcpy(dp, sp, Int(min(s[i].mDataByteSize, d[i].mDataByteSize)))
        }
        return dst
    }
}

enum Log {
    static let url = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Logs/Tingji.log")
    private static let q = DispatchQueue(label: "tingji.log")
    static func write(_ s: String) {
        let line = "\(ISO8601DateFormatter().string(from: Date())) \(s)\n"
        q.async {
            if let h = try? FileHandle(forWritingTo: url) {
                h.seekToEndOfFile(); h.write(line.data(using: .utf8)!); try? h.close()
            } else {
                try? line.data(using: .utf8)!.write(to: url)
            }
        }
    }
}
