'use client'

import { ConnectionState, Room, RoomEvent, Track } from 'livekit-client'
import type { Participant, RemoteTrack } from 'livekit-client'
import { isAgent } from '@/lib/session/room-signals'

/**
 * A call the visitor is IN, as opposed to a call the surface is watching.
 *
 * This sits beside `room-signals.ts` rather than inside it, and the three
 * differences are exactly the three things that file documents itself as never
 * doing:
 *
 *   it publishes.  The visitor's microphone is the input to the whole system.
 *   it plays.      The agent's audio goes to the visitor's speakers, because
 *                  there is nobody else in the room to hear it. The laptop
 *                  surface mutes its sink on purpose - a demo laptop echoing
 *                  the agent back into the room it is standing in is a failure
 *                  mode nobody needs - and that reasoning does not apply to a
 *                  client sitting alone at their own desk.
 *   it reads text. The transcript comes from the framework's own transcription
 *                  streams, not from the event bridge, because the bridge is
 *                  loopback-only by design and this page is not on that
 *                  machine (issue #63).
 *
 * Mutating the watcher into a participant would have made both behaviours one
 * flag apart, on a page where the wrong value is a live microphone.
 */

/** The framework's transcription topic, verified in `room_io/types.py`. */
const TRANSCRIPTION_TOPIC = 'lk.transcription'

export type TalkPhase = 'connecting' | 'live' | 'reconnecting' | 'ended'

export interface TalkLine {
  id: string
  /** Whose words these are. The rail labels them; it never guesses. */
  speaker: 'agent' | 'visitor'
  text: string
  /** False while the stream is still arriving, so the rail can show it settling. */
  final: boolean
}

export interface TalkEvents {
  onPhase: (phase: TalkPhase) => void
  onLine: (line: TalkLine) => void
  /** A failure the visitor needs to read, in their words rather than ours. */
  onTrouble: (reason: string) => void
}

export interface TalkGrant {
  url: string
  token: string
  room: string
  identity: string
}

export interface TalkHandle {
  end: () => Promise<void>
  setMuted: (muted: boolean) => Promise<void>
}

export async function startTalking(grant: TalkGrant, events: TalkEvents): Promise<TalkHandle> {
  const room = new Room({
    // The framework's own defaults for a speech call. Named rather than
    // inherited so that a future change to the library's defaults is a change
    // to this file, not a silent change to how the demo sounds.
    adaptiveStream: true,
    dynacast: true,
  })

  room.on(RoomEvent.ConnectionStateChanged, (state) => {
    switch (state) {
      case ConnectionState.Connected:
        events.onPhase('live')
        break
      case ConnectionState.Connecting:
        events.onPhase('connecting')
        break
      case ConnectionState.Reconnecting:
        events.onPhase('reconnecting')
        break
      case ConnectionState.Disconnected:
        events.onPhase('ended')
        break
      default:
        break
    }
  })

  // The agent's audio, played. A remote audio track with no sink attached is
  // not decoded at all, so this is what makes the call audible rather than
  // merely connected.
  const sinks = new Map<string, HTMLAudioElement>()
  room.on(RoomEvent.TrackSubscribed, (track: RemoteTrack, _pub, participant: Participant) => {
    if (track.kind !== Track.Kind.Audio) return
    const element = track.attach()
    element.autoplay = true
    // Not muted, unlike the watcher's sink: this visitor is the only person
    // who can hear the ambassador.
    element.muted = false
    void element.play().catch(() => {
      // Autoplay policies refuse audio that no gesture asked for. Starting a
      // call IS a gesture, so this is normally fine; when it is not, the
      // visitor needs to know the call is up but silent, because silence
      // otherwise reads as a broken demo.
      events.onTrouble(
        'Your browser is holding the audio back. Click anywhere on the page to let it through.',
      )
    })
    sinks.set(track.sid ?? participant.identity, element)
  })

  room.on(RoomEvent.TrackUnsubscribed, (track: RemoteTrack) => {
    const key = track.sid ?? ''
    track.detach().forEach((element) => {
      element.srcObject = null
      element.remove()
    })
    sinks.delete(key)
  })

  /**
   * The transcript, from the framework's text streams.
   *
   * One stream per segment, and BOTH sides publish: the framework builds a user
   * transcription output and an agent transcription output in the same branch,
   * so a rail fed by one of them would show half a conversation.
   *
   * The chunks are DELTAS. The reader class's own docstring says an async
   * iteration "returns the entire string that has been received up to the
   * current point in time", which reads as cumulative - the implementation
   * decodes and yields each chunk's own content, and `readAll` is what
   * concatenates them. Verified by reading `livekit-client`'s source rather
   * than its comment, because appending cumulative chunks would print every
   * word an increasing number of times.
   */
  room.registerTextStreamHandler(TRANSCRIPTION_TOPIC, (reader, participantInfo) => {
    const speaker: TalkLine['speaker'] =
      participantInfo.identity === grant.identity ? 'visitor' : 'agent'
    const id = reader.info.id
    void (async () => {
      let text = ''
      try {
        for await (const delta of reader) {
          text += delta
          events.onLine({ id, speaker, text, final: false })
        }
      } catch {
        // A truncated segment is still worth showing: the visitor saw the
        // words, and dropping the line would make the rail disagree with what
        // they heard.
      }
      events.onLine({ id, speaker, text, final: true })
    })()
  })

  events.onPhase('connecting')
  await room.connect(grant.url, grant.token, { autoSubscribe: true })

  // The microphone, published after the connection rather than before, so a
  // refused permission cannot leave a half-open room behind. If this throws the
  // caller tears the room down.
  await room.localParticipant.setMicrophoneEnabled(true)

  return {
    async setMuted(muted: boolean) {
      await room.localParticipant.setMicrophoneEnabled(!muted)
    },
    async end() {
      room.unregisterTextStreamHandler(TRANSCRIPTION_TOPIC)
      for (const element of sinks.values()) {
        element.srcObject = null
        element.remove()
      }
      sinks.clear()
      await room.disconnect()
      events.onPhase('ended')
    },
  }
}

export { TRANSCRIPTION_TOPIC, isAgent }
