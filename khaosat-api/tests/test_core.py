import tempfile
import unittest
from pathlib import Path
from fastapi import HTTPException
from app import database
from app.database import init_db, row, connect
from app.excel_import import import_workbook
from app.main import Start, Answer, start, next_question, answer, password_hash, password_ok

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
