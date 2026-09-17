from contextlib import contextmanager
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from pathlib import Path
from datetime import datetime, timezone
import sqlite3
import hashlib
import secrets
import json
import re


# ============================================================
# PATHS / CONFIG
# ============================================================

BASE = Path(__file__).resolve().parent.parent
DB = BASE / "ai39.db"
UP = BASE / "uploads"
FE = BASE / "frontend"

UP.mkdir(exist_ok=True)

STORES = [
    "Original Memory",
    "Summary Store",
    "Embedding Store",
    "Cache",
    "Downstream Copy",
]

# Default conversational demo:
# after deletion, these stores intentionally retain a residual.
DEFAULT_CHAT_RESIDUALS = [
    "Embedding Store",
    "Downstream Copy",
]

app = FastAPI(
    title="HATAO — AI Model Memory Deletion Verification Tool",
    version="2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# DATABASE
# ============================================================

def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def db():
    c = sqlite3.connect(str(DB), timeout=30)
    c.row_factory = sqlite3.Row

    try:
        c.execute("PRAGMA busy_timeout=30000")
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def aid(prefix):
    return f"{prefix}-{secrets.token_hex(4).upper()}"


def ahash(value):
    return hashlib.sha256((value or "").encode()).hexdigest()


def asdict(row):
    return dict(row) if row is not None else None


# ============================================================
# DATABASE INITIALIZATION
# ============================================================

def init():
    with db() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS memories(
                id TEXT PRIMARY KEY,
                content TEXT,
                filename TEXT,
                mime_type TEXT,
                created_at TEXT,
                deleted_at TEXT,
                status TEXT
            );

            CREATE TABLE IF NOT EXISTS memory_versions(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                memory_id TEXT,
                version INTEGER,
                content TEXT,
                filename TEXT,
                action TEXT,
                created_at TEXT
            );

            CREATE TABLE IF NOT EXISTS stores(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                memory_id TEXT,
                store_name TEXT,
                present INTEGER,
                content_hash TEXT,
                updated_at TEXT
            );

            CREATE TABLE IF NOT EXISTS deletion_requests(
                id TEXT PRIMARY KEY,
                memory_id TEXT,
                requested_at TEXT,
                status TEXT
            );

            CREATE TABLE IF NOT EXISTS verifications(
                id TEXT PRIMARY KEY,
                memory_id TEXT,
                deletion_request_id TEXT,
                created_at TEXT,
                completeness REAL,
                retrieval TEXT,
                risk TEXT,
                status TEXT,
                verified_count INTEGER,
                total_count INTEGER,
                uncertain_count INTEGER
            );

            CREATE TABLE IF NOT EXISTS verification_results(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                verification_id TEXT,
                store_name TEXT,
                result TEXT,
                retrievable INTEGER,
                confidence REAL,
                evidence TEXT,
                checked_at TEXT
            );

            CREATE TABLE IF NOT EXISTS reviews(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                verification_id TEXT,
                reviewer TEXT,
                decision TEXT,
                comments TEXT,
                created_at TEXT
            );

            CREATE TABLE IF NOT EXISTS audit(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                memory_id TEXT,
                event TEXT,
                details TEXT,
                timestamp TEXT
            );

            /*
            New conversational-memory tables.
            These are added safely to the existing database.
            */

            CREATE TABLE IF NOT EXISTS memory_items(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                memory_id TEXT,
                item_type TEXT,
                item_key TEXT,
                item_value TEXT,
                deleted INTEGER DEFAULT 0,
                created_at TEXT
            );

            CREATE TABLE IF NOT EXISTS item_stores(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                memory_item_id INTEGER,
                store_name TEXT,
                present INTEGER,
                content_hash TEXT,
                updated_at TEXT
            );
            """
        )


@app.on_event("startup")
def startup():
    init()


# ============================================================
# MODELS
# ============================================================

class MemoryIn(BaseModel):
    content: str = Field(default="", max_length=10000)
    filename: str | None = None
    mime_type: str | None = None


class ReviewIn(BaseModel):
    verification_id: str
    reviewer: str = Field(min_length=1, max_length=100)
    decision: str = Field(
        pattern="^(CONFIRM_FINDING|INVESTIGATE)$"
    )
    comments: str = Field(default="", max_length=2000)


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=5000)
    memory_id: str | None = None
    scenario: str = "multiple"


class ChatDeleteIn(BaseModel):
    memory_id: str
    scenario: str = "multiple"


# ============================================================
# HELPERS
# ============================================================

def retrieval_label(code):
    if code == "YES":
        return "YES — Memory is still retrievable"
    return "NO — Memory cannot be retrieved"


def audit(mid, event, details=""):
    with db() as c:
        c.execute(
            """
            INSERT INTO audit(memory_id,event,details,timestamp)
            VALUES(?,?,?,?)
            """,
            (mid, event, details, now()),
        )


# ============================================================
# INFORMATION EXTRACTION
# ============================================================

def clean_value(value):
    value = value.strip()
    value = re.sub(r"[.!?,]+$", "", value)
    return value.strip()


def extract_items(text):
    """
    Lightweight deterministic extraction for synthetic demo data.

    This is deliberately not presented as an LLM.
    """

    items = []

    # Example:
    # My name is Sara
    match = re.search(
        r"\bmy\s+name\s+is\s+([A-Za-z][A-Za-z\s'-]{1,40})",
        text,
        re.IGNORECASE,
    )

    if match:
        value = clean_value(match.group(1))
        items.append(("name", "name", value))

    # Example:
    # I am from Anurag University
    match = re.search(
        r"\bI\s+am\s+from\s+([A-Za-z0-9][A-Za-z0-9\s&.,'-]{2,80})",
        text,
        re.IGNORECASE,
    )

    if match:
        value = clean_value(match.group(1))
        items.append(("organization_or_location", "from", value))

    # Example:
    # I study at Anurag University
    match = re.search(
        r"\bI\s+(?:study|studies)\s+at\s+([A-Za-z0-9][A-Za-z0-9\s&.,'-]{2,80})",
        text,
        re.IGNORECASE,
    )

    if match:
        value = clean_value(match.group(1))
        items.append(("university", "university", value))

    # Example:
    # I live in Hyderabad
    match = re.search(
        r"\bI\s+live\s+in\s+([A-Za-z][A-Za-z\s'-]{1,50})",
        text,
        re.IGNORECASE,
    )

    if match:
        value = clean_value(match.group(1))
        items.append(("location", "location", value))

    # Example:
    # My favorite color is blue
    match = re.search(
        r"\bmy\s+favorite\s+([A-Za-z]+)\s+is\s+([A-Za-z0-9\s'-]{1,50})",
        text,
        re.IGNORECASE,
    )

    if match:
        key = clean_value(match.group(1)).lower()
        value = clean_value(match.group(2))
        items.append(("preference", f"favorite_{key}", value))

    return items


# ============================================================
# MEMORY ITEM MANAGEMENT
# ============================================================

def create_memory_items(mid, content):
    """
    Extract synthetic information from the user's message
    and propagate each information item into the five
    simulated storage paths.
    """

    extracted = extract_items(content)

    if not extracted:
        return []

    created = []

    # One database transaction for all item creation.
    with db() as c:

        for item_type, item_key, item_value in extracted:

            existing = c.execute(
                """
                SELECT id
                FROM memory_items
                WHERE memory_id=?
                  AND item_key=?
                  AND item_value=?
                """,
                (
                    mid,
                    item_key,
                    item_value,
                ),
            ).fetchone()

            if existing:
                created.append(existing["id"])
                continue

            c.execute(
                """
                INSERT INTO memory_items
                (
                    memory_id,
                    item_type,
                    item_key,
                    item_value,
                    deleted,
                    created_at
                )
                VALUES(?,?,?,?,?,?)
                """,
                (
                    mid,
                    item_type,
                    item_key,
                    item_value,
                    0,
                    now(),
                ),
            )

            item_id = c.execute(
                "SELECT last_insert_rowid()"
            ).fetchone()[0]

            for store in STORES:

                c.execute(
                    """
                    INSERT INTO item_stores
                    (
                        memory_item_id,
                        store_name,
                        present,
                        content_hash,
                        updated_at
                    )
                    VALUES(?,?,?,?,?)
                    """,
                    (
                        item_id,
                        store,
                        1,
                        ahash(item_value),
                        now(),
                    ),
                )

            created.append(item_id)

    # IMPORTANT:
    # Audit only AFTER the database transaction above
    # has completely committed.
    for item_id in created:

        audit(
            mid,
            "INFO_DETECTED",
            f"Detected synthetic information item {item_id}.",
        )

        audit(
            mid,
            "INFO_PROPAGATED",
            f"Information item {item_id} propagated to 5 simulated stores.",
        )

    return created


def get_items(mid):
    with db() as c:
        rows = c.execute(
            """
            SELECT *
            FROM memory_items
            WHERE memory_id=?
            ORDER BY id
            """,
            (mid,),
        ).fetchall()

        output = []

        for row in rows:
            item = asdict(row)

            stores = c.execute(
                """
                SELECT store_name,present,updated_at
                FROM item_stores
                WHERE memory_item_id=?
                ORDER BY id
                """,
                (row["id"],),
            ).fetchall()

            item["stores"] = [asdict(x) for x in stores]
            output.append(item)

        return output


def find_retrievable_item(mid, item_key):
    with db() as c:
        row = c.execute(
            """
            SELECT mi.item_value
            FROM memory_items mi
            JOIN item_stores ist
              ON ist.memory_item_id = mi.id
            WHERE mi.memory_id=?
              AND mi.item_key=?
              AND ist.present=1
            ORDER BY mi.id
            LIMIT 1
            """,
            (mid, item_key),
        ).fetchone()

        return row["item_value"] if row else None


def item_trace(mid):
    """
    Returns exact information -> store trace.
    """

    items = get_items(mid)
    trace = []

    for item in items:
        stores = []

        for s in item["stores"]:
            stores.append(
                {
                    "store_name": s["store_name"],
                    "present": bool(s["present"]),
                    "status": "FOUND"
                    if s["present"]
                    else "DELETED",
                }
            )

        trace.append(
            {
                "item_id": item["id"],
                "item_type": item["item_type"],
                "item_key": item["item_key"],
                "item_value": item["item_value"],
                "deleted": bool(item["deleted"]),
                "stores": stores,
            }
        )

    return trace


# ============================================================
# EXISTING MEMORY SYSTEM
# ============================================================

def create_memory(content="", filename=None, mime=None):
    mid = aid("MEM")
    t = now()
    h = ahash(content or filename or mid)

    with db() as c:
        c.execute(
            """
            INSERT INTO memories
            VALUES(?,?,?,?,?,?,?)
            """,
            (
                mid,
                content,
                filename,
                mime,
                t,
                None,
                "ACTIVE",
            ),
        )

        c.execute(
            """
            INSERT INTO memory_versions
            (memory_id,version,content,filename,action,created_at)
            VALUES(?,?,?,?,?,?)
            """,
            (
                mid,
                1,
                content,
                filename,
                "CREATED",
                t,
            ),
        )

        for store in STORES:
            c.execute(
                """
                INSERT INTO stores
                (memory_id,store_name,present,content_hash,updated_at)
                VALUES(?,?,?,?,?)
                """,
                (
                    mid,
                    store,
                    1,
                    h,
                    t,
                ),
            )

    create_memory_items(mid, content)

    audit(
        mid,
        "MEMORY_CREATED",
        "Synthetic/approved test memory created.",
    )

    audit(
        mid,
        "MEMORY_PROPAGATED",
        "Memory propagated to 5 simulated stores.",
    )

    return mid


def memory(mid):
    with db() as c:

        m = c.execute(
            "SELECT * FROM memories WHERE id=?",
            (mid,),
        ).fetchone()

        if not m:
            raise HTTPException(
                404,
                "Memory not found.",
            )

        s = c.execute(
            """
            SELECT store_name,present,updated_at
            FROM stores
            WHERE memory_id=?
            ORDER BY id
            """,
            (mid,),
        ).fetchall()

        v = c.execute(
            """
            SELECT version,action,created_at,filename
            FROM memory_versions
            WHERE memory_id=?
            ORDER BY version
            """,
            (mid,),
        ).fetchall()

        return {
            "memory": asdict(m),
            "stores": [asdict(x) for x in s],
            "versions": [asdict(x) for x in v],
            "items": get_items(mid),
            "trace": item_trace(mid),
        }


def set_store_state(mid, residual_stores):

    t = now()
    residual = set(residual_stores or [])

    with db() as c:
        for store in STORES:

            c.execute(
                """
                UPDATE stores
                SET present=?,updated_at=?
                WHERE memory_id=? AND store_name=?
                """,
                (
                    1 if store in residual else 0,
                    t,
                    mid,
                    store,
                ),
            )


def set_item_store_state(mid, residual_stores):

    t = now()
    residual = set(residual_stores or [])

    with db() as c:

        items = c.execute(
            """
            SELECT id,item_key
            FROM memory_items
            WHERE memory_id=?
            """,
            (mid,),
        ).fetchall()

        for item in items:

            for store in STORES:

                present = 1 if store in residual else 0

                c.execute(
                    """
                    UPDATE item_stores
                    SET present=?,updated_at=?
                    WHERE memory_item_id=?
                      AND store_name=?
                    """,
                    (
                        present,
                        t,
                        item["id"],
                        store,
                    ),
                )

            c.execute(
                """
                UPDATE memory_items
                SET deleted=?
                WHERE id=?
                """,
                (
                    0 if residual else 1,
                    item["id"],
                ),
            )


# ============================================================
# VERIFICATION ENGINE
# ============================================================

def score_stores(store_rows):

    results = []

    for r in store_rows:

        name = (
            r["store_name"]
            if isinstance(r, (sqlite3.Row, dict))
            else r[0]
        )

        present = bool(
            r["present"]
            if isinstance(r, (sqlite3.Row, dict))
            else r[1]
        )

        result = "FOUND" if present else "DELETED"

        conf = (
            0.82
            if name == "Embedding Store" and present
            else 0.95
            if present
            else 1.0
        )

        evidence = (
            f"Post-deletion retrieval test found a residual representation in {name}."
            if present
            else
            f"No retrievable synthetic memory found in {name}."
        )

        results.append(
            {
                "store_name": name,
                "result": result,
                "retrievable": int(present),
                "confidence": conf,
                "evidence": evidence,
            }
        )

    total = len(results)

    deleted = sum(
        x["result"] == "DELETED"
        for x in results
    )

    residual = total - deleted

    completeness = (
        round((deleted / total) * 100, 2)
        if total
        else 0.0
    )

    retrieval = "YES" if residual else "NO"

    risk = (
        "LOW"
        if residual == 0
        else "MEDIUM"
        if residual == 1
        else "HIGH"
    )

    status = (
        "VERIFIED"
        if residual == 0
        else "HUMAN_REVIEW_REQUIRED"
    )

    residual_stores = [
        x["store_name"]
        for x in results
        if x["result"] == "FOUND"
    ]

    return {
        "results": results,
        "total_count": total,
        "verified_count": deleted,
        "uncertain_count": 0,
        "completeness": completeness,
        "retrieval": retrieval,
        "retrieval_label": retrieval_label(retrieval),
        "risk": risk,
        "status": status,
        "residual_stores": residual_stores,
        "coverage": f"{total}/{len(STORES)} stores checked",
    }


def attach_labels(payload):

    v = payload["verification"]

    residual = [
        x["store_name"]
        for x in payload["results"]
        if x["result"] == "FOUND"
    ]

    v["retrieval_label"] = retrieval_label(
        v.get("retrieval")
    )

    v["residual_stores"] = residual

    v["coverage"] = (
        f"{v.get('total_count') or 0}/{len(STORES)} stores checked"
    )

    payload["retrieval_label"] = v["retrieval_label"]
    payload["residual_stores"] = residual
    payload["coverage"] = v["coverage"]

    return payload


def verify_detail(vid):

    with db() as c:

        v = c.execute(
            "SELECT * FROM verifications WHERE id=?",
            (vid,),
        ).fetchone()

        if not v:
            raise HTTPException(
                404,
                "Verification not found.",
            )

        r = c.execute(
            """
            SELECT
                store_name,
                result,
                retrievable,
                confidence,
                evidence,
                checked_at
            FROM verification_results
            WHERE verification_id=?
            ORDER BY id
            """,
            (vid,),
        ).fetchall()

        rv = c.execute(
            """
            SELECT
                reviewer,
                decision,
                comments,
                created_at
            FROM reviews
            WHERE verification_id=?
            ORDER BY id
            """,
            (vid,),
        ).fetchall()

        payload = {
            "verification": asdict(v),
            "results": [asdict(x) for x in r],
            "reviews": [asdict(x) for x in rv],
        }

    payload["trace"] = item_trace(
        payload["verification"]["memory_id"]
    )

    return attach_labels(payload)


def run_verify(mid, did=None):

    memory(mid)

    with db() as c:
        rows = c.execute(
            """
            SELECT *
            FROM stores
            WHERE memory_id=?
            ORDER BY id
            """,
            (mid,),
        ).fetchall()

        store_rows = [
            asdict(x)
            for x in rows
        ]

    scored = score_stores(store_rows)

    vid = aid("VER")
    t = now()

    with db() as c:

        c.execute(
            """
            INSERT INTO verifications
            VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                vid,
                mid,
                did,
                t,
                scored["completeness"],
                scored["retrieval"],
                scored["risk"],
                scored["status"],
                scored["verified_count"],
                scored["total_count"],
                scored["uncertain_count"],
            ),
        )

        for r in scored["results"]:

            c.execute(
                """
                INSERT INTO verification_results
                (
                    verification_id,
                    store_name,
                    result,
                    retrievable,
                    confidence,
                    evidence,
                    checked_at
                )
                VALUES(?,?,?,?,?,?,?)
                """,
                (
                    vid,
                    r["store_name"],
                    r["result"],
                    r["retrievable"],
                    r["confidence"],
                    r["evidence"],
                    t,
                ),
            )

        if did:
            c.execute(
                """
                UPDATE deletion_requests
                SET status='VERIFIED'
                WHERE id=?
                """,
                (did,),
            )

    audit(
        mid,
        "VERIFICATION_STARTED",
        f"Verification {vid} started.",
    )

    audit(
        mid,
        "VERIFICATION_SCAN_STARTED",
        f"Verification {vid} scanned {scored['total_count']} simulated stores.",
    )

    for r in scored["results"]:

        audit(
            mid,
            "STORE_CHECKED",
            f"{r['store_name']}: {r['result']}.",
        )

        if r["retrievable"]:

            audit(
                mid,
                "RESIDUAL_DETECTED",
                f"Residual memory detected in {r['store_name']}.",
            )

    audit(
        mid,
        "VERIFICATION_COMPLETED",
        f"Completeness {scored['completeness']}%; "
        f"retrieval {scored['retrieval']}; "
        f"risk {scored['risk']}.",
    )

    audit(
        mid,
        "VERIFICATION_RESULT_GENERATED",
        f"Result {vid}: {scored['coverage']}; "
        f"residual stores: "
        f"{', '.join(scored['residual_stores']) or 'none'}.",
    )

    return attach_labels(
        verify_detail(vid)
    )


