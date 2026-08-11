import time

import numpy as np
import sounddevice as sd

from kokoro_onnx import Kokoro


# ============================================================
# MODEL
# ============================================================

kokoro = Kokoro(
    "kokoro-v1.0.onnx",
    "voices-v1.0.bin"
)


# ============================================================
# MALE AMERICAN VOICES
# ============================================================

VOICES = [
    "am_adam",
    "am_echo",
    "am_eric",
    "am_fenrir",
    "am_liam",
    "am_michael",
]


# ============================================================
# TEST LINE
#
# Slightly longer sentence on purpose.
# Kokoro tends to perform better on normal-length phrases
# than extremely tiny one-line samples.
# ============================================================

TEXT = (
    "Okay, this is significantly better. "
    "I was beginning to think you planned on trapping "
    "a customer service representative inside my body forever."
)


# ============================================================
# AUDITION
# ============================================================

for voice in VOICES:

    print()
    print("=" * 60)
    print("VOICE:", voice)
    print("=" * 60)


    try:

        samples, sample_rate = kokoro.create(
            TEXT,
            voice=voice,
            speed=1.0,
            lang="en-us",
        )


        audio = np.asarray(
            samples,
            dtype=np.float32
        )


        print(
            f"Playing {voice}..."
        )


        sd.play(
            audio,
            sample_rate
        )


        sd.wait()


        print(
            f"Finished {voice}."
        )


        time.sleep(
            0.7
        )


    except Exception as error:

        print(
            f"{voice} FAILED:",
            error
        )


print()
print("AUDITION COMPLETE")