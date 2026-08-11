# Robot Friend — Phase 1 software

Robot Friend now runs against simulated hardware on Windows and exposes the
same small driver contracts that the Jetson Orin Nano and Waveshare UGV Rover
will use later. The default mode cannot accidentally move real motors.

## Run on Windows (simulation)

```powershell
cd C:\Users\jantz\OneDrive\Documents\GitHub\robot-friend
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
# Put your OPENAI_API_KEY in .env if you want /chat and /respond.
python brain\server.py
```

Open `http://127.0.0.1:8000`. Hardware/state endpoints work without an API key.

Run verification:

```powershell
python -m unittest discover -s tests -v
```

Quick motion test (simulation only):

```powershell
Invoke-RestMethod http://127.0.0.1:8000/motion -Method Post `
  -ContentType 'application/json' -Body '{"linear":0.25,"angular":0,"duration":1}'
Invoke-RestMethod http://127.0.0.1:8000/safety/stop -Method Post `
  -ContentType 'application/json' -Body '{"reason":"manual test"}'
Invoke-RestMethod http://127.0.0.1:8000/safety/reset -Method Post
```

## Voice output

On Windows, an existing `OPENAI_API_KEY` automatically enables real speech through
OpenAI and plays it through the default PC speakers. The older `ROBOT_AUDIO=sim`
value is upgraded automatically when a key is present. Set `ROBOT_AUDIO=silent`
to deliberately disable playback, or start the local Kokoro service and set
`ROBOT_AUDIO=kokoro` to use that service instead.

## Architecture

- `brain/server.py`: conversation, personality, persistent memory, and HTTP API
- `robot/interfaces.py`: motion, sensor, camera, display, and audio contracts
- `robot/runtime.py`: state, motion validation, obstacle checks, and stop latch
- `robot/orchestrator.py`: continuous sensor/safety loop and future autonomy
- `robot/drivers/simulated.py`: complete Windows/test implementations
- `robot/drivers/jetson.py`: isolated Waveshare integration point
- `robot/state.py`: thread-safe source of truth shared by face, brain, and body

## Phase 1 Jetson hookup

1. Confirm the exact UGV Rover control board/firmware and its official Python API.
2. Implement only `WaveshareMotion` and `WaveshareSensors` in
   `robot/drivers/jetson.py`; keep speed values normalized from `-1.0` to `1.0`.
3. Add camera/display/audio classes implementing the matching interfaces.
4. Add a `jetson` branch in `robot/factory.py`, then set
   `ROBOT_HARDWARE=jetson` on the Orin Nano.
5. Test wheels raised off the ground, verify `/safety/stop`, then test at low speed.

The Jetson mode deliberately fails closed until those vendor-specific calls are
implemented. This prevents a typo or missing library from silently selecting a
partially working real-motor configuration.

## API

- `GET /health`, `GET /state`, `GET /vision`
- `POST /motion` with `linear`, `angular`, and optional `duration` (max 10 sec)
- `POST /motion/stop`, `POST /safety/stop`, `POST /safety/reset`
- `POST /face` with `expression` and optional `message`
- `POST /tts`, `POST /speech/stop`, `POST /speech/event`
- `POST /chat` (text response), `POST /respond` (response plus speech)
- `GET /memories`

An emergency stop is latched: movement stays disabled until `/safety/reset`
succeeds. A bumper press or obstacle inside 20 cm also triggers the latch.
