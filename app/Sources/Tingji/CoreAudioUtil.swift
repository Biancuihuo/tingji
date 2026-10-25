import CoreAudio
import Foundation

enum CA {
    static func addr(_ s: AudioObjectPropertySelector, _ scope: AudioObjectPropertyScope = kAudioObjectPropertyScopeGlobal) -> AudioObjectPropertyAddress {
        AudioObjectPropertyAddress(mSelector: s, mScope: scope, mElement: kAudioObjectPropertyElementMain)
    }

    static func ids(_ obj: AudioObjectID, _ sel: AudioObjectPropertySelector, _ scope: AudioObjectPropertyScope = kAudioObjectPropertyScopeGlobal) -> [AudioObjectID] {
        var a = addr(sel, scope)
        var size: UInt32 = 0
        guard AudioObjectGetPropertyDataSize(obj, &a, 0, nil, &size) == noErr, size > 0 else { return [] }
        var out = [AudioObjectID](repeating: 0, count: Int(size) / MemoryLayout<AudioObjectID>.size)
        guard AudioObjectGetPropertyData(obj, &a, 0, nil, &size, &out) == noErr else { return [] }
        return out
    }

    static func string(_ obj: AudioObjectID, _ sel: AudioObjectPropertySelector) -> String? {
        var a = addr(sel)
        var size = UInt32(MemoryLayout<Unmanaged<CFString>?>.size)
        var v: Unmanaged<CFString>? = nil
        guard AudioObjectGetPropertyData(obj, &a, 0, nil, &size, &v) == noErr, let s = v?.takeRetainedValue() else { return nil }
        return s as String
    }

    static func u32(_ obj: AudioObjectID, _ sel: AudioObjectPropertySelector) -> UInt32 {
        var a = addr(sel)
        var size = UInt32(4)
        var v: UInt32 = 0
        AudioObjectGetPropertyData(obj, &a, 0, nil, &size, &v)
        return v
    }

    static func channels(_ dev: AudioObjectID, input: Bool) -> Int {
        var a = addr(kAudioDevicePropertyStreamConfiguration, input ? kAudioObjectPropertyScopeInput : kAudioObjectPropertyScopeOutput)
        var size: UInt32 = 0
        guard AudioObjectGetPropertyDataSize(dev, &a, 0, nil, &size) == noErr, size > 0 else { return 0 }
        let raw = UnsafeMutableRawPointer.allocate(byteCount: Int(size), alignment: 16)
        defer { raw.deallocate() }
        guard AudioObjectGetPropertyData(dev, &a, 0, nil, &size, raw) == noErr else { return 0 }
        let list = UnsafeMutableAudioBufferListPointer(raw.assumingMemoryBound(to: AudioBufferList.self))
        return list.reduce(0) { $0 + Int($1.mNumberChannels) }
    }

    struct Device { let id: AudioObjectID; let uid: String; let name: String; let inputs: Int; let outputs: Int; let transport: UInt32 }

    static func devices() -> [Device] {
        ids(AudioObjectID(kAudioObjectSystemObject), kAudioHardwarePropertyDevices).compactMap { id in
            guard let uid = string(id, kAudioDevicePropertyDeviceUID) else { return nil }
            return Device(id: id, uid: uid, name: string(id, kAudioObjectPropertyName) ?? uid,
                          inputs: channels(id, input: true), outputs: channels(id, input: false),
                          transport: u32(id, kAudioDevicePropertyTransportType))
        }
    }

    static func defaultDevice(input: Bool) -> AudioObjectID {
        var a = addr(input ? kAudioHardwarePropertyDefaultInputDevice : kAudioHardwarePropertyDefaultOutputDevice)
        var size = UInt32(4)
        var id = AudioObjectID(0)
        AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &a, 0, nil, &size, &id)
        return id
    }

    struct Proc { let id: AudioObjectID; let pid: Int32; let bundle: String; let playing: Bool }

    static func processes() -> [Proc] {
        ids(AudioObjectID(kAudioObjectSystemObject), kAudioHardwarePropertyProcessObjectList).map { id in
            Proc(id: id, pid: Int32(bitPattern: u32(id, kAudioProcessPropertyPID)),
                 bundle: string(id, kAudioProcessPropertyBundleID) ?? "",
                 playing: u32(id, kAudioProcessPropertyIsRunningOutput) != 0)
        }
    }
}
