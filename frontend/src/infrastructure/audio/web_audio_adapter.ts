/**
 * Web Audio API Circular Ring Buffer & VAD Barge-In Adapter (Frontend Infrastructure Layer).
 * Implements AudioEnginePort using injectable Web Audio / MediaDevices primitives.
 */

import type {
  AudioCaptureCallbacks,
  AudioEnginePort,
  AudioHardwareStatus,
} from "../../application/ports/outbound";
import {
  computeBargeInPlaybackOffsetMs,
  computeRmsEnergy,
  DEFAULT_BARGE_IN_VAD_ONSET_MS,
  DEFAULT_SAMPLE_RATE_HZ,
  PcmRingBuffer,
  VoiceActivityHysteresisTracker,
} from "../../domain/interview/audio_math";

export interface MinimalAudioContextLike {
  readonly sampleRate: number;
  readonly currentTime: number;
  resume(): Promise<void>;
  close(): Promise<void>;
}

export interface MinimalMediaStreamLike {
  getTracks(): readonly { stop(): void }[];
}

export interface WebAudioDriverConfig {
  readonly sampleRateHz?: number;
  readonly ringBufferCapacitySeconds?: number;
  readonly vadRmsThreshold?: number;
  readonly bargeInOnsetMs?: number;
  readonly audioContextFactory?: () => MinimalAudioContextLike;
  readonly requestUserMediaFn?: () => Promise<MinimalMediaStreamLike>;
}

export class WebAudioRingBufferAdapter implements AudioEnginePort {
  private readonly sampleRateHz: number;
  private readonly ringBuffer: PcmRingBuffer;
  private readonly vadTracker: VoiceActivityHysteresisTracker;
  private readonly audioContextFactory?: () => MinimalAudioContextLike;
  private readonly requestUserMediaFn?: () => Promise<MinimalMediaStreamLike>;

  private audioCtx: MinimalAudioContextLike | null = null;
  private mediaStream: MinimalMediaStreamLike | null = null;
  private captureCallbacks: AudioCaptureCallbacks | null = null;
  private aiSpeaking = false;
  private playbackTotalDurationMs = 0;
  private playbackAnchorSeconds: number | null = null;
  private lastRmsEnergy = 0;

  public constructor(config: WebAudioDriverConfig = {}) {
    this.sampleRateHz = config.sampleRateHz ?? DEFAULT_SAMPLE_RATE_HZ;
    const capacitySamples = Math.max(
      this.sampleRateHz,
      Math.round((config.ringBufferCapacitySeconds ?? 8) * this.sampleRateHz),
    );
    this.ringBuffer = new PcmRingBuffer(capacitySamples, this.sampleRateHz);
    this.vadTracker = new VoiceActivityHysteresisTracker(
      config.vadRmsThreshold ?? 0.035,
      config.bargeInOnsetMs ?? DEFAULT_BARGE_IN_VAD_ONSET_MS,
    );
    if (config.audioContextFactory !== undefined) {
      this.audioContextFactory = config.audioContextFactory;
    }
    if (config.requestUserMediaFn !== undefined) {
      this.requestUserMediaFn = config.requestUserMediaFn;
    }
  }

  public get unreadRingSamples(): number {
    return this.ringBuffer.unreadSamples;
  }

  public get latestRmsEnergy(): number {
    return this.lastRmsEnergy;
  }

  public async requestMicrophone(): Promise<AudioHardwareStatus> {
    if (this.requestUserMediaFn !== undefined) {
      this.mediaStream = await this.requestUserMediaFn();
    } else if (
      typeof globalThis.navigator !== "undefined" &&
      globalThis.navigator.mediaDevices !== undefined &&
      typeof globalThis.navigator.mediaDevices.getUserMedia === "function"
    ) {
      this.mediaStream = await globalThis.navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
    }

    if (this.audioContextFactory !== undefined) {
      this.audioCtx = this.audioContextFactory();
      await this.audioCtx.resume();
    } else if (typeof globalThis.AudioContext !== "undefined") {
      const ctx = new globalThis.AudioContext({ sampleRate: this.sampleRateHz });
      await ctx.resume();
      this.audioCtx = ctx as unknown as MinimalAudioContextLike;
    }

    return {
      microphoneGranted: true,
      sampleRateHz: this.audioCtx?.sampleRate ?? this.sampleRateHz,
      echoCancellationEnabled: true,
    };
  }

  public startCapture(callbacks: AudioCaptureCallbacks): void {
    this.captureCallbacks = callbacks;
    this.vadTracker.reset();
  }

  public stopCapture(): void {
    this.captureCallbacks = null;
    this.vadTracker.reset();
    if (this.mediaStream !== null) {
      for (const track of this.mediaStream.getTracks()) {
        track.stop();
      }
      this.mediaStream = null;
    }
  }

  public setAiSpeakingState(speaking: boolean): void {
    this.aiSpeaking = speaking;
    if (!speaking) {
      this.playbackAnchorSeconds = null;
      this.playbackTotalDurationMs = 0;
    }
  }

  public enqueueTtsChunk(pcmSamples: Float32Array, durationMs: number): void {
    if (this.playbackAnchorSeconds === null) {
      this.playbackAnchorSeconds = this.audioCtx?.currentTime ?? 0;
    }
    this.playbackTotalDurationMs += Math.max(0, durationMs);
    this.ringBuffer.enqueue(pcmSamples);
  }

  public getCurrentPlaybackOffsetMs(): number {
    if (this.playbackAnchorSeconds === null) {
      return 0;
    }
    const nowSec = this.audioCtx?.currentTime ?? this.playbackAnchorSeconds;
    return computeBargeInPlaybackOffsetMs({
      totalDurationMs: this.playbackTotalDurationMs,
      anchorTimeSeconds: this.playbackAnchorSeconds,
      interruptTimeSeconds: nowSec,
    });
  }

  public flushPlayback(): number {
    const playedMs = this.getCurrentPlaybackOffsetMs();
    this.ringBuffer.flushImmediate();
    this.aiSpeaking = false;
    this.playbackAnchorSeconds = null;
    this.playbackTotalDurationMs = 0;
    return playedMs;
  }

  /**
   * Process one captured microphone PCM frame from AudioWorklet (or Vitest harness),
   * triggering local VAD onset & barge-in when AI playback is active.
   */
  public processMicrophoneFrame(samples: Float32Array, frameDurationMs = 20): void {
    const rms = computeRmsEnergy(samples);
    this.lastRmsEnergy = rms;
    const prevAccumulated = this.vadTracker.accumulatedVoicedMs;
    const sustained = this.vadTracker.pushFrame(rms, frameDurationMs);

    this.captureCallbacks?.onFrameCaptured(samples, rms);

    if (rms >= this.vadTracker.rmsThreshold && prevAccumulated === 0) {
      this.captureCallbacks?.onVadSpeechStart();
    } else if (rms < this.vadTracker.rmsThreshold && prevAccumulated > 0) {
      this.captureCallbacks?.onVadSpeechEnd();
    }

    if (this.aiSpeaking && sustained) {
      const playedMs = this.flushPlayback();
      this.captureCallbacks?.onBargeInDetected(playedMs);
    }
  }
}
