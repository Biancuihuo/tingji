import AVFoundation
import CoreAudio

/// 麦克风：默认跟系统的默认输入走，settings.json 里 mic_device_uid 可以指定某一个
final class MicCapture {
    let writer: ChunkWriter
    var deviceUID: String?
    private var engine: AVAudioEngine?
    private(set) var running = false
    private(set) var deviceName = "—"
    var lastError: String?
    private var observer: NSObjectProtocol?
    private var requesting = false
    var retryAt = Date.distantPast
    var muted = false  // 电脑在放声音时为 true：麦克风开着，但数据不写

    init(writer: ChunkWriter, deviceUID: String?) {
        self.writer = writer
        self.deviceUID = deviceUID
    }

    func start() {
        guard !running, !requesting else { return }
        switch AVCaptureDevice.authorizationStatus(for: .audio) {
        case .notDetermined:
            requesting = true
            AVCaptureDevice.requestAccess(for: .audio) { ok in
                DispatchQueue.main.async {
                    self.requesting = false
                    ok ? self.start() : self.fail("没有麦克风权限")
                }
            }
            return
        case .denied, .restricted:
            fail("没有麦克风权限（系统设置 › 隐私与安全性 › 麦克风 里打开「听记」）")
            return
        default: break
        }
        let e = AVAudioEngine()
        let input = e.inputNode
        let devs = CA.devices()
        let dev = deviceUID.flatMap { uid in devs.first { $0.uid == uid } }
            ?? devs.first { $0.id == CA.defaultDevice(input: true) }
        if let dev, deviceUID != nil, let au = input.audioUnit {
            var id = dev.id
            AudioUnitSetProperty(au, kAudioOutputUnitProperty_CurrentDevice, kAudioUnitScope_Global, 0, &id, UInt32(MemoryLayout<AudioObjectID>.size))
        }
        deviceName = dev?.name ?? "默认输入"
        let fmt = input.outputFormat(forBus: 0)
        guard fmt.sampleRate > 0, fmt.channelCount > 0 else { fail("输入设备没有声道"); return }
        let writer = self.writer
        input.installTap(onBus: 0, bufferSize: 4096, format: fmt) { [weak self] buf, _ in
            if self?.muted == false { writer.append(buf) }
        }
        do {
            try e.start()
        } catch {
            input.removeTap(onBus: 0)
            fail("麦克风启动失败：\(error.localizedDescription)")
            return
        }
        engine = e
        running = true
        lastError = nil
        observer = NotificationCenter.default.addObserver(forName: .AVAudioEngineConfigurationChange, object: e, queue: .main) { [weak self] _ in
            Log.write("[mic] 设备变了，重启")
            self?.stop()
            DispatchQueue.main.asyncAfter(deadline: .now() + 1) { self?.start() }
        }
        Log.write("[mic] 开始录：\(deviceName) \(Int(fmt.sampleRate))Hz")
    }

    func stop() {
        if let o = observer { NotificationCenter.default.removeObserver(o); observer = nil }
        guard let e = engine else { running = false; return }
        e.inputNode.removeTap(onBus: 0)
        e.stop()
        engine = nil
        running = false
        writer.flushIfIdle()
        Log.write("[mic] 停止")
    }

    private func fail(_ s: String) {
        if lastError != s { Log.write("[mic] \(s)") }
        lastError = s
        running = false
        retryAt = Date().addingTimeInterval(30)
    }
}
