import json
import os
import threading
import logging
from datetime import datetime, timezone
from pathlib import Path

from firebase_admin import firestore
from google.api_core.retry import Retry

from .database import connect


BACKEND = os.getenv("DATA_BACKEND", "sqlite").strip().lower()
BACKUP_PATH = Path(os.getenv("JSONL_BACKUP_PATH", Path(__file__).resolve().parents[1] / "backups" / "survey-events.jsonl"))
_backup_lock = threading.Lock()
FIRESTORE_TIMEOUT = float(os.getenv("FIRESTORE_TIMEOUT_SECONDS", "8"))
FIRESTORE_RETRY = Retry(deadline=FIRESTORE_TIMEOUT)
logger = logging.getLogger(__name__)


class DuplicateAnswer(Exception):
    pass


def firestore_enabled():
    return BACKEND == "firestore"


def _client():
    return firestore.client()


def _backup(event_type, payload):
    try:
        BACKUP_PATH.parent.mkdir(parents=True, exist_ok=True)
        record = {"event": event_type, "recorded_at": datetime.now(timezone.utc).isoformat(), "data": payload}
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        with _backup_lock, BACKUP_PATH.open("a", encoding="utf-8") as output:
            output.write(line)
            output.flush()
        BACKUP_PATH.chmod(0o600)
    except OSError:
        logger.exception("Không thể ghi JSONL backup")


def persist_session(record):
    if firestore_enabled():
        _client().collection("survey_sessions").document(record["id"]).create(record, retry=FIRESTORE_RETRY, timeout=FIRESTORE_TIMEOUT)
    _backup("session.created", record)


def persist_answer(sid, record):
    if firestore_enabled():
        ref = _client().collection("survey_sessions").document(sid)
        ref.update({f'answers.{record["question_id"]}': record}, retry=FIRESTORE_RETRY, timeout=FIRESTORE_TIMEOUT)
    _backup("answer.created", {"respondent_id": sid, **record})
    return True


def persist_answer_batch(sid, records, session_updates=None):
    """Persist one client checkpoint with exactly one Firestore document write."""
    if not records and not session_updates:
        return
    if firestore_enabled():
        updates = {f'answers.{record["question_id"]}': record for record in records}
        updates.update(session_updates or {})
        _client().collection("survey_sessions").document(sid).update(updates, retry=FIRESTORE_RETRY, timeout=FIRESTORE_TIMEOUT)
    for record in records:
        _backup("answer.created", {"respondent_id": sid, **record})
    if session_updates:
        _backup("session.updated", {"id": sid, **session_updates})


def commit_checkpoint(sid, records, session_updates, revision, idempotency_key):
    """Commit a retry-safe checkpoint with one atomic Firestore document write.

    Revision and replay checks are performed against the local projection by the
    API route before this function is called. All answers, session metadata,
    revision and the deterministic checkpoint marker are still written atomically
    to the same Firestore document. Avoiding a read-before-write transaction
    removes Firestore retry contention from the participant request path.
    """
    if not firestore_enabled():
        return {"replayed": False, "revision": revision}
    ref = _client().collection("survey_sessions").document(sid)
    updates = {f'answers.{record["question_id"]}': record for record in records}
    updates.update(session_updates or {})
    updates["revision"] = revision
    updates[f"checkpoints.{idempotency_key}"] = revision
    ref.update(updates, retry=FIRESTORE_RETRY, timeout=FIRESTORE_TIMEOUT)
    for record in records:
        _backup("answer.created", {"respondent_id": sid, **record})
    _backup("checkpoint.committed", {"id": sid, "revision": revision, "idempotency_key": idempotency_key})
    return {"replayed": False, "revision": revision}


def load_session(sid):
    """Read only one session on demand; never scan the whole collection at boot."""
    if not firestore_enabled():
        return None
    snapshot = _client().collection("survey_sessions").document(sid).get(retry=FIRESTORE_RETRY, timeout=FIRESTORE_TIMEOUT)
    return snapshot.to_dict() if snapshot.exists else None


def project_session(sid):
    data = load_session(sid)
    if not data:
        return None
    with connect() as con:
        con.execute("""INSERT OR REPLACE INTO respondents
            (id,name,email,consent,product,platform,theme,started_at,completed_at,status,revision)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)""",(sid,data.get("name"),data.get("email"),int(data.get("consent",True)),data.get("product"),data.get("platform"),data.get("theme","rose"),data.get("started_at"),data.get("completed_at"),data.get("status","active"),int(data.get("revision") or 0)))
        for qid,item in (data.get("answers") or {}).items():
            con.execute("""INSERT OR REPLACE INTO answers
                (respondent_id,question_id,option_id,value_json,scores_json,answered_at)
                VALUES(?,?,?,?,?,?)""",(sid,qid,item["option_id"],json.dumps(item.get("value"),ensure_ascii=False),json.dumps(item.get("scores",{})),item.get("answered_at")))
        for qid,item in (data.get("skipped") or {}).items():
            con.execute("INSERT OR REPLACE INTO skipped VALUES(?,?,?,?,?)",(sid,qid,json.dumps(item.get("variables",[])),item.get("reason"),item.get("created_at")))
    return data


