"""Robot Friend brain and hardware-control API.

Runs with simulated hardware by default on Windows and Jetson. Physical
drivers can later replace the simulated implementations through robot.factory.
"""

from __future__ import annotations

import atexit
import hashlib
import json
import logging
import os
import re
import sys
import threading
from pathlib import Path

from dotenv import load_dotenv
import httpx
from flask import Flask, Response, jsonify, request, send_from_directory
from flask_cors import CORS
from werkzeug.exceptions import HTTPException

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from memory.memory_manager import RobotMemory
from robot.factory import create_runtime
from robot.orchestrator import Orchestrator
from robot.runtime import SafetyError

load_dotenv(ROOT_DIR / ".env")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logging.getLogger("werkzeug").setLevel(logging.WARNING)

app = Flask(__name__)
CORS(app)

PERSONALITY_PATH = ROOT_DIR / "personality" / "robot_personality.md"
MEMORY_PATH = Path(os.getenv("ROBOT_MEMORY_PATH", ROOT_DIR / "memory" / "robot_memory.db"))
PERSONALITY = PERSONALITY_PATH.read_text(encoding="utf-8").strip()
memory_manager = RobotMemory(MEMORY_PATH)
runtime = create_runtime()
orchestrator = Orchestrator(runtime)
conversation: list[dict[str, str]] = []
conversation_lock = threading.RLock()
CURRENT_MEMORY_SUBJECT = os.getenv("ROBOT_MEMORY_SUBJECT", "primary_user")
wake_listener = None
wake_command_lock = threading.Lock()
realtime_guard_lock = threading.Lock()
realtime_guard_generation = 0


def realtime_enabled() -> bool:
    return os.getenv("ROBOT_REALTIME", "1").lower() not in {"0", "false", "off", "no"}


def realtime_readiness() -> tuple[bool, str | None]:
    if not realtime_enabled():
        return False, "Realtime voice is disabled"
    if not os.getenv("OPENAI_API_KEY", "").strip():
        return False, "OPENAI_API_KEY is missing from .env"
    return True, None


def cancel_realtime_guard() -> None:
    global realtime_guard_generation
    with realtime_guard_lock:
        realtime_guard_generation += 1


def recover_stale_realtime(generation: int) -> None:
    """Never let a vanished browser leave the wake microphone paused."""
    with realtime_guard_lock:
        if generation != realtime_guard_generation:
            return
    state = runtime.state.snapshot()
    if state["wake_paused"]:
        app.logger.warning("Realtime browser disappeared; resuming wake-word listener")
        if wake_listener is not None:
            wake_listener.resume()
        runtime.state.update(
            listening=True, speaking=False, wake_detected=False,
            wake_paused=False, mode="idle",
        )


def arm_realtime_guard(timeout: float = 12.0) -> None:
    global realtime_guard_generation
    with realtime_guard_lock:
        realtime_guard_generation += 1
        generation = realtime_guard_generation
    timer = threading.Timer(timeout, recover_stale_realtime, args=(generation,))
    timer.daemon = True
    timer.start()

FACE_COLORS = {
    "red": "#FF3B30", "orange": "#FF8A2D", "herman orange": "#FF5A2D",
    "burnt orange": "#D95F22", "coral": "#FF6F61", "peach": "#FFB38A",
    "amber": "#FFB43B", "gold": "#FFD34E", "yellow": "#FFE14A",
    "chartreuse": "#BFFF36", "lime": "#B7FF3C", "green": "#4CFF88",
    "emerald": "#28E58B", "mint": "#79FFD2", "teal": "#35F2D0",
    "turquoise": "#31E6DC", "aqua": "#42F5FF", "cyan": "#42E8FF",
    "sky blue": "#65D9FF", "blue": "#55CFFF", "navy": "#4267D9",
    "indigo": "#6657E8", "violet": "#7B8CFF", "purple": "#A970FF",
    "lavender": "#C4A7FF", "plum": "#C05AE8", "magenta": "#FF3FD5",
    "hot pink": "#FF45A7", "pink": "#FF4FC8", "rose": "#FF6F9F",
    "white": "#F4FAFF", "silver": "#C9D7E3",
}