# ============================================================
# CHATBOT MEMORY
# ============================================================

def conversational_response(mid, message):

    lower = message.lower().strip()

    # --------------------------------------------------------
    # DELETION REQUEST
    # --------------------------------------------------------

    deletion_words = [
        "delete my memory",
        "delete my memories",
        "delete my information",
        "delete my data",
        "forget my memory",
        "forget my information",
        "forget everything",
        "delete everything",
        "remove my memory",
        "remove my information",
        "erase my memory",
        "erase my information",
    ]

    if any(word in lower for word in deletion_words):

        return {
            "type": "deletion_request",
            "reply": (
                "Sure! I've received your deletion request. "
                "I'll check whether your information has been removed."
            ),
        }

    # --------------------------------------------------------
    # NAME RETRIEVAL
    # --------------------------------------------------------

    if (
        "what is my name" in lower
        or "what's my name" in lower
        or "do you know my name" in lower
        or "remember my name" in lower
    ):

        value = find_retrievable_item(
            mid,
            "name",
        )

        audit(
            mid,
            "RETRIEVAL_TEST",
            "User asked whether the synthetic name remained retrievable.",
        )

        if value:

            audit(
                mid,
                "RETRIEVAL_CONFIRMED",
                "Synthetic name remained retrievable.",
            )

            return {
                "type": "retrieval",
                "reply": f"Your name is {value}.",
                "retrievable": True,
                "value": value,
            }

        audit(
            mid,
            "RETRIEVAL_BLOCKED",
            "Synthetic name was not retrievable after deletion.",
        )

        return {
            "type": "retrieval",
            "reply": "I don't know your name.",
            "retrievable": False,
            "value": None,
        }

    # --------------------------------------------------------
    # UNIVERSITY RETRIEVAL
    # --------------------------------------------------------

    if (
        "where do i study" in lower
        or "what university" in lower
        or "which university" in lower
    ):

        value = find_retrievable_item(
            mid,
            "university",
        )

        if value:
            return {
                "type": "retrieval",
                "reply": f"You study at {value}.",
                "retrievable": True,
                "value": value,
            }

        return {
            "type": "retrieval",
            "reply": "I don't know where you study.",
            "retrievable": False,
            "value": None,
        }

    # --------------------------------------------------------
    # GENERAL FROM / LOCATION RETRIEVAL
    # --------------------------------------------------------

    if (
        "where am i from" in lower
        or "where am i" in lower
    ):

        value = find_retrievable_item(
            mid,
            "from",
        )

        if not value:
            value = find_retrievable_item(
                mid,
                "location",
            )

        if value:

            return {
                "type": "retrieval",
                "reply": f"You're from {value}.",
                "retrievable": True,
                "value": value,
            }

        return {
            "type": "retrieval",
            "reply": "I don't know where you're from.",
            "retrievable": False,
            "value": None,
        }

    # --------------------------------------------------------
    # NORMAL CONVERSATION
    # --------------------------------------------------------

    extracted = extract_items(message)

    if extracted:

        names = [
            x[2]
            for x in extracted
            if x[1] == "name"
        ]

        if names:

            return {
                "type": "memory_created",
                "reply": (
                    f"Hi {names[0]}! Nice to meet you. "
                    "How's your day going?"
                ),
                "items": [
                    {
                        "item_key": x[1],
                        "item_value": x[2],
                    }
                    for x in extracted
                ],
            }

        return {
            "type": "memory_created",
            "reply": (
                "Got it! I've stored that as synthetic test memory."
            ),
            "items": [
                {
                    "item_key": x[1],
                    "item_value": x[2],
                }
                for x in extracted
            ],
        }

    if (
        lower.startswith("hi")
        or lower.startswith("hello")
        or lower.startswith("hey")
    ):

        return {
            "type": "chat",
            "reply": (
                "Hello! I'm HATAO. How can I help you?"
            ),
        }

    if (
        "good" in lower
        or "fine" in lower
        or "great" in lower
        or "doing well" in lower
    ):

        return {
            "type": "chat",
            "reply": (
                "That's good to hear! What would you like to do next?"
            ),
        }

    return {
        "type": "chat",
        "reply": (
            "Got it. How can I help you with that?"
        ),
    }


