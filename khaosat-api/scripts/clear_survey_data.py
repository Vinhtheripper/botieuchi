#!/usr/bin/env python3
"""Delete survey responses while preserving admin/configuration data."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

load_dotenv()

from app.database import connect, init_db
from app.firebase import initialize_firebase


def clear_local():
    init_db()
    with connect() as con:
        counts = {
            table: con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("respondents", "answers", "skipped", "question_timing")
        }
        con.execute("DELETE FROM question_timing")
        con.execute("DELETE FROM skipped")
        con.execute("DELETE FROM answers")
        con.execute("DELETE FROM respondents")
    return counts


def clear_firestore():
    initialize_firebase()
    from firebase_admin import firestore

    client = firestore.client()
    collection = client.collection("survey_sessions")
    # recursive_delete also removes legacy answer/skipped subcollections.
    client.recursive_delete(collection)
    return {"collection": "survey_sessions", "remaining": len(list(collection.limit(1).stream()))}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--local-only", action="store_true")
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args()
    if not args.yes:
        raise SystemExit("Refusing destructive cleanup without --yes")
    result = {"local_deleted": clear_local()}
    if not args.local_only:
        try:
            result["firestore"] = clear_firestore()
        except Exception as exc:
            result["firestore"] = {"ok": False, "error": type(exc).__name__, "detail": str(exc)}
            print(json.dumps(result, ensure_ascii=False, indent=2))
            raise SystemExit(2)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
