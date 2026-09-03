'use client'

import {
  ConnectionState,
  DefaultReconnectPolicy,
  DisconnectReason,
  Room,
  RoomEvent,
  Track,
} from 'livekit-client'
import type { Participant, RemoteTrack } from 'livekit-client'
import { isAgent } from '@/lib/session/room-signals'
import { levelMeter } from '@/lib/talk/levels'
import type { Levels } from '@/lib/talk/levels'

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

/**
 * Which utterance a stream belongs to, which is not the same as which stream it
 * is: the visitor's side opens a new stream per update within one segment.
 */
const SEGMENT_ID_ATTRIBUTE = 'lk.segment_id'

/** Carried on the closing header, as the string "true". */
const TRANSCRIPTION_FINAL_ATTRIBUTE = 'lk.transcription_final'

export type TalkPhase = 'connecting' | 'live' | 'reconnecting' | 'ended'

/**
 * Why a call stopped, in the visitor's terms.
 *
 * A call that the ambassador finished politely and a call whose network died
 * look identical from inside `ConnectionState`, and telling a visitor the wrong
 * one is a lie in either direction: "the ambassador ended the call" when the
 * signal dropped, or "connection lost" after a farewell they just heard. The
 * only thing that separates them is `DisconnectReason`, so it is read.
 */
export type TalkEndingKind = 'ended' | 'lost' | 'taken_over' | 'failed'

export interface TalkEnding {
  kind: TalkEndingKind
  /** Shown to the visitor as-is. */
  message: string
}

/**
 * The closed mapping, and where an unrecognised reason falls.
 *
 * Deliberate ends are the ones where the room itself is finished: the agent
 * shut its job down (`ROOM_DELETED`, which is what a graceful goodbye
 * produces), the room's own timeout closed it (`ROOM_CLOSED`), the server
 * removed this participant (`PARTICIPANT_REMOVED`, which is the shape a
 * duration cap takes), or the visitor pressed End call (`CLIENT_INITIATED`).
 *
 * Everything else falls to `lost`, INCLUDING an absent reason. That is the safe
 * direction here: `lost` says the call stopped and offers another one, which is
 * true whatever happened, while `ended` claims the conversation was finished on
 * purpose and would be a fabricated farewell if it were not.
 */
export function endingFor(reason: DisconnectReason | undefined): TalkEnding {
  switch (reason) {
    case DisconnectReason.CLIENT_INITIATED:
      return { kind: 'ended', message: 'Call ended.' }
    case DisconnectReason.ROOM_DELETED:
    case DisconnectReason.ROOM_CLOSED:
      return { kind: 'ended', message: 'The ambassador ended the call.' }
    case DisconnectReason.PARTICIPANT_REMOVED:
      return {
        kind: 'ended',
        message: 'The call ended. Demo calls are limited in length.',
      }
    case DisconnectReason.DUPLICATE_IDENTITY:
      return {
        kind: 'taken_over',
        message: 'This call was picked up in another tab, so it ended here.',
      }
    case DisconnectReason.AGENT_ERROR:
      return {
        kind: 'failed',
        message: 'Something went wrong on our side and the call stopped.',
      }
    default:
      return { kind: 'lost', message: 'The call stopped unexpectedly.' }
  }
}

/**
 * Reconnect attempts, bounded.
 *
 * The library default climbs through ten attempts to its maximum delay. On a
 * room that no longer exists every one of them fails, and the visitor watches
 * "Reconnecting" for the whole climb before being told anything - which is the
 * worst of both, since the call was over at the first attempt. Four attempts
 * over about four seconds is enough for a hiccup on a hotel network and short
 * enough that a closed room resolves to an answer quickly.
 */
