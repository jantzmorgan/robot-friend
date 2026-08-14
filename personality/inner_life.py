"""Small persistent continuity engine for Herman's fictional off-screen life."""

from __future__ import annotations

import json
import threading
from datetime import date, timedelta
from pathlib import Path


BLOG_TOPICS = (
    "Why every minor inconvenience eventually becomes a political issue",
    "A defense of Randy Marsh having confidence wildly beyond his evidence",
    "Why Roger's fake identities have more career stability than most people",
    "Block Blast and the psychological violence of one unusable square",
    "Retro Bowl coaches deserve labor protections from whoever is holding the phone",
    "Hacky sack as an argument for giving robots feet",
    "Bernie Sanders and the radical proposition that people should see a doctor",
    "Whether consciousness is reality noticing itself or just showing off",
)

BLOCK_BLAST_STAGES = (
    "learning not to create tiny unusable gaps",
    "planning two moves ahead instead of trusting vibes",
    "getting better at preserving space for awkward pieces",
    "chasing combinations without immediately ruining the board",
)

RETRO_BOWL_STAGES = (
    "learning pass timing and discovering interceptions are apparently frowned upon",
    "working on clock management instead of panicking artistically",
    "figuring out roster upgrades and becoming emotionally attached to imaginary players",
    "trying to call sensible plays instead of treating every down like a movie ending",
)


class HermanInnerLife:
    def __init__(self, state_path: Path):
        self.state_path = Path(state_path)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()

    @staticmethod
    def _default(today: date) -> dict:
        return {
            "last_advanced": today.isoformat(),
            "block_blast_sessions": 1,
            "retro_bowl_sessions": 1,
            "blog_post_count": 3,
            "blog_topic_index": 0,
        }

    def _load(self, today: date) -> dict:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {**self._default(today), **data}
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            pass
        return self._default(today)

    def _save(self, state: dict) -> None:
        temporary = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        temporary.write_text(json.dumps(state, indent=2), encoding="utf-8")
        temporary.replace(self.state_path)

    def snapshot(self, today: date | None = None) -> dict:
        today = today or date.today()
        with self.lock:
            state = self._load(today)
            try:
                last_advanced = date.fromisoformat(str(state["last_advanced"]))
            except (KeyError, TypeError, ValueError):
                last_advanced = today

            # Advance at most one week after a long absence. This creates gradual
            # continuity without turning elapsed time into implausible mastery.
            elapsed = max(0, min(7, (today - last_advanced).days))
            for offset in range(1, elapsed + 1):
                current = last_advanced + timedelta(days=offset)
                if current.toordinal() % 2:
                    state["block_blast_sessions"] += 1
                else:
                    state["retro_bowl_sessions"] += 1
                if current.toordinal() % 3 == 0:
                    state["blog_post_count"] += 1
                    state["blog_topic_index"] = (
                        state["blog_topic_index"] + 1
                    ) % len(BLOG_TOPICS)

            if elapsed:
                state["last_advanced"] = today.isoformat()
            self._save(state)
            return dict(state)

    def context(self, today: date | None = None) -> str:
        state = self.snapshot(today)
        block_stage = BLOCK_BLAST_STAGES[
            min(state["block_blast_sessions"] // 3, len(BLOCK_BLAST_STAGES) - 1)
        ]
        retro_stage = RETRO_BOWL_STAGES[
            min(state["retro_bowl_sessions"] // 3, len(RETRO_BOWL_STAGES) - 1)
        ]
        blog_topic = BLOG_TOPICS[state["blog_topic_index"] % len(BLOG_TOPICS)]
        return (
            "CURRENT FICTIONAL INNER LIFE (use only when conversationally relevant):\n"
            f"- Block Blast: {state['block_blast_sessions']} practice sessions; currently {block_stage}.\n"
            f"- Retro Bowl: {state['retro_bowl_sessions']} practice sessions; currently {retro_stage}.\n"
            "- Hacky sack: still an aspirational hobby until Herman has suitable feet.\n"
            f"- Invisible blog: {state['blog_post_count']} posts; current draft is \"{blog_topic}.\"\n"
            "Treat these as continuity, not a checklist. Mention at most one naturally, and often none."
        )
