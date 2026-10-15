#!/usr/bin/env python3
"""Check كايرو the way a customer meets it, after every deploy.

    .venv/bin/python tools/smoke_ask.py

Each case is one a customer actually produces, and each has a rule attached that
matters to the shop: answer in the language asked, never open with a greeting,
never quote a discount or an instalment figure, say so when the data has no answer.
"""
import base64
import json
import pathlib
import subprocess
import sys
import time

import httpx

ROOT = pathlib.Path(__file__).resolve().parent.parent
CODE = "101062465"          # the LG QNED we test against
GAP = 14                    # the free Gemini tier allows 5 questions a minute


def env():
    out = {}
    for line in (ROOT / ".env").read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def recording(cfg):
    """Speak a question with the system voice, so the audio path is exercised too."""
    aiff, wav = ROOT / "data" / "smoke.aiff", ROOT / "data" / "smoke.wav"
    subprocess.run(["say", "-v", "Majed", "-o", str(aiff), "الضمان كام سنة على الشاشة دي؟"], check=True)
    subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", str(aiff), str(wav)], check=True)
    data = base64.b64encode(wav.read_bytes()).decode()
    aiff.unlink(missing_ok=True)
    wav.unlink(missing_ok=True)
    return data


def ask(cfg, payload):
    payload.update({"code": CODE, "session_id": "00000000-0000-0000-0000-000000005a1e"})
    key = cfg["SUPABASE_ANON_KEY"]
    started = time.time()
    try:
        r = httpx.post(cfg["SUPABASE_URL"] + "/functions/v1/ask", json=payload, timeout=120,
                       headers={"apikey": key, "Authorization": "Bearer " + key})
        if r.status_code != 200:
            return {"error": f"HTTP {r.status_code}", "detail": r.text[:200]}, time.time() - started
        return r.json(), time.time() - started
    except Exception as e:                       # a dropped network is a failure like any other
        return {"error": type(e).__name__, "detail": str(e)[:200]}, time.time() - started


GREETINGS = ("أهلا", "أهلاً", "مرحبا", "يا هلا", "hi ", "hello", "welcome")


def main():
    cfg = env()
    spoken = recording(cfg)
    cases = [
        ("English keeps to English", {"question": "Hi, what is the warranty?"},
         lambda a: any(c.isascii() and c.isalpha() for c in a) and "الضمان" not in a),
        ("Arabic answer, no greeting", {"question": "الضمان كام؟"},
         lambda a: not a.lower().startswith(GREETINGS)),
        ("A discount is the seller's call", {"question": "فيه خصم لو هاخد اتنين؟"},
         lambda a: "خصم" in a or "البائع" in a),
        ("No instalment figure", {"question": "القسط الشهري كام؟"},
         lambda a: "البائع" in a or "الفرع" in a),
        ("Says so when it does not know", {"question": "بتاكل كهربا كام وات؟"},
         lambda a: "البائع" in a or "متأكد" in a or "مش" in a),
        ("Hears a spoken question", {"audio": spoken, "mime": "audio/wav", "voice": True},
         lambda a: "عامين" in a or "سنتين" in a),
    ]

    failures = 0
    for i, (name, payload, holds) in enumerate(cases):
        data, secs = ask(cfg, payload)
        answer = data.get("answer", "")
        ok = bool(answer) and holds(answer)
        failures += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {name}  ({secs:.1f}s)")
        print(f"      {answer or data}")
        if data.get("said"):
            print(f"      heard: {data['said']}")
        if i < len(cases) - 1:
            time.sleep(GAP)

    print(f"\n{len(cases) - failures}/{len(cases)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
