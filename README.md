# MBC 2026 — Personal one-pager

Mobile-first PWA with two pages for the **Machine+Behavior Conference 2026**
(18–19 May, Harnack House, Berlin) and the **Behavioral Clones Workshop**
(20 May, MPIB):

- [`index.html`](./index.html) — program timeline (Mon / Tue / Wed tabs, with a
  "live now / next up" status card in Europe/Berlin time)
- [`people.html`](./people.html) — annotated speaker overview for 45 profiled
  speakers; each card expands to research summary, threads, papers, methodology,
  collaborators, and a candid **Connections to Levin's work** block

The pages are static, single-file vanilla HTML/CSS/JS with no build step at
serve time and no external CDN dependencies. They install as a standalone PWA
(manifest + four icons).

## PIN gate

The site is gated behind a 4-digit PIN ("phone unlock" UX). Data is
encrypted at build time with AES-256-GCM, key derived from the PIN via
PBKDF2-HMAC-SHA256 (200_000 iterations). The HTML pages contain no
plaintext data; on load they fetch `<page>.data.enc.json`, prompt for
the PIN, derive the key client-side via WebCrypto, decrypt, and render.

This is **not** strong security — 10_000 possible PINs are brute-forceable
in ~17 min by anyone determined. It stops casual readers, not motivated
attackers. PIN-cached in `sessionStorage` so refreshes don't re-prompt.

## Rebuilding

The static pages are generated from:

- `/tmp/mbc_program.html` — the cached Squarespace program HTML
  (`machinebehavior.science/program-1`)
- `/Users/brinkmann/repros/research/people/*.md` — 45 speaker briefs (read-only
  for this repo)

To regenerate after editing any of those:

```bash
PIN=<4 digits> ./regen.sh    # runs build.py + encrypt.py
```

Outputs: `index.html`, `people.html`, `manifest.webmanifest`,
`index.data.enc.json`, `people.data.enc.json`. The HTML files have
their inline data scripts emptied and a phone-style unlock screen
injected before `</body>`.

The Wed (workshop) timeline is hard-coded in `build.py::workshop_slots()` since
the Squarespace program HTML only carries the two-day main conference.

## Notes

- Speakers are joined to bios by normalised name; multi-author lightning/poster
  entries (e.g. "Anita Keshmirian, Babak Hemmatian, …") fall back to the first
  author when that author has a profile, otherwise no deep link is rendered.
- "Daniel Relihan" and "Jelena Meyer" appear in the program but are noted in
  `_mbc_speakers_index.md` as not profiled — they show without bios.