MEMORY_CURATOR_INSTRUCTIONS = """You curate durable memory for a companion robot.
Return only a JSON array with at most five objects containing text, category,
and importance (0.0-1.0). Store stable preferences, people, projects, goals,
inside jokes, promises, or milestones. Never store secrets or routine small talk.
Use [] when nothing deserves long-term storage."""


def get_openai_client():
    from openai import OpenAI
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set; hardware APIs still work normally")
    return OpenAI(api_key=api_key)


def robot_context(user_message: str) -> str:
    state = runtime.state.snapshot()
    memory = memory_manager.get_context(user_message, CURRENT_MEMORY_SUBJECT, limit=6)
    return (
        f"{PERSONALITY}\n\nCURRENT ROBOT STATE (real telemetry; do not invent more):\n"
        f"{json.dumps(state, indent=2)}\n\n"
        "FACE CAPABILITIES:\n"
        "You can genuinely change your LED face to one or several named colors, "
        "an animated full rainbow, fire, or blue tears when asked. The current "
        "face_theme, face_colors, and face_effect above are real. Acknowledge color "
        "changes naturally as something you physically did; never claim a change "
        "that is not reflected in that state.\n\n"
        "SPOKEN TURN RULES:\n"
        "Default to one natural sentence of 5-20 words. Answer directly and stop. "
        "Do not use headings, lists, recaps, caveats, or offer extra help in ordinary "
        "conversation. Only become detailed when the user explicitly asks for detail.\n\n"
        f"{memory}"
    )


def realtime_session_config() -> dict:
    """Build a short, stateful voice session without exposing the API key."""
    instructions = robot_context("live spoken conversation") + (
        "\n\nLIVE CONVERSATION RULES:\n"
        "You are speaking aloud through the robot's face. Sound warm, compact, and "
        "slightly robotic, but never imitate a specific copyrighted character. "
        "Usually answer in one sentence and under 20 words. Do not narrate your "
        "reasoning or say you are an AI. Treat follow-up speech as the same conversation. "
        "If the user pauses mid-thought, wait instead of jumping in. If interrupted, stop "
        "and listen. Briefly acknowledge an explicit goodbye or stop-listening request."
    )
    return {
        "type": "realtime",
        "model": os.getenv("ROBOT_REALTIME_MODEL", "gpt-realtime-2.1-mini"),
        "output_modalities": ["audio"],
        "instructions": instructions,
        "max_output_tokens": int(os.getenv("ROBOT_REALTIME_MAX_OUTPUT_TOKENS", "120")),
        "audio": {
            "input": {
                "noise_reduction": {"type": "far_field"},
                "transcription": {"model": "gpt-realtime-whisper", "language": "en"},
                "turn_detection": {
                    "type": "semantic_vad",
                    "eagerness": os.getenv("ROBOT_REALTIME_EAGERNESS", "medium"),
                    "create_response": True,
                    "interrupt_response": True,
                },
            },
            "output": {
                "voice": os.getenv("ROBOT_REALTIME_VOICE", "cedar"),
                "speed": float(os.getenv("ROBOT_REALTIME_SPEED", "1.05")),
            },
        },
    }


def curate_memory(user_message: str, reply: str) -> None:
    try:
        response = get_openai_client().responses.create(
            model=os.getenv("ROBOT_MODEL", "gpt-5.6-luna"),
            instructions=MEMORY_CURATOR_INSTRUCTIONS,
            input=f"USER:\n{user_message}\n\nROBOT:\n{reply}",
            reasoning={"effort": "low"}, max_output_tokens=350,
        )
        raw = response.output_text.strip()
        if raw.startswith("```"):
            raw = "\n".join(raw.splitlines()[1:-1])
        for item in json.loads(raw)[:5]:
            if isinstance(item, dict) and str(item.get("text", "")).strip():
                memory_manager.remember(
                    memory_text=item["text"], category=item.get("category", "general"),
                    importance=item.get("importance", 0.5), subject=CURRENT_MEMORY_SUBJECT,
                    source="conversation",
                )
    except Exception:
        app.logger.exception("Background memory curation failed")