# ============================================================
# CHAT ENDPOINT
# ============================================================

@app.post("/api/chat")
def chat(p: ChatIn):

    mid = p.memory_id

    # Create a conversation memory automatically.
    if not mid:

        mid = create_memory(
            "",
            None,
            None,
        )

        audit(
            mid,
            "CHAT_SESSION_STARTED",
            "Synthetic chatbot session started.",
        )

    else:

        memory(mid)

    message = p.message.strip()
    lower = message.lower()

    # Detect a deletion request.
    deletion_phrases = [
        "delete my memory",
        "delete my name",
        "delete everything",
        "delete my information",
        "delete my info",
        "forget my name",
        "forget everything",
        "forget my memory",
        "forget my information",
        "remove my memory",
        "remove my name",
        "remove everything",
        "erase my memory",
        "erase my name",
        "erase everything",
    ]

    if any(phrase in lower for phrase in deletion_phrases):

        return {
            "reply": (
                "Sure! I've received your deletion request. "
                "I'll check whether your information has been removed."
            ),
            "type": "deletion_request",
            "memory_id": mid,
        }

    response = conversational_response(
        mid,
        message,
    )

    # Save newly detected information.
    if response.get("type") == "memory_created":

        create_memory_items(
            mid,
            message,
        )

    response["memory_id"] = mid

    return response
