'use client'

import { ConnectionState, ParticipantKind, Room, RoomEvent, Track } from 'livekit-client'
import type { Participant, RemoteTrack, RemoteTrackPublication } from 'livekit-client'
import { rms as rawRms } from '@/lib/audio/level'
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
export function isAgent(participant: Pick<Participant, 'kind' | 'identity'>): boolean {
  return (
    participant.kind === ParticipantKind.AGENT ||
    participant.identity.startsWith('agent-')
  )
}

export async function joinRoom(grant: RoomGrant, emit: Emit): Promise<RoomHandle> {
  const room = new Room()
  const analysers = new Map<
    string,
    { analyser: AnalyserNode; agent: boolean; sink: HTMLAudioElement }
  >()
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

  room.on(
    RoomEvent.TrackSubscribed,
    (track: RemoteTrack, _publication: RemoteTrackPublication, participant: Participant) => {
      if (track.kind !== Track.Kind.Audio) return
      // Built from the track rather than read off `track.mediaStream`, which
      // is not populated for a remote track: reading it gave an analyser that
      // was never created, so the waveform sat at its floor while the call was
      // audible - silence and absence rendering the same, which is the one
      // thing this panel must not do.
      const mediaTrack = track.mediaStreamTrack
      if (mediaTrack === undefined) return
      const stream = new MediaStream([mediaTrack])

      audioContext ??= new AudioContext()
      // A context created outside a gesture starts suspended, and a suspended
      // analyser returns silence rather than an error - the waveform would sit
      // flat while the call was audible, which is the exact lie the "no audio
      // track" label exists to avoid. Attaching is a click, so this normally
      // does nothing; it is here because the failure mode is invisible.
      if (audioContext.state === 'suspended') void audioContext.resume()

      // A remote WebRTC audio track with no sink attached is not decoded, so
      // the analyser reads silence off a track that is carrying speech. The
      // sink has to exist. It is MUTED rather than absent: the demo laptop
      // must not echo the agent back into the room it is standing in, and
      // muting stops the speakers without stopping the pipeline.
      const sink = new Audio()
      sink.muted = true
      sink.autoplay = true
      sink.srcObject = stream
      void sink.play().catch(() => {
        // Autoplay refused. The analyser still gets data from the graph below;
        // this is a best effort, not a dependency.
      })

      const analyser = audioContext.createAnalyser()
      analyser.fftSize = 512
      audioContext.createMediaStreamSource(stream).connect(analyser)
      // The analyser itself is deliberately NOT connected to
      // `audioContext.destination`: the muted element above is the only sink,
      // so nothing reaches the speakers by either path.
      analysers.set(track.sid ?? participant.identity, {
        analyser,
        agent: isAgent(participant),
        sink,
      })
    },
  )

  room.on(RoomEvent.TrackUnsubscribed, (track: RemoteTrack) => {
    const entry = analysers.get(track.sid ?? '')
    if (entry !== undefined) {
      entry.sink.srcObject = null
      analysers.delete(track.sid ?? '')
    }
  })

  await room.connect(grant.url, grant.token, { autoSubscribe: true })

  // Who is speaking comes from the SAME measurement that draws the waveform,
  // not from `RoomEvent.ActiveSpeakersChanged`. Two sources for one fact is
  // the mistake this file already avoids with the event stream, and it is
  // worse here: the server's detection and the local analyser would disagree
  // in front of the room, with the bars moving and the indicator dark. One
  // number, both readings.
  let agentOn = false
  let buyerOn = false

  timer = setInterval(() => {
    if (analysers.size === 0) return
    let peak = 0
    let agentLevel = 0
    let buyerLevel = 0
    for (const { analyser, agent } of analysers.values()) {
      const level = rms(analyser)
      peak = Math.max(peak, level)
      if (agent) agentLevel = Math.max(agentLevel, level)
      else buyerLevel = Math.max(buyerLevel, level)
    }
    emit({ signal: 'level', value: peak })

    // Only on a change: this runs 20 times a second, and a dispatch per tick
    // per indicator would be three re-renders where nothing moved.
    const nextAgent = agentLevel >= SPEAKING_FLOOR
    const nextBuyer = buyerLevel >= SPEAKING_FLOOR
    // Agent first: the reducer reads a buyer signal arriving while the agent
    // is still speaking as barge-in, so the order of these two is load-bearing.
    if (nextAgent !== agentOn) {
      agentOn = nextAgent
      emit({ signal: 'agent_speaking', on: agentOn })
    }
    if (nextBuyer !== buyerOn) {
      buyerOn = nextBuyer
      emit({ signal: 'buyer_speaking', on: buyerOn })
    }
  }, SAMPLE_MS)

  return {
    async disconnect() {
      if (timer !== null) clearInterval(timer)
      timer = null
      for (const { sink } of analysers.values()) sink.srcObject = null
      analysers.clear()
      await room.disconnect()
      await audioContext?.close()
      audioContext = null
    },
  }
}

/**
 * The watcher's reading of a track's level, 0 to roughly 1.
 *
 * The measurement is shared (`lib/audio/level.ts`); the floor and the scale
 * below are this panel's policy. The floor keeps a silent track drawing nothing
 * rather than drawing the room's noise floor as speech.
 */
function rms(analyser: AnalyserNode): number {
  const value = rawRms(analyser)
  return value < SPEAKING_FLOOR / 4 ? 0 : Math.min(1, value * 4)
}
