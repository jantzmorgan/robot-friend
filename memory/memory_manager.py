import math
import re
import sqlite3
import threading

from datetime import (
    datetime,
    timezone
)

from pathlib import Path


# ============================================================
# ROBOT MEMORY
#
# Persistent local memory stored in SQLite.
#
# This first version intentionally does NOT require embeddings,
# vector databases, cloud storage, or another dependency.
#
# Later we can upgrade retrieval to semantic embeddings without
# changing the rest of Robot Friend's brain.
# ============================================================


STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "been",
    "but",
    "by",
    "can",
    "could",
    "did",
    "do",
    "does",
    "for",
    "from",
    "had",
    "has",
    "have",
    "he",
    "her",
    "him",
    "his",
    "how",
    "i",
    "if",
    "in",
    "is",
    "it",
    "its",
    "just",
    "me",
    "my",
    "of",
    "on",
    "or",
    "our",
    "she",
    "so",
    "that",
    "the",
    "their",
    "them",
    "they",
    "this",
    "to",
    "was",
    "we",
    "were",
    "what",
    "when",
    "where",
    "which",
    "who",
    "why",
    "will",
    "with",
    "would",
    "you",
    "your"
}


# ============================================================
# HELPERS
# ============================================================

def utc_now():

    return (
        datetime
        .now(
            timezone.utc
        )
        .isoformat()
    )


def normalize_text(
    text
):

    text = (
        text
        .strip()
        .lower()
    )


    text = re.sub(
        r"\s+",
        " ",
        text
    )


    text = re.sub(
        r"[^\w\s'-]",
        "",
        text
    )


    return text


def tokenize(
    text
):

    words = re.findall(
        r"[a-zA-Z0-9']+",
        text.lower()
    )


    return {
        word
        for word in words
        if (
            len(word) >= 3
            and
            word not in STOP_WORDS
        )
    }


# ============================================================
# MEMORY MANAGER
# ============================================================