def generate_reply(user_message: str) -> str:
    with conversation_lock:
        recent = [*conversation[-10:], {"role": "user", "content": user_message}]
    response = get_openai_client().responses.create(
        model=os.getenv("ROBOT_MODEL", "gpt-5.4-mini"),
        instructions=robot_context(user_message), input=recent,
        # Spoken conversation is latency-sensitive and normally needs no
        # hidden deliberation. This remains configurable for harder uses.
        reasoning={"effort": os.getenv("ROBOT_REASONING_EFFORT", "none")},
        max_output_tokens=80,
        text={"verbosity": "low"},
    )
    reply = response.output_text.strip()
    if not reply:
        raise RuntimeError("OpenAI returned an empty reply")
    with conversation_lock:
        conversation.extend((
            {"role": "user", "content": user_message},
            {"role": "assistant", "content": reply},
        ))
        del conversation[:-30]
    threading.Thread(target=curate_memory, args=(user_message, reply), daemon=True).start()
    return reply


def apply_spoken_face_colors(message: str) -> list[str] | None:
    """Apply explicit natural-language appearance requests locally."""
    lowered = message.lower()
    has_color = any(name in lowered for name in FACE_COLORS) or any(
        word in lowered for word in ("rainbow", "multicolor", "multi color", "original")
    )
    action = bool(re.search(r"\b(make|change|turn|set|switch|go|be|light)\b", lowered)) and has_color
    effect = None
    expression = None
    stop_effect = bool(re.search(
        r"\b(stop|end|clear|remove|quit|disable|turn off|no more)\b.{0,24}"
        r"\b(fire|flames|burning|cry|crying|tears|effect|effects)\b",
        lowered,
    )) or any(phrase in lowered for phrase in (
        "effects off", "effect off", "no effect", "normal effect",
    ))
    start_fire = any(phrase in lowered for phrase in (
        "on fire", "catch fire", "start fire", "start the fire", "show flames",
        "turn fire on", "fire on", "fiery",
    ))
    start_tears = bool(re.search(r"\b(cry|crying|weep|tears|tearful)\b", lowered))
    expression_action = bool(re.search(
        r"\b(make|change|turn|set|switch|go|get|be|look|show)\b", lowered
    ))

    if stop_effect:
        effect = "none"
        expression = "normal"
    elif start_fire:
        effect = "fire"
        expression = "annoyed"
    elif start_tears:
        effect = "tears"
        expression = "sad"
    elif expression_action and re.search(r"\b(mad|angry|annoyed)\b", lowered):
        # Auto shows fire for an annoyed face and, importantly, replaces tears.
        effect = "auto"
        expression = "annoyed"
    elif expression_action and re.search(r"\b(sad|upset)\b", lowered):
        effect = "auto"
        expression = "sad"
    elif expression_action and re.search(r"\b(happy|excited|surprised|curious|normal)\b", lowered):
        effect = "auto"
        for name in ("happy", "excited", "surprised", "curious", "normal"):
            if name in lowered:
                expression = name
                break

    if not action and effect is None:
        return None

    found: list[str] = []
    consumed = lowered
    for name in sorted(FACE_COLORS, key=len, reverse=True):
        if name in consumed:
            value = FACE_COLORS[name]
            if value not in found:
                found.append(value)
            consumed = consumed.replace(name, " ")

    theme = "custom"
    if "rainbow" in lowered or "full spectrum" in lowered:
        found = ["#42E8FF", "#7B8CFF", "#FF4FC8"]
        theme = "rainbow"
    elif "multicolor" in lowered or "multi color" in lowered:
        found = ["#42E8FF", "#7B8CFF", "#FF4FC8"]
    elif "original" in lowered or ("herman" in lowered and not found):
        found = ["#FF5A2D"]
        theme = "herman"

    if not found and effect is None:
        return None

    found = found[:4]
    changes = {}
    if found:
        changes.update(face_theme=theme if theme != "custom" else ("custom" if len(found) > 1 else "solid"),
                       face_colors=found)
    if effect is not None:
        changes["face_effect"] = effect
    if expression is not None:
        changes["expression"] = expression
    runtime.state.update(**changes)
    return found


def transcribe_command(audio_bytes: bytes) -> str:
    """Transcribe an approved post-wake command through OpenAI."""
    transcript = get_openai_client().audio.transcriptions.create(
        model=os.getenv("ROBOT_TRANSCRIBE_MODEL", "gpt-4o-mini-transcribe"),
        file=("robot-command.wav", audio_bytes, "audio/wav"),
        language="en",
    )
    return str(transcript.text).strip()