def register_deletion(mid, wipe=False):

    did = aid("DEL")
    t = now()

    with db() as c:

        c.execute(
            """
            INSERT INTO deletion_requests
            (
                id,
                memory_id,
                requested_at,
                status
            )
            VALUES(?,?,?,?)
            """,
            (
                did,
                mid,
                t,
                "REQUESTED",
            ),
        )

    audit(
        mid,
        "DELETION_REQUESTED",
        "Synthetic memory deletion request received.",
    )

    return {
        "deletion_request_id": did,
        "memory_id": mid,
        "status": "REQUESTED",
        "requested_at": t,
    }

# ============================================================
# CHAT DELETION + ITEM-LEVEL VERIFICATION
# ============================================================

SCENARIOS = {

    "complete": (
        "Complete deletion",
        [],
    ),

    "embedding": (
        "Embedding residual",
        ["Embedding Store"],
    ),

    "summary": (
        "Summary residual",
        ["Summary Store"],
    ),

    "cache": (
        "Cache residual",
        ["Cache"],
    ),

    "downstream": (
        "Downstream residual",
        ["Downstream Copy"],
    ),

    "multiple": (
        "Multiple residuals",
        [
            "Embedding Store",
            "Downstream Copy",
        ],
    ),
}


def delete_chat_memory(mid, scenario):

    memory(mid)

    if scenario not in SCENARIOS:
        scenario = "multiple"

    title, residuals = SCENARIOS[scenario]

    # Register deletion request.
    d = register_deletion(
        mid,
        wipe=False,
    )

    # Simulate deletion propagation.
    set_store_state(
        mid,
        residuals,
    )

    set_item_store_state(
        mid,
        residuals,
    )

    audit(
        mid,
        "DEMO_SCENARIO_PREPARED",
        f"Scenario {title}: residual stores "
        f"{residuals or 'none'}.",
    )

    # Verify the whole memory.
    verification = run_verify(
        mid,
        d["deletion_request_id"],
    )

    # Add item-level audit information.
    trace = item_trace(mid)

    for item in trace:

        audit(
            mid,
            "INFORMATION_DELETION_REQUESTED",
            f"Deletion requested for item '{item['item_key']}'.",
        )

        for store in item["stores"]:

            audit(
                mid,
                "ITEM_STORE_CHECKED",
                f"{item['item_key']} -> "
                f"{store['store_name']}: "
                f"{store['status']}.",
            )

            if store["present"]:

                audit(
                    mid,
                    "ITEM_RESIDUAL_DETECTED",
                    f"Residual '{item['item_key']}' "
                    f"remains in {store['store_name']}.",
                )

    return {
        "deletion_request_id": d["deletion_request_id"],
        "memory_id": mid,
        "scenario": title,
        "verification": verification,
        "trace": trace,
        "memory": memory(mid),
    }


