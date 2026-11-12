/**
 * Alert sound.
 *
 * Synthesised with the Web Audio API so the app ships no binary assets and
 * works fully offline. The context is created lazily on the first user gesture
 * (browsers block autoplay otherwise).
 */

let context: AudioContext | null = null;

type AudioContextCtor = typeof AudioContext;

function getContext(): AudioContext | null {
  if (typeof window === "undefined") return null;
  const Ctor: AudioContextCtor | undefined =
    window.AudioContext ?? (window as unknown as { webkitAudioContext?: AudioContextCtor }).webkitAudioContext;
  if (!Ctor) return null;
  if (!context) {
    try {
      context = new Ctor();
    } catch {
      return null;
    }
  }
  if (context.state === "suspended") {
    void context.resume().catch(() => undefined);
  }
  return context;
}

/** Call once from a click handler so later alerts can play without a gesture. */
export function primeAudio(): void {
  getContext();
}

export type AlertSound = "critical" | "warning" | "info";

const PATTERNS: Record<AlertSound, { notes: number[]; gain: number }> = {
  // low, urgent double-beep
  critical: { notes: [880, 0, 880, 0, 660], gain: 0.32 },
  // two-tone chime
  warning: { notes: [660, 880], gain: 0.24 },
  // single soft blip
  info: { notes: [520], gain: 0.16 },
};

export function playAlertSound(severity: string, volume = 0.6): void {
  const audio = getContext();
  if (!audio) return;
  const pattern = PATTERNS[(severity as AlertSound) in PATTERNS ? (severity as AlertSound) : "info"];
  const gain = audio.createGain();
  gain.gain.value = Math.max(0, Math.min(1, volume)) * pattern.gain;
  gain.connect(audio.destination);

  let time = audio.currentTime;
  for (const frequency of pattern.notes) {
    const duration = frequency === 0 ? 0.06 : 0.14;
    if (frequency > 0) {
      const oscillator = audio.createOscillator();
      oscillator.type = "square";
      oscillator.frequency.value = frequency;
      const envelope = audio.createGain();
      envelope.gain.setValueAtTime(0.0001, time);
      envelope.gain.exponentialRampToValueAtTime(1, time + 0.012);
      envelope.gain.exponentialRampToValueAtTime(0.0001, time + duration);
      oscillator.connect(envelope);
      envelope.connect(gain);
      oscillator.start(time);
      oscillator.stop(time + duration + 0.02);
    }
    time += duration + 0.02;
  }
}

export function notifyDesktop(title: string, body: string): void {
  if (typeof window === "undefined" || !("Notification" in window)) return;
  if (Notification.permission !== "granted") return;
  try {
    new Notification(title, { body, dir: "rtl", lang: "fa" });
  } catch {
    /* notifications are best-effort */
  }
}

export async function requestNotificationPermission(): Promise<boolean> {
  if (typeof window === "undefined" || !("Notification" in window)) return false;
  if (Notification.permission === "granted") return true;
  if (Notification.permission === "denied") return false;
  const result = await Notification.requestPermission().catch(() => "denied");
  return result === "granted";
}
