#!/usr/bin/env python3
"""Encrypt index.html / people.html data blobs with a 4-digit PIN, and
inject a phone-style unlock screen + WebCrypto decryption bootstrap.

For each input page, finds:
  1. `<script type="application/json" id="data">…</script>`  — the data blob
  2. `<script> …app-code… </script>`                          — the renderer

Then:
  - Encrypts the data JSON with AES-256-GCM, key derived from PIN via
    PBKDF2-HMAC-SHA256 (200_000 iterations). Writes ciphertext to
    `<page>.data.enc.json`.
  - Empties the inline data script (keeps the tag for the renderer to read).
  - Changes the renderer <script> type to "text/x-mbc-deferred" so the
    browser doesn't execute it until we say so.
  - Appends a lock-screen DOM block + a bootstrap <script> that prompts for
    a 4-digit PIN, decrypts via WebCrypto, populates the data script, and
    executes the deferred renderer.

Usage:
    PIN=0849 python3 encrypt.py
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import sys
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ROOT = Path(__file__).resolve().parent

# Single fixed app-level salt; combined with PBKDF2's iteration count still
# forces ~100ms work per PIN guess client-side. Keyspace is only 10_000,
# so this is not security against motivated attackers — it discourages
# casual readers who land on the URL.
APP_SALT = b"mbc-2026-personal::brinkmann::v1"
ITERATIONS = 200_000


def derive_key(pin: str) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), APP_SALT, ITERATIONS, dklen=32)


def encrypt_blob(pin: str, plaintext: bytes) -> dict:
    key = derive_key(pin)
    iv = secrets.token_bytes(12)
    aes = AESGCM(key)
    ct = aes.encrypt(iv, plaintext, associated_data=None)
    return {
        "v": 1,
        "kdf": {
            "algo": "PBKDF2-HMAC-SHA256",
            "iter": ITERATIONS,
            "salt_b64": base64.b64encode(APP_SALT).decode(),
        },
        "cipher": {
            "algo": "AES-256-GCM",
            "iv_b64": base64.b64encode(iv).decode(),
            "ct_b64": base64.b64encode(ct).decode(),
        },
    }


DATA_RE = re.compile(
    r'(<script\s+type="application/json"\s+id="data">)(.*?)(</script>)',
    re.DOTALL,
)
RENDERER_RE = re.compile(
    r'(<script>\s*\n\s*function escapeHtml.*?)(</script>)',
    re.DOTALL,
)
BODY_CLOSE_RE = re.compile(r'</body>', re.IGNORECASE)


LOCK_HTML = r"""
<!-- Lock screen overlay (visible until PIN unlock) -->
<div id="lockscreen" aria-hidden="false" style="position:fixed;inset:0;background:var(--bg,#fafaf8);z-index:9999;display:flex;flex-direction:column;align-items:center;justify-content:center;padding:24px;font-family:-apple-system,BlinkMacSystemFont,'Inter','Segoe UI',Roboto,sans-serif;color:#1a1a1a;">
  <div style="font-size:15px;color:#6a6a6a;margin-bottom:8px;letter-spacing:0.6px;text-transform:uppercase;font-weight:600;">M+B 2026</div>
  <div id="lockTitle" style="font-size:18px;font-weight:600;margin-bottom:24px;text-align:center;">Enter PIN</div>
  <div id="pinDots" style="display:flex;gap:14px;margin-bottom:18px;">
    <span class="pin-dot"></span><span class="pin-dot"></span><span class="pin-dot"></span><span class="pin-dot"></span>
  </div>
  <input id="pinInput" type="tel" inputmode="numeric" pattern="[0-9]*" maxlength="4" autocomplete="off"
         aria-label="4-digit PIN"
         style="position:absolute;opacity:0;pointer-events:none;width:1px;height:1px;left:-9999px;">
  <div id="pinMsg" style="font-size:13px;color:#6a6a6a;min-height:18px;margin-top:6px;">Tap to enter</div>
  <button id="pinTap" style="position:absolute;inset:0;background:transparent;border:none;cursor:pointer;" aria-label="Focus PIN entry"></button>
</div>
<style>
  #lockscreen .pin-dot { width:16px; height:16px; border-radius:50%; border:1.5px solid #b5b5b5; background:#fff; transition: background 0.15s, border-color 0.15s, transform 0.15s; }
  #lockscreen .pin-dot.filled { background:#2a5da8; border-color:#2a5da8; transform: scale(1.08); }
  #lockscreen.shake { animation: shake 0.4s; }
  @keyframes shake { 0%,100%{transform:translateX(0);} 20%,60%{transform:translateX(-8px);} 40%,80%{transform:translateX(8px);} }
  #lockscreen.unlocked { opacity:0; pointer-events:none; transition: opacity 0.25s ease; }
