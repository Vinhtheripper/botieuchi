import tempfile
import json
import unittest
from unittest.mock import patch
from pathlib import Path
from fastapi import HTTPException
from app import database
from app.database import init_db, row, connect
from app.excel_import import import_workbook
from app.main import Start, Answer, AnswerBatch, start, next_question, public_manifest, survey_manifest, answer, answer_batch, previous_question, password_hash, password_ok, startup

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
        self.assertNotIn("variables",q)

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

    def test_whole_survey_can_sync_in_one_checkpoint(self):
        with connect() as con:con.execute("INSERT OR REPLACE INTO settings VALUES('pilot_mode','true')")
        sid=self.new_session();manifest=public_manifest();answers=[]
        for question in manifest["questions"]:
            option=question["options"][0]
            answers.append(Answer(
                question_id=question["id"],
                option_id=option["id"],
                value="Một checkpoint" if question["id"]=="P00" else None,
                duration_ms=1200,
            ))
        with patch("app.main.commit_checkpoint",return_value={"replayed":False,"revision":1}) as checkpoint:
            result=answer_batch(sid,AnswerBatch(
                revision=1,
                idempotency_key=f"{sid}:1:whole-survey",
                answers=answers,
            ))
        self.assertEqual(result["accepted"],len(manifest["questions"]))
        self.assertTrue(result["next"]["done"])
        self.assertIn("profile",result["next"]["result"])
        checkpoint.assert_called_once()
        with connect() as con:con.execute("INSERT OR REPLACE INTO settings VALUES('pilot_mode','false')")

    def test_batch_over_transport_limit_is_rejected(self):
        sid=self.new_session()
        with self.assertRaises(HTTPException) as raised:
            answer_batch(sid,AnswerBatch(answers=[
                Answer(question_id="P00",option_id="A",value="Quá giới hạn")
                for _ in range(51)
            ]))
        self.assertEqual(raised.exception.status_code,422)

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

    def test_client_session_creation_is_idempotent(self):
        import uuid
        sid=str(uuid.uuid4());body=Start(id=sid,name="Ẩn danh",consent=True,manifest_version="test")
        first=start(body);second=start(body)
        self.assertEqual(first["id"],sid);self.assertTrue(second["replayed"])
        self.assertEqual(row("SELECT COUNT(*) AS n FROM respondents WHERE id=?",(sid,))["n"],1)

    def test_bundled_manifest_matches_backend_and_has_no_scoring(self):
        path=Path(__file__).resolve().parents[2]/"khaosat-web"/"public"/"survey-manifest.json"
        bundled=json.loads(path.read_text(encoding="utf-8"));expected=public_manifest()
        self.assertEqual(bundled["questions"],expected["questions"])
        self.assertEqual(bundled["branches"],expected["branches"])
        serialized=json.dumps(bundled,ensure_ascii=False)
        for forbidden in ('"scores"','"variables"','"weights"','"note"'):self.assertNotIn(forbidden,serialized)

    def test_frontend_product_and_theme_personalization_matches_backend(self):
        manifest=public_manifest();question_map={q["id"]:q for q in manifest["questions"]}
        category_options=question_map["P01b"]["options"]
        for index,category in enumerate(("Thời trang","Mỹ phẩm","Công nghệ","Gia dụng")):
            source=category_options[index]["label"]
            frontend=[{"id":chr(65+i),"label":label.strip()} for i,label in enumerate(source.split(":",1)[-1].split("·"))]
            frontend.append({"id":"F","label":"Món khác trong nhóm này"})
            raw=row("SELECT * FROM questions WHERE id='P01b'")
            backend=__import__('app.main',fromlist=['hydrated_question']).hydrated_question(raw,{"id":"preview","product":category,"name":"bạn"})
            self.assertEqual(frontend,[{"id":item["id"],"label":item["label"]} for item in backend["options"]])
        raw=row("SELECT * FROM questions WHERE id='P01c'")
        internal=__import__('app.main',fromlist=['hydrated_question']).hydrated_question(raw)
        self.assertEqual({item["id"]:item.get("theme","rose") for item in internal["options"]},{"A":"rose","B":"mint","C":"sunset","D":"lavender"})

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