const RECONNECT_DELAYS_MS = [0, 300, 1_200, 2_700]

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
  /**
   * The call is over, and why. Fires exactly once per session, whoever ended
   * it - including the visitor - so the page has one place to settle into.
   */
  onEnded: (ending: TalkEnding) => void
  /**
   * How loud each side is, twenty times a second, so the orb can follow a voice
   * rather than a state change. Optional: a caller that shows no orb - a test,
   * or a future surface - pays nothing for the analyser.
   */
  onLevels?: (levels: Levels) => void
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
  // The agent's audio, played. A remote audio track with no sink attached is
  // not decoded at all, so this is what makes the call audible rather than
  // merely connected.
  const sinks = new Map<string, HTMLAudioElement>()
  // Text so far per SEGMENT, so an agent segment spread over several writes
  // keeps accumulating across them.
  const segments = new Map<string, string>()
  const meter = events.onLevels ? levelMeter(events.onLevels) : null

  const room = new Room({
    // The framework's own defaults for a speech call. Named rather than
    // inherited so that a future change to the library's defaults is a change
    // to this file, not a silent change to how the demo sounds.
    adaptiveStream: true,
    dynacast: true,
    reconnectPolicy: new DefaultReconnectPolicy(RECONNECT_DELAYS_MS),
  })

  // Fires exactly once, whoever ended the call. Everything that tears the
  // session down runs here rather than in the End-call button, because the
  // common case is the OTHER side hanging up: the agent finishes, the room
  // closes, and this page is told about it after the fact.
  let finished = false
  const finish = (reason: DisconnectReason | undefined) => {
    if (finished) return
    finished = true
    const ending = endingFor(reason)
    room.unregisterTextStreamHandler(TRANSCRIPTION_TOPIC)
    for (const element of sinks.values()) {
      element.srcObject = null
      element.remove()
    }
    sinks.clear()
    segments.clear()
    meter?.stop()
    // Releases the microphone. The browser's own recording indicator stays lit
    // until the local track is actually stopped, and a visitor whose call is
    // over should not be left looking at one. Idempotent, and already
    // disconnected in the server-initiated case, so failure here is not news.
    void room.disconnect().catch(() => {})
    events.onPhase('ended')
    events.onEnded(ending)
  }

  room.on(RoomEvent.Disconnected, (reason?: DisconnectReason) => {
    finish(reason)
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
      // Disconnected is deliberately NOT handled here: this event knows the
      // state but not the reason, and the reason is the whole point.
      default:
        break
    }
  })

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

    // The same track the element is playing, measured. `isAgent` decides the
    // side rather than the identity comparison used for the transcript,
    // because a track arrives with a participant and a transcript with an
    // identity string.
    const media = track.mediaStreamTrack
    if (media !== undefined) {
      meter?.add(track.sid ?? participant.identity, media, isAgent(participant) ? 'agent' : 'visitor')
    }
  })

  room.on(RoomEvent.TrackUnsubscribed, (track: RemoteTrack) => {
    const key = track.sid ?? ''
    meter?.remove(key)
    track.detach().forEach((element) => {
      element.srcObject = null
      element.remove()
    })
    sinks.delete(key)
  })

  /**
   * The transcript, from the framework's text streams.
   *
   * BOTH SIDES PUBLISH, and they publish DIFFERENTLY. Read out of
   * `livekit-agents` 1.7.0's `room_io/_output.py`, where
   * `_ParticipantStreamTranscriptionOutput` branches on `is_delta_stream`:
   *
   *   agent   `is_delta_stream=True`. One writer is REUSED for the segment and
   *           each write is a delta, so the pieces have to be appended.
   *   visitor `is_delta_stream=False`. Every update "always create a new
   *           writer", writes the WHOLE text so far and closes it, and the
   *           flush opens yet another one for the final.
   *
   * That asymmetry is why the rail is keyed on `lk.segment_id` rather than on
   * the stream id. Keyed on the stream, one visitor sentence arrives as a pile
   * of separate lines each a little longer than the last, because each of those
   * new writers is its own stream. Keyed on the segment they land on one line:
   * appended for the agent, replaced for the visitor.
   *
   * The final flag is `lk.transcription_final` and its value is the STRING
   * "true", not a boolean; it rides on the closing header. It is read rather
   * than inferred from the stream ending, because on the visitor's side every
   * interim stream ends too.
   */
  room.registerTextStreamHandler(TRANSCRIPTION_TOPIC, (reader, participantInfo) => {
    const speaker: TalkLine['speaker'] =
      participantInfo.identity === grant.identity ? 'visitor' : 'agent'
    const attributes = reader.info.attributes ?? {}
    // One line per segment. The stream id is the fallback for a stream with no
    // segment id, where one stream is the best guess at one line.
    const id = attributes[SEGMENT_ID_ATTRIBUTE] ?? reader.info.id
    const wasFinal = attributes[TRANSCRIPTION_FINAL_ATTRIBUTE] === 'true'
    const appends = speaker === 'agent'

    void (async () => {
      let text = appends ? (segments.get(id) ?? '') : ''
      try {
        for await (const piece of reader) {
          text += piece
          segments.set(id, text)
          events.onLine({ id, speaker, text, final: false })
        }
      } catch {
        // A truncated segment is still worth showing: the visitor saw the
        // words, and dropping the line would make the rail disagree with what
        // they heard.
      }
      segments.set(id, text)
      events.onLine({ id, speaker, text, final: wasFinal })
    })()
  })

  events.onPhase('connecting')
  await room.connect(grant.url, grant.token, { autoSubscribe: true })

  // The microphone, published after the connection rather than before, so a
  // refused permission cannot leave a half-open room behind. If this throws the
  // caller tears the room down.
  await room.localParticipant.setMicrophoneEnabled(true)

  // The visitor's own level, off the local track. Read after publishing rather
  // than from a getUserMedia stream of our own, so there is one microphone in
  // play and muting it stops the orb reacting to it too.
  //
  // Wrapped, and the direction is deliberate: the orb is how the call LOOKS and
  // the call is the thing. A meter that cannot attach costs the visitor's half
  // of the bloom - the orb reads "listening" while they speak - and must never
  // cost a working conversation, which is what throwing here would do one line
  // after the microphone went live.
  try {
    for (const publication of room.localParticipant.audioTrackPublications.values()) {
      const media = publication.track?.mediaStreamTrack
      if (media !== undefined) meter?.add(publication.trackSid, media, 'visitor')
    }
  } catch {
    // Measured nothing; the call is up.
  }

  return {
    async setMuted(muted: boolean) {
      await room.localParticipant.setMicrophoneEnabled(!muted)
    },
    async end() {
      // The visitor's own End call goes through the same path as the agent's,
      // so there is one teardown rather than two that drift. The library will
      // also emit Disconnected here; `finish` is once-only, so that is a no-op.
      finish(DisconnectReason.CLIENT_INITIATED)
      await room.disconnect().catch(() => {})
    },
  }
}

export { TRANSCRIPTION_TOPIC, SEGMENT_ID_ATTRIBUTE, TRANSCRIPTION_FINAL_ATTRIBUTE, isAgent }