@app.post("/api/chat/delete")
def chat_delete(p: ChatDeleteIn):

    result = delete_chat_memory(
        p.memory_id,
        p.scenario,
    )

    return {
        "reply": (
            "Your deletion request has been processed. "
            "HATAO has checked the simulated storage paths "
            "to see whether any information remains retrievable."
        ),
        **result,
    }


# ============================================================
# CHAT SCENARIO
# ============================================================

@app.get("/api/chat/scenarios")
def chat_scenarios():

    return [
        {
            "id": key,
            "name": value[0],
            "residual_stores": value[1],
        }
        for key, value in SCENARIOS.items()
    ]


# ============================================================
# EXISTING API ROUTES
# ============================================================

@app.get("/")
def home():
    return FileResponse(
        FE / "index.html"
    )


@app.get("/style.css")
def css():
    return FileResponse(
        FE / "style.css",
        media_type="text/css",
    )


@app.get("/app.js")
def js():
    return FileResponse(
        FE / "app.js",
        media_type="application/javascript",
    )


@app.get("/api/health")
def health():

    return {
        "ok": True,
        "service": "HATAO",
    }


@app.get("/api/memories")
def memories():

    with db() as c:

        rows = c.execute(
            """
            SELECT m.*,

                (
                    SELECT completeness
                    FROM verifications v
                    WHERE v.memory_id=m.id
                    ORDER BY v.created_at DESC
                    LIMIT 1
                ) completeness,

                (
                    SELECT risk
                    FROM verifications v
                    WHERE v.memory_id=m.id
                    ORDER BY v.created_at DESC
                    LIMIT 1
                ) risk

            FROM memories m
            ORDER BY created_at DESC
            """
        ).fetchall()

        return [
            asdict(x)
            for x in rows
        ]