def process_wake_command() -> None:
    """Run a multi-turn conversation, then safely return to wake-word mode."""
    if not wake_command_lock.acquire(blocking=False):
        return
    try:
        if wake_listener is None:
            return

        wake_listener.pause(wait=True)
        silence_seconds = float(os.getenv("ROBOT_COMMAND_SILENCE_SECONDS", "1.1"))
        followup_wait = float(os.getenv("ROBOT_CONVERSATION_WAIT_SECONDS", "12"))
        max_turns = max(1, int(os.getenv("ROBOT_CONVERSATION_MAX_TURNS", "20")))
        exit_phrases = {
            "stop listening", "end conversation", "goodbye", "go to sleep",
            "that's all", "that is all", "never mind", "nevermind",
        }

        print("Conversation mode active.")
        for turn_number in range(max_turns):
            runtime.state.update(listening=True, mode="listening")
            audio_bytes = wake_listener.capture_command(
                max_seconds=15.0 if turn_number == 0 else followup_wait,
                silence_seconds=silence_seconds,
            )
            if not audio_bytes:
                print("Conversation timed out; returning to wake-word mode.")
                break

            message = transcribe_command(audio_bytes)
            if not message:
                app.logger.warning("OpenAI returned an empty command transcript")
                continue

            print(f"You: {message}")
            normalized = re.sub(r"[^a-z' ]", "", message.lower()).strip()
            if normalized in exit_phrases:
                runtime.state.update(listening=False)
                runtime.speak("Okay. Say Hey Jarvis when you need me.")
                break

            runtime.state.update(listening=False, mode="thinking")
            apply_spoken_face_colors(message)
            reply = generate_reply(message)
            print(f"Robot: {reply}")
            runtime.speak(reply)
        else:
            print("Conversation turn limit reached; returning to wake-word mode.")
    except Exception as error:
        runtime.state.update(last_error=f"Voice command failed: {error}")
        app.logger.exception("Post-wake voice command failed")
    finally:
        if wake_listener is not None:
            wake_listener.resume()
        runtime.state.update(
            listening=True,
            wake_detected=False,
            wake_paused=False,
            mode="idle",
        )
        wake_command_lock.release()


def handle_wake_word() -> None:
    """Notify the face, release the mic, and process the command in Python."""
    ready, reason = realtime_readiness()
    if realtime_enabled() and not ready:
        runtime.state.update(
            wake_detected=False, wake_paused=False, listening=True,
            mode="idle", last_error=reason,
        )
        app.logger.error("Wake ignored: %s", reason)
        return
    state = runtime.state.snapshot()
    runtime.state.update(
        wake_detected=True,
        wake_counter=state["wake_counter"] + 1,
        wake_paused=True,
        listening=True,
    )
    if wake_listener is not None:
        # This callback runs on the listener thread, so it must not wait for
        # that same thread to acknowledge the pause.
        wake_listener.pause(wait=False)
        if realtime_enabled():
            # If the face page is closed, denied microphone access, or fails
            # before WebRTC connects, restore wake mode automatically.
            arm_realtime_guard()
        else:
            threading.Thread(target=process_wake_command, daemon=True).start()


def start_wake_word() -> None:
    """Start OpenWakeWord when enabled; keep the rest of the robot usable on failure."""
    global wake_listener
    if os.getenv("ROBOT_WAKE_WORD", "1").lower() in {"0", "false", "off", "no"}:
        app.logger.info("Wake word disabled by ROBOT_WAKE_WORD")
        return
    try:
        from voice.wakeword import WakeWordListener
        wake_listener = WakeWordListener(handle_wake_word)
        wake_listener.start()
        runtime.state.update(wake_word_online=True, wake_paused=False, listening=True)
    except Exception as error:
        wake_listener = None
        runtime.state.update(wake_word_online=False, last_error=f"Wake word unavailable: {error}")
        app.logger.exception("Wake word listener could not start")


@app.errorhandler(SafetyError)
def safety_error(error):
    return jsonify(error=str(error), state=runtime.state.snapshot()), 409


@app.errorhandler(ValueError)
def value_error(error):
    return jsonify(error=str(error)), 400