def project_recent_sessions(limit=500):
    """Hydrate a bounded admin read page, never an unbounded startup scan."""
    if not firestore_enabled():
        return 0
    snapshots = (_client().collection("survey_sessions")
        .order_by("started_at", direction=firestore.Query.DESCENDING)
        .limit(max(1, min(int(limit), 1000))).stream(timeout=FIRESTORE_TIMEOUT))
    projected = 0
    for snapshot in snapshots:
        # project_session performs one extra read, so project the already fetched
        # payload directly through the same local upsert path via a small helper.
        data=snapshot.to_dict();sid=snapshot.id
        with connect() as con:
            con.execute("""INSERT OR REPLACE INTO respondents
                (id,name,email,consent,product,platform,theme,started_at,completed_at,status,revision)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)""",(sid,data.get("name"),data.get("email"),int(data.get("consent",True)),data.get("product"),data.get("platform"),data.get("theme","rose"),data.get("started_at"),data.get("completed_at"),data.get("status","active"),int(data.get("revision") or 0)))
            for qid,item in (data.get("answers") or {}).items():
                con.execute("""INSERT OR REPLACE INTO answers
                    (respondent_id,question_id,option_id,value_json,scores_json,answered_at)
                    VALUES(?,?,?,?,?,?)""",(sid,qid,item["option_id"],json.dumps(item.get("value"),ensure_ascii=False),json.dumps(item.get("scores",{})),item.get("answered_at")))
        projected+=1
    return projected


def persist_session_update(sid, values):
    if firestore_enabled():
        _client().collection("survey_sessions").document(sid).set(values, merge=True, retry=FIRESTORE_RETRY, timeout=FIRESTORE_TIMEOUT)
    _backup("session.updated", {"id": sid, **values})


def persist_skip(sid, qid, variables, reason, created_at):
    record={"question_id":qid,"variables":variables,"reason":reason,"created_at":created_at}
    if firestore_enabled():
        _client().collection("survey_sessions").document(sid).update({f"skipped.{qid}":record}, retry=FIRESTORE_RETRY, timeout=FIRESTORE_TIMEOUT)
    _backup("question.skipped", {"respondent_id":sid,**record})


def rollback_remote_answer(sid, qid):
    """Remove one answer and derived skips so the route can be calculated again."""
    if firestore_enabled():
        session=_client().collection("survey_sessions").document(sid)
        session.update({f"answers.{qid}":firestore.DELETE_FIELD,"skipped":{}},retry=FIRESTORE_RETRY,timeout=FIRESTORE_TIMEOUT)
    _backup("answer.rolled_back", {"respondent_id":sid,"question_id":qid})


def restore_projection():
    """Rebuild the local read model from Firestore after an ephemeral restart."""
    if not firestore_enabled():
        return {"sessions": 0, "answers": 0}
    session_count=answer_count=0
    client=_client()
    # Firestore là nguồn chính: projection cục bộ phải phản ánh đúng dữ liệu hiện có,
    # không giữ respondent cũ sau khi collection đã được dọn.
    with connect() as con:
        con.execute("DELETE FROM question_timing")
        con.execute("DELETE FROM skipped")
        con.execute("DELETE FROM answers")
        con.execute("DELETE FROM respondents")
    for snapshot in client.collection("survey_sessions").stream():
        data=snapshot.to_dict();sid=snapshot.id
        with connect() as con:
            con.execute("""INSERT OR REPLACE INTO respondents
                (id,name,email,consent,product,platform,theme,started_at,completed_at,status)
                VALUES(?,?,?,?,?,?,?,?,?,?)""",(sid,data.get("name"),data.get("email"),int(data.get("consent",True)),data.get("product"),data.get("platform"),data.get("theme","rose"),data.get("started_at"),data.get("completed_at"),data.get("status","active")))
        session_count+=1
        with connect() as con:
            for qid,item in (data.get("answers") or {}).items():
                con.execute("""INSERT OR IGNORE INTO answers
                    (respondent_id,question_id,option_id,value_json,scores_json,answered_at)
                    VALUES(?,?,?,?,?,?)""",(sid,qid,item["option_id"],json.dumps(item.get("value"),ensure_ascii=False),json.dumps(item.get("scores",{})),item.get("answered_at")))
                answer_count+=1
            for qid,item in (data.get("skipped") or {}).items():
                con.execute("INSERT OR IGNORE INTO skipped VALUES(?,?,?,?,?)",(sid,qid,json.dumps(item.get("variables",[])),item.get("reason"),item.get("created_at")))
    return {"sessions":session_count,"answers":answer_count}
