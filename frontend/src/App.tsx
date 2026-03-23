import { useMemo, useState } from 'react'
import {
  LiveKitRoom,
  VideoTrack,
  useTracks,
} from '@livekit/components-react'
import { Track } from 'livekit-client'
import '@livekit/components-styles'
import './App.css'

const defaultUrl = import.meta.env.VITE_LIVEKIT_URL ?? ''

function RemoteCameraGrid() {
  const tracks = useTracks([Track.Source.Camera], { onlySubscribed: true })
  const remote = useMemo(
    () => tracks.filter((t) => !t.participant.isLocal),
    [tracks],
  )

  if (remote.length === 0) {
    return (
      <div className="placeholder">
        <p>Waiting for video tracks from the robot…</p>
      </div>
    )
  }

  return (
    <div className="video-grid">
      {remote.map((trackRef) => (
        <div
          key={`${trackRef.participant.identity}-${trackRef.publication.trackSid}`}
          className="video-cell"
        >
          <VideoTrack trackRef={trackRef} className="lk-video-track" />
          <div className="video-label">
            <span className="identity">{trackRef.participant.identity}</span>
            <span className="track-name">
              {trackRef.publication.trackName ?? 'camera'}
            </span>
          </div>
        </div>
      ))}
    </div>
  )
}

export default function App() {
  const [serverUrl, setServerUrl] = useState(defaultUrl)
  const [token, setToken] = useState('')
  const [session, setSession] = useState<{ url: string; token: string } | null>(
    null,
  )

  const canConnect =
    serverUrl.trim().length > 0 && token.trim().length > 0

  if (!session) {
    return (
      <div className="app shell">
        <header className="header">
          <h1>LiveKit operator</h1>
          <p className="subtitle">
            Enter your WebSocket URL and access token (subscriber-only JWT).
          </p>
        </header>
        <form
          className="connect-form"
          onSubmit={(e) => {
            e.preventDefault()
            if (!canConnect) return
            setSession({ url: serverUrl.trim(), token: token.trim() })
          }}
        >
          <label>
            LiveKit URL
            <input
              type="text"
              name="serverUrl"
              autoComplete="off"
              placeholder="ws://127.0.0.1:7880"
              value={serverUrl}
              onChange={(e) => setServerUrl(e.target.value)}
            />
          </label>
          <label>
            Access token (JWT)
            <textarea
              name="token"
              rows={4}
              placeholder="Paste token from livekit-api / lk CLI"
              value={token}
              onChange={(e) => setToken(e.target.value)}
            />
          </label>
          <button type="submit" disabled={!canConnect}>
            Connect
          </button>
        </form>
      </div>
    )
  }

  return (
    <div className="app room">
      <LiveKitRoom
        serverUrl={session.url}
        token={session.token}
        connect
        audio={false}
        video={false}
        onDisconnected={() => setSession(null)}
        onError={(err) => console.error('LiveKit error', err)}
        data-lk-theme="default"
      >
        <header className="toolbar">
          <span className="room-url">{session.url}</span>
          <button type="button" onClick={() => setSession(null)}>
            Disconnect
          </button>
        </header>
        <RemoteCameraGrid />
      </LiveKitRoom>
    </div>
  )
}