</style>
"""

BOOTSTRAP_JS = r"""
<script>
(function () {
  'use strict';
  const SK = 'mbc-pin-v1';
  const lock = document.getElementById('lockscreen');
  const dots = lock.querySelectorAll('.pin-dot');
  const input = document.getElementById('pinInput');
  const msg = document.getElementById('pinMsg');
  const tap = document.getElementById('pinTap');
  const dataTag = document.getElementById('data');
  const codeTag = document.getElementById('app-code');
  const encSrc = dataTag && dataTag.dataset.encSrc;
  if (!encSrc) { msg.textContent = 'Bootstrap error: no encrypted source.'; return; }

  function setDots(n) {
    dots.forEach((d, i) => d.classList.toggle('filled', i < n));
  }

  function focusInput() { setTimeout(() => input.focus(), 50); }
  tap.addEventListener('click', focusInput);
  lock.addEventListener('click', focusInput);

  function b64ToBytes(b64) {
    const bin = atob(b64);
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
  }

  async function deriveKey(pin, saltBytes, iter) {
    const enc = new TextEncoder();
    const baseKey = await crypto.subtle.importKey(
      'raw', enc.encode(pin), { name: 'PBKDF2' }, false, ['deriveKey']
    );
    return crypto.subtle.deriveKey(
      { name: 'PBKDF2', salt: saltBytes, iterations: iter, hash: 'SHA-256' },
      baseKey,
      { name: 'AES-GCM', length: 256 },
      false,
      ['decrypt']
    );
  }

  let encBlob = null;
  async function loadBlob() {
    if (encBlob) return encBlob;
    const r = await fetch(encSrc, { cache: 'no-store' });
    if (!r.ok) throw new Error('fetch failed: ' + r.status);
    encBlob = await r.json();
    return encBlob;
  }

  async function tryUnlock(pin) {
    msg.textContent = 'Unlocking…';
    try {
      const blob = await loadBlob();
      const salt = b64ToBytes(blob.kdf.salt_b64);
      const iv = b64ToBytes(blob.cipher.iv_b64);
      const ct = b64ToBytes(blob.cipher.ct_b64);
      const key = await deriveKey(pin, salt, blob.kdf.iter);
      const plain = await crypto.subtle.decrypt({ name: 'AES-GCM', iv }, key, ct);
      const text = new TextDecoder().decode(plain);
      // Sanity: must be JSON parseable
      JSON.parse(text);
      // Inject and boot
      dataTag.textContent = text;
      try { sessionStorage.setItem(SK, pin); } catch (e) {}
      lock.classList.add('unlocked');
      setTimeout(() => lock.remove(), 280);
      const newScript = document.createElement('script');
      newScript.textContent = codeTag.textContent;
      document.body.appendChild(newScript);
      return true;
    } catch (e) {
      return false;
    }
  }

  function handleAttempt(pin) {
    setDots(4);
    tryUnlock(pin).then(ok => {
      if (!ok) {
        msg.textContent = 'Wrong PIN.';
        lock.classList.add('shake');
        try { sessionStorage.removeItem(SK); } catch (e) {}
        setTimeout(() => {
          lock.classList.remove('shake');
          input.value = '';
          setDots(0);
          msg.textContent = 'Tap to try again';
          focusInput();
        }, 480);
      }
    });
  }

  input.addEventListener('input', () => {
    const v = (input.value || '').replace(/\D/g, '').slice(0, 4);
    input.value = v;
    setDots(v.length);
    if (v.length === 4) handleAttempt(v);
    else msg.textContent = 'Enter your 4-digit PIN';
  });

  // Auto-unlock from session cache (so refresh doesn't re-prompt).
  try {
    const cached = sessionStorage.getItem(SK);
    if (cached && /^\d{4}$/.test(cached)) {
      setDots(4);
      msg.textContent = 'Unlocking…';
      handleAttempt(cached);
    } else {
      focusInput();
    }
  } catch (e) { focusInput(); }
})();
</script>
"""


def process_page(page_name: str, pin: str) -> None:
    page_path = ROOT / page_name
    text = page_path.read_text()

    # 1) Find + encrypt data
    m_data = DATA_RE.search(text)
    if not m_data:
        raise SystemExit(f"{page_name}: data script tag not found")
    plaintext = m_data.group(2).strip().encode("utf-8")
    blob = encrypt_blob(pin, plaintext)
    enc_name = page_path.stem + ".data.enc.json"
    (ROOT / enc_name).write_text(json.dumps(blob, separators=(",", ":")) + "\n")

    # 2) Empty the data script + add data-enc-src
    new_data_script = (
        f'<script type="application/json" id="data" data-enc-src="{enc_name}"></script>'
    )

    # 3) Find the renderer script, change its type so it doesn't execute,
    # and give it an id so the bootstrap can find it.
    m_code = RENDERER_RE.search(text)
    if not m_code:
        raise SystemExit(
            f"{page_name}: renderer script not found (expected <script> containing 'function escapeHtml')"
        )
    new_code_open = '<script type="text/x-mbc-deferred" id="app-code">'
    code_block = new_code_open + m_code.group(1)[len("<script>") :] + m_code.group(2)

    # 4) Splice everything together
    # Replace data tag first
    text2 = text[: m_data.start()] + new_data_script + text[m_data.end() :]
    # Re-find the code block in the new text and replace
    m_code2 = RENDERER_RE.search(text2)
    if m_code2 is None:
        raise SystemExit(f"{page_name}: renderer script lost after data replace")
    text3 = text2[: m_code2.start()] + code_block + text2[m_code2.end() :]

    # 5) Inject lock UI + bootstrap before </body>
    if "</body>" not in text3.lower():
        raise SystemExit(f"{page_name}: </body> not found")
    injection = LOCK_HTML + BOOTSTRAP_JS + "</body>"
    text4 = re.sub(
        r"</body>",
        lambda _m: injection,
        text3,
        count=1,
        flags=re.IGNORECASE,
    )

    page_path.write_text(text4)
    print(f"  {page_name}: encrypted {len(plaintext):,} B → {enc_name}; renderer deferred; lock UI injected")


def main() -> None:
    pin = os.environ.get("PIN", "")
    if not re.fullmatch(r"\d{4}", pin):
        sys.exit("Set PIN=<4 digits> (e.g. PIN=1234 python3 encrypt.py)")
    print(
        f"Encrypting with 4-digit PIN — kdf=PBKDF2-HMAC-SHA256, iter={ITERATIONS:,}, AES-256-GCM"
    )
    for page in ("index.html", "people.html"):
        process_page(page, pin)
    print(
        "Done. The HTML pages no longer contain plaintext data; data files are .data.enc.json"
    )


if __name__ == "__main__":
    main()
