import AppKit
import AVFoundation

let home = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/Tingji")
let spool = home.appendingPathComponent("spool")

// 电脑在放声音时麦克风先停（抖音、视频的声音别录进来）。这些进程出声不算：
// 通话软件（打电话要接着录）、语音输入、远程控制、系统自己的提示音和语音服务
let notMedia = [
    "com.tencent.xinWeChat", "com.tencent.WeWorkMac", "com.tencent.meeting", "com.electron.lark", "com.bytedance.lark",
    "com.bytedance.feishu", "com.bytedance.awemeim", "now.typeless.",
    "com.alibaba.DingTalkMac", "us.zoom.xos", "com.apple.FaceTime", "com.apple.avconferenced", "com.apple.TelephonyUtilities",
    "com.microsoft.teams", "cn.shandianshuo.", "com.oray.", "com.teamviewer.", "systemsoundserverd",
    "com.apple.CoreSpeech", "com.apple.accessibility.", "com.apple.Siri", "com.apple.assistantd", "com.apple.cmio.",
    "com.apple.audiomxd", "com.apple.mediaremoted", "com.apple.universalaccessd", "com.apple.controlcenter", "com.apple.cloudpaird",
    "com.biancuihuo.tingji",
]
// 微信里的视频播放器、小程序容器算放视频
let mediaInsideCallApps = ["com.tencent.xinWeChat.xplayer", "com.tencent.flue.WeChatAppEx"]

func isMedia(_ bundle: String) -> Bool {
    if bundle.isEmpty { return false }
    if mediaInsideCallApps.contains(where: { bundle.hasPrefix($0) }) { return true }
    return !notMedia.contains { bundle.hasPrefix($0) }
}

func micUID() -> String? {
    guard let d = try? Data(contentsOf: home.appendingPathComponent("settings.json")),
          let j = try? JSONSerialization.jsonObject(with: d) as? [String: Any],
          let v = j["mic_device_uid"] as? String, !v.isEmpty else { return nil }
    return v
}

// 命令行：--probe 看设备和现在谁在出声，不启动界面
if CommandLine.arguments.contains("--probe") {
    let def = CA.defaultDevice(input: true)
    for d in CA.devices() where d.inputs > 0 { print(d.id == def ? "*" : " ", d.uid, "|", d.name) }
    for p in CA.processes() where p.playing { print("出声中", p.pid, p.bundle, isMedia(p.bundle) ? "→ 算放视频，麦克风会停" : "→ 不算") }
    print("麦克风权限", AVCaptureDevice.authorizationStatus(for: .audio).rawValue)
    exit(0)
}

final class App: NSObject, NSApplicationDelegate, NSMenuDelegate {
    lazy var writer = ChunkWriter(source: "mic", root: spool)
    lazy var mic = MicCapture(writer: writer, deviceUID: micUID())
    var item: NSStatusItem!
    var enabled = true
    var mediaApps: [String] = []
    var mediaStoppedAt: Date?