class RobotMemory:

    def __init__(
        self,
        database_path
    ):

        self.database_path = (
            Path(
                database_path
            )
        )


        self.database_path.parent.mkdir(
            parents=True,
            exist_ok=True
        )


        self.write_lock = (
            threading.Lock()
        )


        self._initialize_database()


    # ========================================================
    # DATABASE CONNECTION
    # ========================================================

    def _connect(
        self
    ):

        connection = sqlite3.connect(
            self.database_path,
            timeout=10
        )


        connection.row_factory = (
            sqlite3.Row
        )


        connection.execute(
            "PRAGMA journal_mode=WAL"
        )


        connection.execute(
            "PRAGMA synchronous=NORMAL"
        )


        connection.execute(
            "PRAGMA busy_timeout=10000"
        )


        return connection


    # ========================================================
    # INITIALIZE DATABASE
    # ========================================================

    def _initialize_database(
        self
    ):

        with self._connect() as connection:

            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS memories (

                    id INTEGER PRIMARY KEY AUTOINCREMENT,

                    subject TEXT NOT NULL
                        DEFAULT 'primary_user',

                    category TEXT NOT NULL
                        DEFAULT 'general',

                    memory_text TEXT NOT NULL,

                    normalized_text TEXT NOT NULL,

                    importance REAL NOT NULL
                        DEFAULT 0.5,

                    created_at TEXT NOT NULL,

                    updated_at TEXT NOT NULL,

                    last_used_at TEXT,

                    use_count INTEGER NOT NULL
                        DEFAULT 0,

                    source TEXT NOT NULL
                        DEFAULT 'conversation',

                    UNIQUE(
                        subject,
                        normalized_text
                    )
                )
                """
            )


            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS
                idx_memories_subject
                ON memories(subject)
                """
            )


            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS
                idx_memories_category
                ON memories(category)
                """
            )


            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS
                idx_memories_importance
                ON memories(importance)
                """
            )


        print(
            "Robot memory database online:",
            self.database_path
        )


    # ========================================================
    # REMEMBER
    # ========================================================

    def remember(
        self,
        memory_text,
        category="general",
        importance=0.5,
        subject="primary_user",
        source="conversation"
    ):

        memory_text = (
            str(memory_text)
            .strip()
        )


        if not memory_text:

            return False


        normalized = (
            normalize_text(
                memory_text
            )
        )


        if len(normalized) < 4:

            return False


        try:

            importance = float(
                importance
            )

        except Exception:

            importance = 0.5


        importance = max(
            0.0,
            min(
                1.0,
                importance
            )
        )


        category = (
            str(category)
            .strip()
            .lower()
            or
            "general"
        )


        subject = (
            str(subject)
            .strip()
            or
            "primary_user"
        )


        now = utc_now()


        with self.write_lock:

            with self._connect() as connection:

                existing = (
                    connection.execute(
                        """
                        SELECT
                            id,
                            importance
                        FROM memories
                        WHERE
                            subject = ?
                            AND
                            normalized_text = ?
                        """,
                        (
                            subject,
                            normalized
                        )
                    )
                    .fetchone()
                )


                if existing:

                    new_importance = max(
                        float(
                            existing[
                                "importance"
                            ]
                        ),
                        importance
                    )


                    connection.execute(
                        """
                        UPDATE memories
                        SET
                            memory_text = ?,
                            category = ?,
                            importance = ?,
                            updated_at = ?,
                            source = ?
                        WHERE id = ?
                        """,
                        (
                            memory_text,
                            category,
                            new_importance,
                            now,
                            source,
                            existing[
                                "id"
                            ]
                        )
                    )


                    print(
                        "MEMORY UPDATED:",
                        memory_text
                    )


                    return True


                connection.execute(
                    """
                    INSERT INTO memories (

                        subject,
                        category,
                        memory_text,
                        normalized_text,
                        importance,
                        created_at,
                        updated_at,
                        source

                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        subject,
                        category,
                        memory_text,
                        normalized,
                        importance,
                        now,
                        now,
                        source
                    )
                )


        print(
            "MEMORY SAVED:",
            memory_text
        )


        return True


    # ========================================================
    # RELEVANT MEMORY RETRIEVAL
    # ========================================================

    def search(
        self,
        query,
        subject="primary_user",
        limit=6
    ):

        query_tokens = (
            tokenize(
                query
            )
        )


        with self._connect() as connection:

            rows = (
                connection.execute(
                    """
                    SELECT
                        *
                    FROM memories
                    WHERE subject = ?
                    ORDER BY
                        importance DESC,
                        updated_at DESC
                    LIMIT 250
                    """,
                    (
                        subject,
                    )
                )
                .fetchall()
            )


        if not rows:

            return []


        scored = []


        now = (
            datetime
            .now(
                timezone.utc
            )
        )


        for row in rows:

            memory_tokens = (
                tokenize(
                    row[
                        "memory_text"
                    ]
                )
            )


            overlap = (
                query_tokens
                &
                memory_tokens
            )


            overlap_count = (
                len(
                    overlap
                )
            )


            if query_tokens:

                overlap_ratio = (
                    overlap_count
                    /
                    max(
                        1,
                        len(
                            query_tokens
                        )
                    )
                )

            else:

                overlap_ratio = 0.0


            importance = float(
                row[
                    "importance"
                ]
            )


            # ------------------------------------------------
            # RECENCY
            # ------------------------------------------------

            try:

                updated = (
                    datetime
                    .fromisoformat(
                        row[
                            "updated_at"
                        ]
                    )
                )


                age_days = max(
                    0.0,
                    (
                        now -
                        updated
                    )
                    .total_seconds()
                    /
                    86400
                )

            except Exception:

                age_days = 365.0


            recency = (
                1.0
                /
                (
                    1.0
                    +
                    age_days
                    /
                    30.0
                )
            )


            usage = math.log1p(
                int(
                    row[
                        "use_count"
                    ]
                )
            )


            score = (

                overlap_count
                * 3.0

                +

                overlap_ratio
                * 4.0

                +

                importance
                * 1.8

                +

                recency
                * 0.5

                +

                usage
                * 0.08
            )


            # ------------------------------------------------
            # Strong memories can remain available even when
            # the current query has weak keyword overlap.
            # ------------------------------------------------

            if (
                overlap_count > 0
                or
                importance >= 0.88
            ):

                scored.append(
                    (
                        score,
                        row
                    )
                )


        scored.sort(
            key=lambda item:
                item[0],
            reverse=True
        )


        selected = [
            row
            for _, row
            in scored[
                :limit
            ]
        ]


        if selected:

            selected_ids = [
                row[
                    "id"
                ]
                for row
                in selected
            ]


            placeholders = (
                ",".join(
                    "?"
                    for _
                    in selected_ids
                )
            )


            with self.write_lock:

                with self._connect() as connection:

                    connection.execute(
                        f"""
                        UPDATE memories
                        SET
                            last_used_at = ?,
                            use_count =
                                use_count + 1
                        WHERE id IN (
                            {placeholders}
                        )
                        """,
                        (
                            utc_now(),
                            *selected_ids
                        )
                    )


        return [
            {
                "id":
                    row[
                        "id"
                    ],

                "text":
                    row[
                        "memory_text"
                    ],

                "category":
                    row[
                        "category"
                    ],

                "importance":
                    float(
                        row[
                            "importance"
                        ]
                    )
            }

            for row in selected
        ]


    # ========================================================
    # FORMAT MEMORY CONTEXT
    # ========================================================

    def get_context(
        self,
        query,
        subject="primary_user",
        limit=6
    ):

        memories = self.search(
            query=query,
            subject=subject,
            limit=limit
        )


        if not memories:

            return """
LONG-TERM MEMORY:

No relevant long-term memories were retrieved for this turn.
"""


        lines = [
            "LONG-TERM MEMORY:",
            "",
            "These are real stored memories from previous interactions.",
            "Use them naturally when relevant.",
            "Do not mention that they came from a database.",
            ""
        ]


        for memory in memories:

            lines.append(
                f"- {memory['text']}"
            )


        return "\n".join(
            lines
        )


    # ========================================================
    # DEBUG / VIEW RECENT MEMORIES
    # ========================================================

    def recent(
        self,
        limit=20,
        subject="primary_user"
    ):

        with self._connect() as connection:

            rows = (
                connection.execute(
                    """
                    SELECT
                        *
                    FROM memories
                    WHERE subject = ?
                    ORDER BY
                        updated_at DESC
                    LIMIT ?
                    """,
                    (
                        subject,
                        limit
                    )
                )
                .fetchall()
            )


        return [
            dict(
                row
            )
            for row in rows
        ]