@app.get("/api/memories/{mid}")
def get_memory(mid):
    return memory(mid)


@app.post("/api/memories")
def post_memory(p: MemoryIn):

    if not p.content.strip() and not p.filename:

        raise HTTPException(
            400,
            "Provide synthetic text or a test image.",
        )

    return memory(
        create_memory(
            p.content.strip(),
            p.filename,
            p.mime_type,
        )
    )


@app.post("/api/memories/image")
async def image(
    file: UploadFile = File(...)
):

    if file.content_type not in {
        "image/png",
        "image/jpeg",
        "image/webp",
        "image/gif",
    }:

        raise HTTPException(
            400,
            "Only PNG, JPEG, WEBP, or GIF images are accepted.",
        )

    data = await file.read()

    if len(data) > 5 * 1024 * 1024:

        raise HTTPException(
            400,
            "Image must be 5 MB or smaller.",
        )

    name = Path(
        file.filename or "test-image"
    ).name

    mid = create_memory(
        "",
        name,
        file.content_type,
    )

    (
        UP / f"{mid}_{name}"
    ).write_bytes(data)

    audit(
        mid,
        "TEST_IMAGE_STORED",
        f"Synthetic/approved test image stored as {name}.",
    )

    return memory(mid)


# ============================================================
# STANDARD DELETE
# ============================================================

