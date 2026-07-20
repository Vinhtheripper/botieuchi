import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from fastapi import HTTPException
from app import database
from app.database import init_db, row, connect
from app.excel_import import import_workbook
from app.main import Start, Answer, AnswerBatch, start, next_question, survey_manifest, answer, answer_batch, previous_question, password_hash, password_ok, startup

class SurveySecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory()
        database.DB_PATH=Path(cls.tmp.name)/"test.db"
        init_db();import_workbook()

    @classmethod
    def tearDownClass(cls): cls.tmp.cleanup()

    def new_session(self): return start(Start(name="Test",consent=True))["id"]

    def test_password_is_scrypt_hashed(self):
        hashed=password_hash("admin123")
        self.assertTrue(hashed.startswith("scrypt$"))
        self.assertTrue(password_ok("admin123",hashed))
        self.assertFalse(password_ok("wrong",hashed))

    def test_hidden_scores_never_reach_browser(self):
        q=next_question(self.new_session())["question"]
        self.assertNotIn("scores",q["options"][0])
        self.assertEqual(q["variables"],[])

    def test_answer_cannot_be_overwritten_or_sent_out_of_order(self):
        sid=self.new_session();q=next_question(sid)["question"]
        answer(sid,Answer(question_id=q["id"],option_id=q["options"][0]["id"],value="Tester"))
        replay=answer(sid,Answer(question_id=q["id"],option_id=q["options"][0]["id"],value="Tester"))
        self.assertTrue(replay["replayed"])
        with self.assertRaises(HTTPException): answer(sid,Answer(question_id=q["id"],option_id="B",value="Changed"))
        with self.assertRaises(HTTPException): answer(sid,Answer(question_id="Q18",option_id="A"))

    def test_question_timing_is_recorded(self):
        sid=self.new_session();q=next_question(sid)["question"]
        answer(sid,Answer(question_id=q["id"],option_id=q["options"][0]["id"],value="Tester"))
        timing=row("SELECT * FROM question_timing WHERE respondent_id=? AND question_id=?",(sid,q["id"]))
        self.assertIsNotNone(timing["answered_at"]);self.assertGreaterEqual(timing["duration_ms"],0)

    def test_user_can_go_back_and_replace_last_answer(self):
        sid=self.new_session();q=next_question(sid)["question"]
        answer(sid,Answer(question_id=q["id"],option_id=q["options"][0]["id"],value="Tên cũ"))
        previous=previous_question(sid)["next"]["question"]
        self.assertEqual(previous["id"],q["id"])
        answer(sid,Answer(question_id=q["id"],option_id=q["options"][0]["id"],value="Tên mới"))
        saved=row("SELECT value_json FROM answers WHERE respondent_id=? AND question_id=?",(sid,q["id"]))
        self.assertEqual(saved["value_json"],'"Tên mới"')

    def test_public_manifest_and_batch_sync(self):
        sid=self.new_session();manifest=survey_manifest(sid)
        self.assertGreater(len(manifest["questions"]),20)
        self.assertNotIn("scores",manifest["questions"][0]["options"][0])
        first=next_question(sid)["question"]
        result=answer_batch(sid,AnswerBatch(answers=[Answer(question_id=first["id"],option_id=first["options"][0]["id"],value="Batch")]))
        self.assertEqual(result["accepted"],1);self.assertEqual(result["next"]["answered"],1)

    def test_batch_uses_one_remote_checkpoint(self):
        sid=self.new_session();first=next_question(sid)["question"]
        with patch("app.main.commit_checkpoint",return_value={"replayed":False,"revision":1}) as checkpoint:
            result=answer_batch(sid,AnswerBatch(answers=[Answer(question_id=first["id"],option_id=first["options"][0]["id"],value="Checkpoint")]))
        self.assertEqual(result["accepted"],1)
        checkpoint.assert_called_once()

    def test_startup_does_not_scan_firestore(self):
        with patch("app.main.initialize_firebase"), patch("app.main.project_session") as lazy_loader:
            startup()
        lazy_loader.assert_not_called()
        self.assertIsNotNone(row("SELECT value FROM settings WHERE key='last_import'"))

    def test_batch_checkpoint_is_idempotent(self):
        sid=self.new_session();first=next_question(sid)["question"]
        body=AnswerBatch(revision=1,idempotency_key="stable-key",answers=[Answer(question_id=first["id"],option_id=first["options"][0]["id"],value="Một lần")])
        first_result=answer_batch(sid,body);second_result=answer_batch(sid,body)
        self.assertTrue(first_result["committed"]);self.assertEqual(second_result,first_result)
        self.assertEqual(row("SELECT COUNT(*) AS n FROM answers WHERE respondent_id=?",(sid,))["n"],1)

    def test_pilot_mode_disables_heuristic_skip(self):
        with connect() as con:con.execute("INSERT OR REPLACE INTO settings VALUES('pilot_mode','true')")
        sid=self.new_session();seen=[]
        for _ in range(40):
            nxt=next_question(sid)
            if nxt["done"]:break
            q=nxt["question"];seen.append(q["id"]);answer(sid,Answer(question_id=q["id"],option_id=q["options"][0]["id"],value="T" if q["id"]=="P00" else None))
        self.assertIn("Q09",seen);self.assertIn("Q12",seen);self.assertIn("Q18",seen)
        with connect() as con:con.execute("INSERT OR REPLACE INTO settings VALUES('pilot_mode','false')")

if __name__ == "__main__": unittest.main()
