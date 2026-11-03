# Music + SFX for DataAgent Videos

**Rule:** voice first. Music supports, never competes. No trending audio that
fights the narration.

---

## Final A1 mix (delivered)

The delivered cut uses a **synthesized, license-free pad** — no sampled track,
no attribution requirement — plus short UI SFX. It was mixed in
`build_audio.py` and encoded with the final voice.

| Item | Value |
|---|---|
| Voice | `en-US-AndrewNeural`, +0%, 21.53 s |
| Bed | Am7 (0–7.2 s) → Fmaj7 (6.2–14.6 s) → Cmaj9 (13.6–21.8 s), 1.6 s chord crossfades, low-passed at 1.15 kHz, −27 dBFS RMS |
| Ducking | pad −4.5 dB under the voice, 0.18 s smoothed envelope |
| SFX | short whooshes/ticks/pop at the segment cuts; −20 to −18 LUFS peak-equivalent |
| Master | −14.1 LUFS integrated, true peak −1.4 dBFS, 48 kHz stereo AAC 192 kbps |
| Hold | end-card rise starts at 18.86 s; pad fades over the final 2.2 s |

The pad runs under the full read, including the SQL line, at a low level and
ducked. The older “remove music entirely during the SQL/DAX explanation”
guideline was superseded by the delivered mix; if a future read is denser, drop
the bed another 2–3 dB or cut it for that one line rather than raising the voice.

---

## Mix levels

| Layer | Level |
|---|---:|
| Voiceover | −14 LUFS integrated (with the bed) |
| Music bed | −28 to −24 LUFS; A1 uses a −27 dBFS RMS pad |
| SFX (click, whoosh) | −20 to −18 LUFS, short |
| Room tone | −45 to −40 LUFS |

- Duck music by 4–6 dB under every spoken line.
- Keep the bed below the voice; do not let a chord change land on a key word.
- Fade out over the last 0.5–2 s; no hard stop.

---

## Free, monetization-safe sources

| Source | What to use | Licence note |
|---|---|---|
| **YouTube Audio Library** (studio.youtube.com) | Calm, minimal electronic/lo-fi beds | Free; some tracks require attribution — check the track |
| **Pixabay Music** (pixabay.com/music) | Neutral corporate/tech beds | Pixabay Content Licence; no attribution required |
| **Uppbeat** (uppbeat.io) | Modern tech/startup beds | Free tier with code; check per-track terms |
| **Freesound** (freesound.org) | Clicks, keyboard, whoosh, UI ticks | CC0 or CC-BY; filter by licence |
| **YouTube Audio Library SFX** | Interface clicks, transitions | Free |

**Do not use:** commercial tracks, trending Reels audio, or anything you cannot
show a licence for. A synthesized pad (as in A1) avoids the licence question
entirely.

---

## Track direction

Search terms that fit the brand:

- `minimal technology`
- `calm corporate`
- `lo-fi desk`
- `soft pulse`
- `documentary minimal`

Avoid: EDM drops, orchestral swells, "epic AI", loud drums, sirens,
riser-heavy trailers.

---

## SFX map for A1

| Time | Sound | Level |
|---:|---|---:|
| 0:00.02 | Soft whoosh into the hook | −22 LUFS |
| 0:05.612 | Keyboard tick as the prompt is sent | −20 LUFS |
| 0:07.045 | Subtle SQL / result tick | −22 LUFS |
| 0:08.190 | Soft chart pop | −22 LUFS |
| 0:09.385 | Export click | −20 LUFS |
| 0:11.375 | Whoosh into the Power BI open | −22 LUFS |
| 0:18.860 | Soft chord rise under the end card | −26 LUFS |

All SFX are short and panned subtly; none should read as a notification.

---

## Voice

- The delivered A1 voice is AI-generated (Edge TTS). Disclose it in the
  caption/description: `AI-generated voice; product demo is real screen capture.`
- For a human re-record: treated room or closet with soft furnishings, mic
  15–20 cm away and slightly off-axis, 10 s of room tone, normalize to −14 LUFS
  and keep true peak ≤ −1 dB.
- Do not add: “free `.pbix`”, “unlimited”, “100% private”, “open source”, or
  absolute privacy claims (“never leaves your machine”, “fully offline”).