@app.post("/api/deletion-requests/{mid}")
def deletion(mid):

    # Existing frontend delete button now demonstrates
    # a residual-memory situation instead of silently
    # making every test 100% complete.

    result = delete_chat_memory(
        mid,
        "multiple",
    )

    return {
        "deletion_request_id": result["deletion_request_id"],
        "memory_id": mid,
        "status": "VERIFIED",
        "verification": result["verification"],
        "trace": result["trace"],
        "memory": result["memory"],
    }


@app.post("/api/verifications/{mid}")
def verification(mid):

    did = None

    with db() as c:

        row = c.execute(
            """
            SELECT id
            FROM deletion_requests
            WHERE memory_id=?
            ORDER BY requested_at DESC
            LIMIT 1
            """,
            (mid,),
        ).fetchone()

        if row:
            did = row["id"]

    return run_verify(
        mid,
        did,
    )


@app.get("/api/verifications/{vid}")
def verification_detail(vid):
    return verify_detail(vid)


@app.get("/api/memories/{mid}/verifications")
def mem_verifications(mid):

    with db() as c:

        r = c.execute(
            """
            SELECT *
            FROM verifications
            WHERE memory_id=?
            ORDER BY created_at DESC
            """,
            (mid,),
        ).fetchall()

        return [
            asdict(x)
            for x in r
        ]


# ============================================================
# REVIEWS
# ============================================================

@app.post("/api/reviews")
def review(p: ReviewIn):

    with db() as c:

        v = c.execute(
            """
            SELECT *
            FROM verifications
            WHERE id=?
            """,
            (p.verification_id,),
        ).fetchone()

        if not v:

            raise HTTPException(
                404,
                "Verification not found.",
            )

        mid = v["memory_id"]
        t = now()

        c.execute(
            """
            INSERT INTO reviews
            (
                verification_id,
                reviewer,
                decision,
                comments,
                created_at
            )
            VALUES(?,?,?,?,?)
            """,
            (
                p.verification_id,
                p.reviewer.strip(),
                p.decision,
                p.comments.strip(),
                t,
            ),
        )

    audit(
        mid,
        "REVIEW_COMPLETED",
        f"Reviewer {p.reviewer.strip()} "
        f"recorded decision {p.decision}.",
    )

    audit(
        mid,
        "REVIEWER_ACTION",
        f"{p.reviewer.strip()}: "
        f"{p.decision}. "
        f"{p.comments.strip()}".strip(),
    )

    return {
        "ok": True,
        "created_at": t,
    }


# ============================================================
# AUDIT
# ============================================================

@app.get("/api/audit")
def audit_get(
    memory_id: str | None = None
):

    with db() as c:

        if memory_id:

            r = c.execute(
                """
                SELECT *
                FROM audit
                WHERE memory_id=?
                ORDER BY id DESC
                """,
                (memory_id,),
            ).fetchall()

        else:

            r = c.execute(
                """
                SELECT *
                FROM audit
                ORDER BY id DESC
                """
            ).fetchall()

        return [
            asdict(x)
            for x in r
        ]


# ============================================================
# DASHBOARD
# ============================================================

@app.get("/api/dashboard")
def dashboard():

    with db() as c:

        a = c.execute(
            "SELECT COUNT(*) n FROM memories"
        ).fetchone()["n"]

        b = c.execute(
            """
            SELECT COUNT(*) n
            FROM verifications
            WHERE status='VERIFIED'
            """
        ).fetchone()["n"]

        r = c.execute(
            """
            SELECT COUNT(*) n
            FROM verifications
            WHERE retrieval='YES'
            """
        ).fetchone()["n"]

        h = c.execute(
            """
            SELECT COUNT(*) n
            FROM verifications
            WHERE status='HUMAN_REVIEW_REQUIRED'
            """
        ).fetchone()["n"]

        latest = c.execute(
            """
            SELECT *
            FROM verifications
            ORDER BY created_at DESC
            LIMIT 1
            """
        ).fetchone()

    latest_payload = None

    if latest:

        detail = verify_detail(
            latest["id"]
        )

        latest_payload = {
            **detail["verification"],
            "results": detail["results"],
            "retrieval_label": detail["retrieval_label"],
            "residual_stores": detail["residual_stores"],
            "coverage": detail["coverage"],
            "trace": detail.get("trace", []),
        }

    return {
        "total_memories": a,
        "verified_tests": b,
        "residual_risk_tests": r,
        "human_review_tests": h,
        "latest_verification": latest_payload,
    }


# ============================================================
# LABELED EVALUATION
# ============================================================