    func applicationDidFinishLaunching(_ n: Notification) {
        if NSRunningApplication.runningApplications(withBundleIdentifier: Bundle.main.bundleIdentifier ?? "").count > 1 {
            NSApp.terminate(nil)
        }
        try? FileManager.default.createDirectory(at: spool, withIntermediateDirectories: true)
        loadState()
        item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        let menu = NSMenu()
        menu.delegate = self
        item.menu = menu
        Log.write("启动")
        tick()
        Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in self?.tick() }
    }

    func applicationWillTerminate(_ n: Notification) {
        mic.stop()
        writer.stop()
    }

    func tick() {
        // 有放视频的软件在出声就停；停声 2 秒后再接着录
        let playing = CA.processes().filter { $0.playing && $0.pid != getpid() && isMedia($0.bundle) }.map { $0.bundle }
        let wasPaused = !mediaApps.isEmpty
        if !playing.isEmpty {
            if !wasPaused { Log.write("[mic] 电脑在放声音，暂停：\(Set(playing).sorted().joined(separator: ", "))") }
            mediaApps = Array(Set(playing)).sorted()
            mediaStoppedAt = nil
        } else if wasPaused {
            if mediaStoppedAt == nil { mediaStoppedAt = Date() }
            if Date().timeIntervalSince(mediaStoppedAt!) >= 2 {
                mediaApps = []
                mediaStoppedAt = nil
                Log.write("[mic] 声音停了，接着录")
            }
        }
        mic.muted = !mediaApps.isEmpty

        if enabled {
            if !mic.running && mic.lastError == nil { mic.start() }
            if mic.lastError != nil && Date() > mic.retryAt { mic.lastError = nil; mic.start() }  // 权限给了之后能自己接上
        } else if mic.running {
            mic.stop()
        }
        writer.flushIfIdle()
        updateIcon()
        writeStatus()
    }

    var capturing: Bool { enabled && mic.running && !mic.muted }

    func updateIcon() {
        guard let b = item?.button else { return }
        let name: String
        if !enabled { name = "mic.slash" }
        else if mic.lastError != nil { name = "exclamationmark.circle" }
        else if mic.muted { name = "speaker.wave.2" }
        else { name = "waveform.circle.fill" }
        let img = NSImage(systemSymbolName: name, accessibilityDescription: "听记")
        img?.isTemplate = true
        b.image = img
        b.toolTip = "听记"
    }

    func menuNeedsUpdate(_ menu: NSMenu) {
        menu.removeAllItems()
        func info(_ s: String) { let i = NSMenuItem(title: s, action: nil, keyEquivalent: ""); i.isEnabled = false; menu.addItem(i) }
        func act(_ s: String, _ sel: Selector) { let i = NSMenuItem(title: s, action: sel, keyEquivalent: ""); i.target = self; menu.addItem(i) }
        if !enabled { info("录音已关闭") }
        else if let e = mic.lastError { info(e) }
        else if mic.muted { info("电脑在放声音，先停着（\(mediaApps.map(short).joined(separator: "、"))）") }
        else if mic.running { info("录音中：\(mic.deviceName)") }
        else { info("正在打开麦克风…") }
        if let pj = try? JSONSerialization.jsonObject(with: Data(contentsOf: home.appendingPathComponent("progress.json"))) as? [String: Any] {
            info("已整理至 \(pj["processed_until"] as? String ?? "—") · 今天 \(pj["today_events"] as? Int ?? 0) 件事")
        }
        menu.addItem(.separator())
        act(enabled ? "关闭录音" : "开启录音", #selector(toggle))
        act("打开时间线", #selector(openTimeline))
        menu.addItem(.separator())
        act("退出听记", #selector(quit))
    }

    func short(_ bundle: String) -> String {
        let names = ["com.google.Chrome": "Chrome", "com.apple.Safari": "Safari", "com.apple.WebKit": "Safari", "com.bytedance.douyin": "抖音",
                     "com.colliderli.iina": "IINA", "com.apple.Music": "音乐", "com.spotify": "Spotify", "com.tencent.xinWeChat": "微信视频",
                     "com.tencent.flue": "微信小程序", "com.xhs": "小红书"]
        return names.first { bundle.hasPrefix($0.key) }?.value ?? bundle
    }

    @objc func toggle() {
        enabled.toggle()
        Log.write(enabled ? "开启录音" : "关闭录音")
        saveState()
        tick()
    }
    @objc func openTimeline() { NSWorkspace.shared.open(URL(string: "http://127.0.0.1:7788/")!) }
    @objc func quit() { NSApp.terminate(nil) }

    func loadState() {
        guard let d = try? Data(contentsOf: home.appendingPathComponent("state.json")),
              let j = try? JSONSerialization.jsonObject(with: d) as? [String: Any] else { return }
        if let v = j["enabled"] as? Bool { enabled = v }
    }

    func saveState() {
        try? JSONSerialization.data(withJSONObject: ["enabled": enabled]).write(to: home.appendingPathComponent("state.json"))
    }

    func writeStatus() {
        let j: [String: Any] = [
            "ts": Int64(Date().timeIntervalSince1970 * 1000),
            "enabled": enabled,
            "capturing": capturing,
            "paused_for_media": mediaApps,
            "mic": ["running": mic.running, "device": mic.deviceName, "error": (mic.lastError ?? NSNull()) as Any],
        ]
        if let d = try? JSONSerialization.data(withJSONObject: j, options: [.prettyPrinted]) {
            try? d.write(to: home.appendingPathComponent("status.json"), options: .atomic)
        }
    }
}

let app = NSApplication.shared
let delegate = App()
app.delegate = delegate
app.setActivationPolicy(.accessory)
app.run()
