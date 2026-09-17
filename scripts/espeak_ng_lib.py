"""`espeak-ng -w OUT.wav -s RATE TEXT`, through the espeak-ng library in the `espeakng-loader` wheel.

The Intel Mac test kit's stand-in for the espeak-ng program, which no Intel Mac can get without a
package manager: Homebrew refuses Intel now, and MacPorts is a second one to install. The wheel on
PyPI carries libespeak-ng 1.52.0 and its voice data for Intel and Apple silicon, but no program, so
this is the program's one use in proofcut — `scripts/make_demo.py`'s `-w`/`-s` — and nothing else.
It is the same library at the same version as the program, so the demo's voice, and so whatever
whisper hears in it, is the same one every other platform makes.

Run it with the wheel alongside, never from proofcut's own environment:

    uv run --no-project --with espeakng-loader==0.2.4 python scripts/espeak_ng_lib.py -w out.wav -s 150 "text"
"""

from __future__ import annotations

import argparse
import ctypes
import sys
import wave

import espeakng_loader

AUDIO_OUTPUT_SYNCHRONOUS = 2
POS_CHARACTER = 1
espeakRATE = 1
# The program's own flags (src/espeak-ng.c): auto-detected encoding, [[phoneme]] input, a pause at
# the end.
espeakCHARS_AUTO = 0
espeakPHONEMES = 0x100
espeakENDPAUSE = 0x1000

CALLBACK = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.POINTER(ctypes.c_short), ctypes.c_int, ctypes.c_void_p)


def synthesise(text: str, rate: int) -> tuple[int, bytes]:
    """(sample rate, 16-bit mono PCM) for `text` read at `rate` words a minute."""
    lib = espeakng_loader.load_library()
    if lib is None:
        raise SystemExit("espeak-ng: the library in espeakng-loader would not load")
    lib.espeak_Initialize.restype = ctypes.c_int
    sample_rate = lib.espeak_Initialize(
        AUDIO_OUTPUT_SYNCHRONOUS, 0, espeakng_loader.get_data_path().encode(), 0
    )
    if sample_rate <= 0:
        raise SystemExit(f"espeak-ng: initialise failed ({sample_rate})")

    pcm = bytearray()

    def collect(samples, count, _events):
        if count > 0:
            pcm.extend(ctypes.string_at(samples, count * 2))
        return 0

    callback = CALLBACK(collect)  # held in a local for as long as the library may call it
    lib.espeak_SetSynthCallback(callback)
    if lib.espeak_SetVoiceByName(b"en") != 0:
        raise SystemExit("espeak-ng: no 'en' voice in the library's data")
    lib.espeak_SetParameter(espeakRATE, rate, 0)
    data = text.encode("utf-8") + b"\0"
    status = lib.espeak_Synth(
        data, ctypes.c_size_t(len(data)), 0, POS_CHARACTER, 0,
        espeakCHARS_AUTO | espeakPHONEMES | espeakENDPAUSE, None, None,
    )  # fmt: skip
    if status != 0:
        raise SystemExit(f"espeak-ng: synthesis failed ({status})")
    lib.espeak_Synchronize()
    lib.espeak_Terminate()
    return sample_rate, bytes(pcm)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="espeak-ng", description=__doc__.splitlines()[0])
    parser.add_argument("-w", dest="out", required=True, help="the wav file to write")
    parser.add_argument("-s", dest="rate", type=int, default=175, help="words per minute")
    parser.add_argument("text")
    args = parser.parse_args(argv)
    sample_rate, pcm = synthesise(args.text, args.rate)
    with wave.open(args.out, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(sample_rate)
        out.writeframes(pcm)
    return 0


if __name__ == "__main__":
    sys.exit(main())
