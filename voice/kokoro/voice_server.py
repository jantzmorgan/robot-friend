import asyncio
import json
import threading
import urllib.request

import sounddevice as sd

from flask import (
    Flask,
    request,
    jsonify
)

from kokoro_onnx import Kokoro


# ============================================================
# SETTINGS
# ============================================================

HOST = "127.0.0.1"
PORT = 8001

BRAIN_URL = "http://127.0.0.1:8000"

VOICE = "am_fenrir"

# Slightly quicker sounds more natural for conversation
# without making him sound hyper.
SPEED = 1.05

LANG = "en-us"


# ============================================================
# APP
# ============================================================

app = Flask(
    __name__
)


# ============================================================
# LOAD KOKORO ONCE
# ============================================================

print(
    "Loading Kokoro voice engine..."
)


kokoro = Kokoro(
    "kokoro-v1.0.onnx",
    "voices-v1.0.bin"
)


print(
    "Kokoro loaded."
)

print(
    f"Robot voice: {VOICE}"
)


# ============================================================
# SPEECH CONTROL
# ============================================================

speech_lock = (
    threading.Lock()
)


stop_requested = (
    threading.Event()
)


# ============================================================
# TELL ROBOT BRAIN WHETHER AUDIO IS ACTUALLY PLAYING
# ============================================================

def report_speaking_state(
    speaking
):

    try:

        payload = (
            json.dumps({
                "speaking":
                    speaking
            })
            .encode(
                "utf-8"
            )
        )


        brain_request = (
            urllib.request.Request(

                f"{BRAIN_URL}/speech/event",

                data=
                    payload,

                headers={
                    "Content-Type":
                        "application/json"
                },

                method=
                    "POST"
            )
        )


        with (
            urllib.request.urlopen(
                brain_request,
                timeout=1
            )
        ):

            pass


    except Exception as error:

        print(
            "Could not report speech state:",
            error
        )


# ============================================================
# STREAM SPEECH
#
# Kokoro create_stream yields generated audio chunks.
# We only report "speaking" once the first real audio chunk
# is ready to play.
# ============================================================

async def stream_speech(
    text
):

    stream = (
        kokoro.create_stream(

            text,

            voice=
                VOICE,

            speed=
                SPEED,

            lang=
                LANG
        )
    )


    actually_speaking = False


    try:

        async for (
            samples,
            sample_rate
        ) in stream:


            if (
                stop_requested
                .is_set()
            ):

                print(
                    "Kokoro speech interrupted."
                )

                break


            # ------------------------------------------------
            # IMPORTANT:
            #
            # Do not animate the mouth while Kokoro is merely
            # generating.
            #
            # The first actual audio chunk means sound is now
            # ready for playback.
            # ------------------------------------------------

            sd.play(
                samples,
                sample_rate
            )


            # sd.play has now opened and started the output stream. Reporting
            # after this call prevents the mouth from leading the speaker by
            # the HTTP callback round-trip.
            if not actually_speaking:

                actually_speaking = True


                report_speaking_state(
                    True
                )


                print(
                    "Audio playback started."
                )


            sd.wait()


    finally:

        if actually_speaking:

            report_speaking_state(
                False
            )


        print(
            "Audio playback finished."
        )


# ============================================================
# SPEAK
# ============================================================

@app.route(
    "/speak",
    methods=[
        "POST"
    ]
)
def speak():

    data = (
        request.get_json(
            silent=True
        )
        or
        {}
    )


    text = (
        data.get(
            "text",
            ""
        )
        .strip()
    )


    if not text:

        return jsonify({
            "error":
                "No text supplied."
        }), 400


    with speech_lock:


        stop_requested.clear()


        print()

        print(
            "Generating Fenrir speech..."
        )

        print(
            text
        )


        try:

            asyncio.run(
                stream_speech(
                    text
                )
            )


            return jsonify({

                "status":
                    "finished",

                "voice":
                    VOICE,

                "interrupted":
                    stop_requested
                    .is_set()
            })


        except Exception as error:

            report_speaking_state(
                False
            )


            print(
                "KOKORO ERROR:",
                repr(
                    error
                )
            )


            return jsonify({
                "error":
                    str(error)
            }), 500


# ============================================================
# STOP
# ============================================================

@app.route(
    "/stop",
    methods=[
        "POST"
    ]
)
def stop():

    stop_requested.set()


    try:

        sd.stop()

    except Exception:

        pass


    report_speaking_state(
        False
    )


    return jsonify({
        "status":
            "stop requested"
    })


# ============================================================
# HEALTH
# ============================================================

@app.route(
    "/health",
    methods=[
        "GET"
    ]
)
def health():

    return jsonify({

        "status":
            "kokoro online",

        "voice":
            VOICE
    })


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    print()

    print(
        "Kokoro Robot Voice Server"
    )

    print(
        f"http://{HOST}:{PORT}"
    )


    app.run(

        host=
            HOST,

        port=
            PORT,

        debug=
            False,

        use_reloader=
            False,

        threaded=
            True
    )