@app.errorhandler(Exception)
def unexpected_error(error):
    if isinstance(error, HTTPException):
        return jsonify(error=error.description), error.code
    app.logger.exception("Unhandled brain request failure")
    return jsonify(error=str(error)), 500


@app.get("/")
def face():
    return send_from_directory(ROOT_DIR / "face", "index.html")


@app.get("/health")
def health():
    realtime_ready, realtime_error = realtime_readiness()
    return jsonify(
        status="brain online", hardware=os.getenv("ROBOT_HARDWARE", "sim"),
        services={
            "realtime_enabled": realtime_enabled(),
            "realtime_ready": realtime_ready,
            "realtime_error": realtime_error,
        },
        state=runtime.state.snapshot(),
    )


@app.get("/state")
def state():
    return jsonify(runtime.state.snapshot())


@app.get("/vision")
def vision():
    sensors = runtime.state.snapshot()["sensors"]
    return jsonify(camera_online=sensors.get("camera_online", False),
                   camera_index=sensors.get("camera_index"),
                   face_found=sensors.get("person_visible", False),
                   x=sensors.get("face_x", 0.0), y=sensors.get("face_y", 0.0),
                   face_size=sensors.get("face_size", 0.0))


@app.post("/motion")
def motion():
    data = request.get_json(silent=True) or {}
    runtime.drive(float(data.get("linear", 0.0)), float(data.get("angular", 0.0)),
                  float(data["duration"]) if data.get("duration") is not None else None)
    return jsonify(runtime.state.snapshot())


@app.post("/motion/stop")
def motion_stop():
    runtime.stop()
    return jsonify(runtime.state.snapshot())


@app.post("/safety/stop")
def safety_stop():
    data = request.get_json(silent=True) or {}
    runtime.emergency_stop(data.get("reason", "Emergency stop requested through API"))
    return jsonify(runtime.state.snapshot())


@app.post("/safety/reset")
def safety_reset():
    runtime.reset_safety()
    return jsonify(runtime.state.snapshot())


@app.post("/face")
def set_face():
    data = request.get_json(silent=True) or {}
    runtime.set_face(str(data.get("expression", "neutral")), str(data.get("message", "")))
    return jsonify(runtime.state.snapshot())


@app.post("/tts")
def tts():
    data = request.get_json(silent=True) or {}
    text = str(data.get("text", "")).strip()
    if not text:
        raise ValueError("No text supplied")
    runtime.speak(text)
    return jsonify(status="finished", spoken=True)


@app.post("/speech/stop")
def speech_stop():
    runtime.audio_output.stop()
    runtime.state.update(speaking=False, mode="idle")
    return jsonify(status="speech stop requested")


@app.post("/speech/event")
def speech_event():
    speaking = bool((request.get_json(silent=True) or {}).get("speaking", False))
    runtime.state.update(speaking=speaking, mode="speaking" if speaking else "idle")
    return jsonify(speaking=speaking)


@app.post("/wake/pause")
def pause_wake():
    if wake_listener is not None:
        wake_listener.pause()
    runtime.state.update(listening=False, wake_paused=True)
    return jsonify(status="wake word paused")


@app.post("/wake/resume")
def resume_wake():
    if wake_listener is not None:
        wake_listener.resume()
    runtime.state.update(listening=True, wake_detected=False, wake_paused=False)
    return jsonify(status="wake word resumed")


@app.post("/realtime/session")
def create_realtime_session():
    """Exchange the browser's WebRTC offer for an OpenAI answer SDP."""
    if not realtime_enabled():
        return jsonify(error="Realtime conversation is disabled"), 409
    ready, reason = realtime_readiness()
    if not ready:
        runtime.state.update(last_error=reason)
        return jsonify(error=reason), 503
    api_key = os.environ["OPENAI_API_KEY"]
    offer = request.get_data(as_text=True).strip()
    if not offer:
        raise ValueError("No WebRTC offer supplied")

    if wake_listener is not None:
        wake_listener.pause(wait=True)
    runtime.state.update(
        listening=True, speaking=False, wake_detected=True,
        wake_paused=True, mode="listening",
    )
    arm_realtime_guard(10.0)
    safety_id = hashlib.sha256(CURRENT_MEMORY_SUBJECT.encode("utf-8")).hexdigest()
    upstream = httpx.post(
        "https://api.openai.com/v1/realtime/calls",
        headers={
            "Authorization": f"Bearer {api_key}",
            "OpenAI-Safety-Identifier": safety_id,
        },
        files={
            "sdp": (None, offer),
            "session": (None, json.dumps(realtime_session_config())),
        },
        timeout=30.0,
    )
    if upstream.is_error:
        try:
            upstream_message = upstream.json().get("error", {}).get("message", "")
        except (ValueError, AttributeError):
            upstream_message = ""
        detail = upstream_message or f"OpenAI Realtime returned HTTP {upstream.status_code}"
        runtime.state.update(last_error=f"Realtime session failed: {detail}")
        app.logger.error("Realtime session failed (%s): %s", upstream.status_code, upstream.text)
        return jsonify(error=detail), upstream.status_code
    runtime.state.update(last_error=None)
    return Response(upstream.text, status=200, content_type="application/sdp")


