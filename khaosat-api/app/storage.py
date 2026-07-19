import json
import os
import threading
import logging
from datetime import datetime, timezone
from pathlib import Path

from firebase_admin import firestore

from .database import connect


BACKEND = os.getenv("DATA_BACKEND", "sqlite").strip().lower()
BACKUP_PATH = Path(os.getenv("JSONL_BACKUP_PATH", Path(__file__).resolve().parents[1] / "backups" / "survey-events.jsonl"))
_backup_lock = threading.Lock()
FIRESTORE_TIMEOUT = float(os.getenv("FIRESTORE_TIMEOUT_SECONDS", "8"))
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
        _client().collection("survey_sessions").document(record["id"]).create(record, timeout=FIRESTORE_TIMEOUT)
    _backup("session.created", record)


def persist_answer(sid, record):
    if firestore_enabled():
        ref = _client().collection("survey_sessions").document(sid).collection("answers").document(record["question_id"])
        try:
            ref.create(record, timeout=FIRESTORE_TIMEOUT)
        except Exception as exc:
            if "AlreadyExists" in type(exc).__name__ or "already exists" in str(exc).lower():
                existing=ref.get(timeout=FIRESTORE_TIMEOUT).to_dict() or {}
                if existing.get("option_id")==record.get("option_id") and existing.get("value")==record.get("value"):
                    return False
                raise DuplicateAnswer from exc
            raise
    _backup("answer.created", {"respondent_id": sid, **record})
    return True


def persist_session_update(sid, values):
    if firestore_enabled():
        _client().collection("survey_sessions").document(sid).set(values, merge=True, timeout=FIRESTORE_TIMEOUT)
    _backup("session.updated", {"id": sid, **values})


def persist_skip(sid, qid, variables, reason, created_at):
    record={"question_id":qid,"variables":variables,"reason":reason,"created_at":created_at}
    if firestore_enabled():
        _client().collection("survey_sessions").document(sid).collection("skipped").document(qid).set(record, timeout=FIRESTORE_TIMEOUT)
    _backup("question.skipped", {"respondent_id":sid,**record})


def rollback_remote_answer(sid, qid):
    """Remove one answer and derived skips so the route can be calculated again."""
    if firestore_enabled():
        session=_client().collection("survey_sessions").document(sid)
        session.collection("answers").document(qid).delete(timeout=FIRESTORE_TIMEOUT)
        for snapshot in session.collection("skipped").stream():
            snapshot.reference.delete(timeout=FIRESTORE_TIMEOUT)
    _backup("answer.rolled_back", {"respondent_id":sid,"question_id":qid})


def restore_projection():
    """Rebuild the local read model from Firestore after an ephemeral restart."""
    if not firestore_enabled():
        return {"sessions": 0, "answers": 0}
    session_count=answer_count=0
    client=_client()
    for snapshot in client.collection("survey_sessions").stream():
        data=snapshot.to_dict();sid=snapshot.id
        with connect() as con:
            con.execute("""INSERT OR REPLACE INTO respondents
                (id,name,email,consent,product,platform,theme,started_at,completed_at,status)
                VALUES(?,?,?,?,?,?,?,?,?,?)""",(sid,data.get("name"),data.get("email"),int(data.get("consent",True)),data.get("product"),data.get("platform"),data.get("theme","rose"),data.get("started_at"),data.get("completed_at"),data.get("status","active")))
        session_count+=1
    for answer in client.collection_group("answers").stream():
        item=answer.to_dict();sid=answer.reference.parent.parent.id
        with connect() as con:
            con.execute("""INSERT OR IGNORE INTO answers
                (respondent_id,question_id,option_id,value_json,scores_json,answered_at)
                VALUES(?,?,?,?,?,?)""",(sid,item["question_id"],item["option_id"],json.dumps(item.get("value"),ensure_ascii=False),json.dumps(item.get("scores",{})),item.get("answered_at")))
        answer_count+=1
    for skipped in client.collection_group("skipped").stream():
        item=skipped.to_dict();sid=skipped.reference.parent.parent.id
        with connect() as con:
            con.execute("INSERT OR IGNORE INTO skipped VALUES(?,?,?,?,?)",(sid,item["question_id"],json.dumps(item.get("variables",[])),item.get("reason"),item.get("created_at")))
    return {"sessions":session_count,"answers":answer_count}
