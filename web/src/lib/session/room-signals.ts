'use client'

import {
  ConnectionState,
  ParticipantKind,
  Room,
  RoomEvent,
  Track,
} from 'livekit-client'
import type { Participant, RemoteTrack, RemoteTrackPublication } from 'livekit-client'
import type { SessionInput } from '@/lib/session/events'
import type { Emit } from '@/lib/session/source'

/**
 * Real audio, from the room the agent is in.
 *
 * This replaces the honest placeholder the surface has been showing: the event
 * stream carries turns, so until now "who is speaking" was a turn-level
 * inference and there were no levels at all. Here they are measured off the
 * actual tracks.
 *
 * Three things it deliberately does not do:
 *
 *   - it never publishes. The token it is handed cannot (`canPublish: false`),
 *     and nothing here asks for a microphone. The surface watches a call; it is
 *     not a participant in one.
 *   - it never plays the audio. `Room.startAudio` is not called, so nothing is
 *     rendered to speakers - the audio is already in the room, and a demo
 *     laptop echoing the agent back through its own speakers on stage is a
 *     failure mode nobody needs. Levels come from the analyser, not playback.
 *   - it never becomes the source of truth for a turn. Guardrail decisions,
 *     transcripts and timings all still come from the events bridge. This adds
 *     amplitude and who-is-talking, and nothing else.
 *
 * If the room cannot be joined, the caller keeps the placeholder. An absent
 * waveform reads as "no audio track attached"; a flat one reads as silence, and
 * those are different claims.
 */

/** How often levels are sampled. 20/s is smooth enough and cheap. */
const SAMPLE_MS = 50

/** Below this, a participant is not speaking. LiveKit's own default threshold. */
const SPEAKING_FLOOR = 0.05

export interface RoomGrant {
  url: string
  token: string
  room: string
}

export interface RoomHandle {
  disconnect: () => Promise<void>
}

/**
 * The agent, as opposed to the buyer.
 *
 * `ParticipantKind.AGENT` is what LiveKit sets for an agent worker, and the
 * identity prefix is the fallback for a deployment that predates it. Getting
 * this wrong swaps the two indicators on screen, so it is one function rather
 * than a condition repeated at three call sites.
 */
function isAgent(participant: Participant): boolean {
  return (
    participant.kind === ParticipantKind.AGENT ||
    participant.identity.startsWith('agent-')
  )
}

export async function joinRoom(grant: RoomGrant, emit: Emit): Promise<RoomHandle> {
  const room = new Room()
  const analysers = new Map<string, { analyser: AnalyserNode; agent: boolean }>()
  let audioContext: AudioContext | null = null
  let timer: ReturnType<typeof setInterval> | null = null

  const connection = (state: ConnectionState): SessionInput | null => {
    switch (state) {
      case ConnectionState.Connected:
        return { signal: 'connection', state: 'live' }
      case ConnectionState.Connecting:
        return { signal: 'connection', state: 'connecting' }
      case ConnectionState.Reconnecting:
        // The framework reconnects on its own (docs/04-: transport is its job),
        // so this is "the link dropped", not "the call ended".
        return { signal: 'connection', state: 'lost' }
      case ConnectionState.Disconnected:
        return { signal: 'connection', state: 'ended' }
      default:
        return null
    }
  }

  room.on(RoomEvent.ConnectionStateChanged, (state) => {
    const signal = connection(state)
    if (signal !== null) emit(signal)
  })

  room.on(RoomEvent.ActiveSpeakersChanged, (speakers: Participant[]) => {
    const agentSpeaking = speakers.some(isAgent)
    const buyerSpeaking = speakers.some((speaker) => !isAgent(speaker))
    // Order matters: the reducer reads a buyer signal arriving while the agent
    // is still speaking as barge-in, so the agent's state is settled first.
    emit({ signal: 'agent_speaking', on: agentSpeaking })
    emit({ signal: 'buyer_speaking', on: buyerSpeaking })
  })

  room.on(
    RoomEvent.TrackSubscribed,
    (track: RemoteTrack, _publication: RemoteTrackPublication, participant: Participant) => {
      if (track.kind !== Track.Kind.Audio) return
      const stream = track.mediaStream
      if (stream === undefined) return

      audioContext ??= new AudioContext()
      const analyser = audioContext.createAnalyser()
      analyser.fftSize = 512
      audioContext.createMediaStreamSource(stream).connect(analyser)
      // Deliberately NOT connected to `audioContext.destination`: measuring the
      // signal must not also play it out of the demo laptop.
      analysers.set(track.sid ?? participant.identity, {
        analyser,
        agent: isAgent(participant),
      })
    },
  )

  room.on(RoomEvent.TrackUnsubscribed, (track: RemoteTrack) => {
    analysers.delete(track.sid ?? '')
  })

  await room.connect(grant.url, grant.token, { autoSubscribe: true })

  timer = setInterval(() => {
    if (analysers.size === 0) return
    let peak = 0
    for (const { analyser } of analysers.values()) {
      peak = Math.max(peak, rms(analyser))
    }
    emit({ signal: 'level', value: peak })
  }, SAMPLE_MS)

  return {
    async disconnect() {
      if (timer !== null) clearInterval(timer)
      timer = null
      analysers.clear()
      await room.disconnect()
      await audioContext?.close()
      audioContext = null
    },
  }
}

/**
 * Root mean square of the current window, normalised to roughly 0-1.
 *
 * RMS rather than peak: peak jumps on a single sample and makes the trace look
 * like noise, while RMS is what a level meter shows. The floor keeps a silent
 * track drawing nothing rather than drawing the room's noise floor as speech.
 */
function rms(analyser: AnalyserNode): number {
  const samples = new Float32Array(analyser.fftSize)
  analyser.getFloatTimeDomainData(samples)
  let sum = 0
  for (const sample of samples) sum += sample * sample
  const value = Math.sqrt(sum / samples.length)
  return value < SPEAKING_FLOOR / 4 ? 0 : Math.min(1, value * 4)
}