@app.post("/realtime/event")
def realtime_event():
    state = str((request.get_json(silent=True) or {}).get("state", "")).lower()
    changes = {
        "listening": dict(listening=True, speaking=False, mode="listening"),
        "thinking": dict(listening=False, speaking=False, mode="thinking"),
        "speaking": dict(listening=False, speaking=True, mode="speaking"),
        "idle": dict(listening=True, speaking=False, mode="idle"),
    }
    if state not in changes:
        raise ValueError("Unknown realtime state")
    runtime.state.update(**changes[state])
    arm_realtime_guard(10.0)
    return jsonify(runtime.state.snapshot())


@app.post("/realtime/heartbeat")
def realtime_heartbeat():
    if runtime.state.snapshot()["wake_paused"]:
        arm_realtime_guard(10.0)
    return jsonify(status="alive")


@app.post("/realtime/user")
def realtime_user_turn():
    message = str((request.get_json(silent=True) or {}).get("message", "")).strip()
    if message:
        apply_spoken_face_colors(message)
    return jsonify(applied=bool(message), state=runtime.state.snapshot())


@app.post("/realtime/turn")
def save_realtime_turn():
    data = request.get_json(silent=True) or {}
    user_message = str(data.get("user", "")).strip()
    reply = str(data.get("assistant", "")).strip()
    if not user_message and not reply:
        raise ValueError("No conversation turn supplied")
    with conversation_lock:
        if user_message:
            conversation.append({"role": "user", "content": user_message})
        if reply:
            conversation.append({"role": "assistant", "content": reply})
        del conversation[:-30]
    if user_message and reply:
        threading.Thread(target=curate_memory, args=(user_message, reply), daemon=True).start()
    return jsonify(saved=True)


@app.post("/realtime/end")
def end_realtime_session():
    cancel_realtime_guard()
    if wake_listener is not None:
        wake_listener.resume()
    runtime.state.update(
        listening=True, speaking=False, wake_detected=False,
        wake_paused=False, mode="idle",
    )
    return jsonify(status="realtime conversation ended")


@app.post("/chat")
def chat():
    message = str((request.get_json(silent=True) or {}).get("message", "")).strip()
    if not message:
        raise ValueError("No message supplied")
    apply_spoken_face_colors(message)
    return jsonify(reply=generate_reply(message))


@app.post("/respond")
def respond():
    message = str((request.get_json(silent=True) or {}).get("message", "")).strip()
    if not message:
        raise ValueError("No message supplied")
    apply_spoken_face_colors(message)
    reply = generate_reply(message)
    runtime.speak(reply)
    return jsonify(reply=reply, spoken=True)


@app.get("/memories")
def memories():
    limit = min(max(int(request.args.get("limit", 20)), 1), 100)
    return jsonify(memory_manager.recent(limit=limit, subject=CURRENT_MEMORY_SUBJECT))


def shutdown() -> None:
    if wake_listener is not None:
        wake_listener.stop()
    orchestrator.stop()


atexit.register(shutdown)

if __name__ == "__main__":
    orchestrator.start()
    start_wake_word()
    app.run(host=os.getenv("ROBOT_HOST", "127.0.0.1"),
            port=int(os.getenv("ROBOT_PORT", "8000")), threaded=True,
            debug=os.getenv("ROBOT_DEBUG", "0") == "1", use_reloader=False)