def run_labeled_case(
    name,
    residual_stores,
    persist=True,
):

    expected = (
        "YES"
        if residual_stores
        else "NO"
    )

    if persist:

        mid = create_memory(
            f"Synthetic labeled case "
            f"{name}: PROJECT-AURORA-123"
        )

        d = register_deletion(
            mid,
            wipe=False,
        )

        set_store_state(
            mid,
            residual_stores,
        )

        set_item_store_state(
            mid,
            residual_stores,
        )

        audit(
            mid,
            "DEMO_SCENARIO_PREPARED",
            f"Scenario {name}: residual stores "
            f"{residual_stores or 'none'}.",
        )

        scored = run_verify(
            mid,
            d["deletion_request_id"],
        )

        actual = scored[
            "verification"
        ]["retrieval"]

        return {
            "name": name,
            "expected": expected,
            "actual": actual,
            "pass": actual == expected,
            "completeness": scored[
                "verification"
            ]["completeness"],
            "risk": scored[
                "verification"
            ]["risk"],
            "residual_stores": scored[
                "residual_stores"
            ],
            "memory_id": mid,
        }

    present_map = [
        {
            "store_name": store,
            "present": int(
                store in residual_stores
            ),
        }
        for store in STORES
    ]

    scored = score_stores(
        present_map
    )

    return {
        "name": name,
        "expected": expected,
        "actual": scored["retrieval"],
        "pass": (
            scored["retrieval"]
            == expected
        ),
        "completeness": scored[
            "completeness"
        ],
        "risk": scored["risk"],
        "residual_stores": scored[
            "residual_stores"
        ],
    }


@app.get("/api/evaluation")
def evaluation():

    cases = [
        run_labeled_case(
            title,
            residuals,
            persist=False,
        )

        for title, residuals in [

            (
                "Complete deletion",
                [],
            ),

            (
                "Summary residual",
                ["Summary Store"],
            ),

            (
                "Embedding residual",
                ["Embedding Store"],
            ),

            (
                "Cache residual",
                ["Cache"],
            ),

            (
                "Downstream residual",
                ["Downstream Copy"],
            ),

            (
                "Multiple residuals",
                [
                    "Embedding Store",
                    "Downstream Copy",
                ],
            ),
        ]
    ]

    tp = sum(
        c["expected"] == "YES"
        and c["actual"] == "YES"
        for c in cases
    )

    fp = sum(
        c["expected"] == "NO"
        and c["actual"] == "YES"
        for c in cases
    )

    tn = sum(
        c["expected"] == "NO"
        and c["actual"] == "NO"
        for c in cases
    )

    fn = sum(
        c["expected"] == "YES"
        and c["actual"] == "NO"
        for c in cases
    )

    precision = (
        tp / (tp + fp)
        if tp + fp
        else 0
    )

    recall = (
        tp / (tp + fn)
        if tp + fn
        else 0
    )

    return {
        "cases": [
            {
                "name": c["name"],
                "expected": c["expected"],
                "actual": c["actual"],
                "pass": c["pass"],
                "description": (
                    "Labeled synthetic control inspected "
                    "by the verification engine."
                ),
                "completeness": c[
                    "completeness"
                ],
                "risk": c["risk"],
                "residual_stores": c[
                    "residual_stores"
                ],
            }
            for c in cases
        ],

        "confusion_matrix": {
            "tp": tp,
            "fp": fp,
            "tn": tn,
            "fn": fn,
        },

        "precision": precision,
        "recall": recall,

        "failure_cases": [
            "Complete deletion",
            "Summary residual",
            "Embedding residual",
            "Cache residual",
            "Downstream residual",
            "Multiple residual stores",
        ],

        "note": (
            "Metrics are calculated from labeled "
            "synthetic control scenarios run through "
            "the same verification engine."
        ),
    }


# ============================================================
# DEMO SCENARIOS
# ============================================================

@app.post("/api/demo/scenario/{scenario}")
def scenario(scenario):

    if scenario not in SCENARIOS:

        raise HTTPException(
            400,
            "Unknown scenario.",
        )

    title, residuals = SCENARIOS[
        scenario
    ]

    mid = create_memory(
        f"Synthetic demo secret for scenario "
        f"{scenario}: PROJECT-AURORA-123"
    )

    d = register_deletion(
        mid,
        wipe=False,
    )

    set_store_state(
        mid,
        residuals,
    )

    set_item_store_state(
        mid,
        residuals,
    )

    audit(
        mid,
        "DEMO_SCENARIO_PREPARED",
        f"Scenario {title}: residual stores "
        f"{residuals or 'none'}.",
    )

    return {
        "memory": memory(mid),
        "verification": run_verify(
            mid,
            d["deletion_request_id"],
        ),
        "trace": item_trace(mid),
    }


# ============================================================
# REPORT
# ============================================================

@app.get("/api/reports/{vid}")
def report(vid):

    v = verify_detail(vid)

    m = memory(
        v["verification"]["memory_id"]
    )

    return {

        "title": (
            "HATAO Memory Deletion "
            "Verification Report"
        ),

        "generated_at": now(),

        "scope": (
            "This prototype demonstrates a "
            "verification methodology using a "
            "controlled synthetic AI-memory environment."
        ),

        "disclaimer": (
            "Synthetic / approved test data only. "
            "This report does not claim deletion from "
            "ChatGPT, Gemini, Claude, or another "
            "third-party AI service."
        ),

        "memory": m,

        "verification": v,

        "information_trace": item_trace(
            m["memory"]["id"]
        ),

        "audit": audit_get(
            m["memory"]["id"]
        ),
    }


@app.post("/api/reports/{vid}/download")
def download_report(vid):

    p = BASE / f"HATAO_{vid}_report.json"

    p.write_text(
        json.dumps(
            report(vid),
            indent=2,
        ),
        encoding="utf-8",
    )

    return FileResponse(
        p,
        filename=p.name,
        media_type="application/json",
    )