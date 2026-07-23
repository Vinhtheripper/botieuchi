import csv, io, json, os, uuid, hashlib, secrets, time, shutil, logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Union
from fastapi import FastAPI, HTTPException, Header, Response, Depends, Request, UploadFile, File, Form
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from firebase_admin import storage as firebase_storage
from urllib.parse import quote
from dotenv import load_dotenv
from .database import init_db, connect, rows, row, decode, DB_PATH
from .excel_import import import_workbook

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
from .firebase import initialize_firebase
from .storage import DuplicateAnswer, commit_checkpoint, firestore_enabled, persist_answer, persist_session, persist_session_update, persist_skip, project_recent_sessions, project_session, rollback_remote_answer

ALLOWED_ORIGINS=[origin.strip() for origin in os.getenv("ALLOWED_ORIGINS","http://localhost:5173").split(",") if origin.strip()]
app = FastAPI(title="GROUP2 Survey API", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=ALLOWED_ORIGINS, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
app.add_middleware(GZipMiddleware, minimum_size=1000, compresslevel=5)
MEDIA_DIR=Path(__file__).resolve().parents[1]/"media"; MEDIA_DIR.mkdir(exist_ok=True)
BACKUP_DIR=Path(__file__).resolve().parents[1]/"backups"; BACKUP_DIR.mkdir(exist_ok=True)
app.mount("/media",StaticFiles(directory=MEDIA_DIR),name="media")
RATE_BUCKETS={}
RATE_LAST_CLEANUP=0.0
ANALYTICS_CACHE={"expires":0.0,"value":None}
logger=logging.getLogger(__name__)

def now(): return datetime.now(timezone.utc).isoformat()
def participant_name(sid, value=None):
    cleaned=(value or "").strip()
    return cleaned if cleaned and cleaned.lower() not in ("bạn","ẩn danh") else f"Người tham gia #{sid[:6].upper()}"
def password_hash(password,salt=None):
    salt=salt or secrets.token_hex(16); digest=hashlib.scrypt(password.encode(),salt=bytes.fromhex(salt),n=2**14,r=8,p=1).hex()
    return f"scrypt${salt}${digest}"
def password_ok(password,stored):
    try:
        _,salt,_=stored.split('$'); return secrets.compare_digest(password_hash(password,salt),stored)
    except Exception:return False
def admin(authorization: Optional[str] = Header(None)):
    if not authorization or not authorization.startswith("Bearer "): raise HTTPException(401,"Bạn chưa đăng nhập")
    token=authorization[7:]; hashed=hashlib.sha256(token.encode()).hexdigest()
    user=row("SELECT u.* FROM admin_sessions s JOIN admin_users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires_at>? AND u.active=1",(hashed,now()))
    if not user: raise HTTPException(401,"Phiên đăng nhập hết hạn")
    return user
def owner(user=Depends(admin)):
    if user["role"]!="owner": raise HTTPException(403,"Chỉ Owner được thực hiện thao tác này")
    return user
def editor(user=Depends(admin)):
    if user["role"] not in ("owner","editor"): raise HTTPException(403,"Bạn chỉ có quyền xem dữ liệu")
    return user
def audit(user,action,resource,resource_id=None,detail=None,ip=None):
    with connect() as con: con.execute("INSERT INTO audit_logs(admin_id,action,resource,resource_id,detail_json,ip,created_at) VALUES(?,?,?,?,?,?,?)",(user.get("id") if user else None,action,resource,resource_id,json.dumps(detail or {},ensure_ascii=False),ip,now()))

@app.middleware("http")
async def rate_limit(request:Request,call_next):
    global RATE_BUCKETS, RATE_LAST_CLEANUP
    if request.url.path.startswith(("/api/sessions","/api/admin/login")):
        current=time.time();parts=request.url.path.strip('/').split('/')
        ip=request.headers.get("x-forwarded-for","").split(',')[0].strip() or (request.client.host if request.client else "unknown")
        if len(parts)>=3 and parts[1]=="sessions": key=("session",parts[2]);limit=120
        elif parts[-1]=="login": key=("admin-login",ip);limit=10
        else:key=("session-create",ip);limit=int(os.getenv("SESSION_CREATE_RATE_LIMIT_PER_MINUTE","60"))
        bucket=[x for x in RATE_BUCKETS.get(key,[]) if current-x<60]
        if len(bucket)>=limit:return Response(content='{"detail":"Thao tác quá nhanh, vui lòng thử lại sau"}',status_code=429,media_type="application/json")
        bucket.append(current);RATE_BUCKETS[key]=bucket
        if current-RATE_LAST_CLEANUP>300:
            RATE_BUCKETS={k:[x for x in values if current-x<60] for k,values in RATE_BUCKETS.items() if any(current-x<60 for x in values)}
            RATE_LAST_CLEANUP=current
    return await call_next(request)

@app.on_event("startup")
def startup():
    init_db()
    initialize_firebase()
    if not row("SELECT id FROM admin_users LIMIT 1"):
        username=os.getenv("ADMIN_USERNAME","admin")
        password=os.getenv("ADMIN_PASSWORD","admin123")
        with connect() as con: con.execute("INSERT INTO admin_users VALUES(?,?,?,?,?,?)",(str(uuid.uuid4()),username,password_hash(password),"owner",1,now()))
    if not row("SELECT value FROM settings WHERE key='last_import'"): import_workbook()

@app.get("/api/health")
def health(): return {"ok": True}

class Login(BaseModel): username:str="admin"; password:str
@app.post("/api/admin/login")
def admin_login(body:Login,request:Request):
    user=row("SELECT * FROM admin_users WHERE username=? AND active=1",(body.username,))
    if not user or not password_ok(body.password,user["password_hash"]): raise HTTPException(401,"Sai tài khoản hoặc mật khẩu")
    token=secrets.token_urlsafe(32); expires=datetime.now(timezone.utc)+timedelta(hours=12)
    with connect() as con: con.execute("INSERT INTO admin_sessions VALUES(?,?,?,?)",(hashlib.sha256(token.encode()).hexdigest(),user["id"],expires.isoformat(),now()))
    audit(user,"login","auth",ip=request.client.host if request.client else None)
    return {"token":token,"user":{"username":user["username"],"role":user["role"]},"expires_at":expires.isoformat()}

class Start(BaseModel):
    id: Optional[str] = None
    name: str = "Ẩn danh"
    email: Optional[str] = None
    consent: bool = True
    started_at: Optional[str] = None
    manifest_version: Optional[str] = None

class Answer(BaseModel):
    question_id: str
    option_id: str
    value: Optional[Union[dict, str, int]] = None
    duration_ms: Optional[int] = None

class AnswerBatch(BaseModel):
    answers: list[Answer]
    revision: Optional[int] = None
    idempotency_key: Optional[str] = None

@app.post("/api/sessions")
def start(body: Start):
    sid = body.id or str(uuid.uuid4())
    try:uuid.UUID(sid)
    except ValueError:raise HTTPException(422,"Session ID không hợp lệ")
    existing=row("SELECT id FROM respondents WHERE id=?",(sid,))
    if existing:return {"id":sid,"replayed":True}
    created_at=body.started_at or now();name=participant_name(sid,body.name);record={"id":sid,"name":name,"email":body.email,"consent":bool(body.consent),"theme":"rose","started_at":created_at,"completed_at":None,"status":"active","manifest_version":body.manifest_version,"revision":0,"checkpoints":{},"answers":{},"skipped":{}}
    try:
        persist_session(record)
    except Exception:
        # UUID do client tạo gần như luôn là phiên mới. Chỉ đọc Firestore khi
        # create báo trùng/lỗi để giữ idempotency sau cold start, tránh một
        # network round-trip cho mọi người tham gia mới.
        if firestore_enabled():
            try:
                if project_session(sid):return {"id":sid,"replayed":True}
            except Exception:logger.exception("Không thể khôi phục session %s sau create thất bại",sid)
        raise
    with connect() as con: con.execute("INSERT OR IGNORE INTO respondents(id,name,email,consent,theme,started_at,status) VALUES(?,?,?,?,?,?,?)",(sid,name,body.email,int(body.consent),"rose",created_at,"active"))
    return {"id": sid,"replayed":False}

def hydrated_question(q, respondent=None):
    q=decode(q,"variables_json","options_json")
    q["variables"],q["options"]=q.pop("variables_json"),q.pop("options_json")
    media_key=q["id"]
    if respondent:
        respondent_name=respondent.get("name") or "bạn"
        if respondent_name.startswith("Người tham gia #") or respondent_name.lower() in ("ẩn danh","bạn"):respondent_name="bạn"
        q["text"] = (q["text"].replace("{PRODUCT}",respondent.get("product") or "sản phẩm quen thuộc")
            .replace("{PLATFORM}",respondent.get("platform") or "nền tảng bạn thường dùng")
            .replace("{NAME}",respondent_name))
        if q["id"] == "P01b" and respondent.get("product"):
            category = respondent["product"]
            names = ["Thời trang", "Mỹ phẩm", "Công nghệ", "Gia dụng"]
            index = next((i for i, name in enumerate(names) if name.lower() in category.lower()), 0)
            source = q["options"][min(index, len(q["options"])-1)]["label"]
            products = source.split(":", 1)[-1].split("·")
            q["options"] = [{"id": chr(65+i), "label": name.strip(), "scores": {}} for i, name in enumerate(products)]
            q["options"].append({"id": "F", "label": "Món khác trong nhóm này", "scores": {}})
            media_key=f"P01b:{index}"
        if q["id"]=="T01":
            values={}
            for item in rows("SELECT scores_json FROM answers WHERE respondent_id=?",(respondent["id"],)):
                for key,value in json.loads(item["scores_json"] or "{}").items():values.setdefault(key,[]).append(float(value))
            cautious=avg({k:sum(v)/len(v) for k,v in values.items()},"REVQ","PRIV","INFO","SCAM")
            q["text"]="Có vẻ bạn khá kỹ trước khi chốt đơn — bạn thường dừng lại để đọc thêm tín hiệu." if cautious>=3.5 else "Bạn có vẻ khá cởi mở với những trải nghiệm mua sắm mới — trực giác thường lên tiếng khá nhanh."
        if q["id"]=="T02":q["text"]=f"Giờ thử đặt lựa chọn đó vào {respondent.get('product') or 'món đồ bạn quan tâm'} trên {respondent.get('platform') or 'nền tảng quen thuộc'} nhé. Từ đây, tình huống là của riêng bạn."
        if q["id"] in {f"Q{i}" for i in range(13,19)}:
            leads=["Điện thoại rung lên đúng lúc bạn vừa định cất ví...","Đặt mình vào khoảnh khắc thật này nhé:","Không còn là ví dụ chung nữa — đây là chiếc feed dành cho bạn:"]
            q["story_lead"]=leads[int(hashlib.sha256(f'{respondent["id"]}:{q["id"]}'.encode()).hexdigest(),16)%len(leads)]
            base=q["text"].split("]",1)[1].strip() if q["text"].startswith("[") else q["text"]
            q["text"]=f"{q['story_lead']} {base}"
    media=rows("SELECT option_id,path FROM question_media WHERE question_id IN (?,?) ORDER BY question_id DESC,id",(media_key,q["id"]))
    question_image=next((m for m in media if m["option_id"]=="__question__"),None)
    if question_image:q["image_url"]=question_image["path"] if question_image["path"].startswith("http") else "/media/"+question_image["path"]
    for option in q["options"]:
        item=next((m for m in media if m["option_id"]==str(option["id"])),None)
        if item: option["image_url"]=item["path"] if item["path"].startswith("http") else "/media/"+item["path"]
    return q

def public_question(q):
    """Không bao giờ gửi hidden-scoring xuống trình duyệt/F12."""
    safe=dict(q)
    safe["options"]=[{"id":o["id"],"label":o["label"],**({"image_url":o["image_url"]} if o.get("image_url") else {})} for o in q["options"]]
    safe.pop("variables",None)
    safe.pop("note",None)
    return safe

def should_skip(sid, q, snapshot=None):
    pilot=snapshot.get("pilot") if snapshot else row("SELECT value FROM settings WHERE key='pilot_mode'")
    if pilot=="true" or (isinstance(pilot,dict) and pilot.get("value")=="true"): return False
    protected={"PRICE","PRIV","INFO","SCAM","PI"}; variables=q["variables"]
    # P00, AC1, piping và demographic không có biến hidden-scoring: luôn hiển thị.
    if not variables: return False
    if protected.intersection(variables): return False
    answered=snapshot.get("answers",[]) if snapshot else rows("SELECT scores_json FROM answers WHERE respondent_id=?",(sid,))
    scores={v:[] for v in variables}
    for a in answered:
        for k,v in json.loads(a["scores_json"] or "{}").items():
            if k in scores: scores[k].append(float(v))
    if not all(scores[v] and sum(scores[v])/len(scores[v]) >= 4 for v in variables): return False
    previous=snapshot.get("skipped",[]) if snapshot else rows("SELECT variables_json FROM skipped WHERE respondent_id=?",(sid,))
    used={v for x in previous for v in json.loads(x["variables_json"])}
    return not used.intersection(variables)

def branch_skips(sid,qid,snapshot=None):
    rules=[x for x in snapshot.get("branches",[]) if x["target_question"]==qid] if snapshot else rows("SELECT * FROM branch_rules WHERE target_question=? AND active=1",(qid,))
    for rule in rules:
        source=snapshot.get("answer_by_question",{}).get(rule["source_question"]) if snapshot else row("SELECT option_id,value_json FROM answers WHERE respondent_id=? AND question_id=?",(sid,rule["source_question"]))
        actual=source["option_id"] if source else None; match=str(actual)==rule["expected_value"]
        if rule["operator"]=="not_equals":match=not match
        if rule["action"]=="skip" and match:return True
        if rule["action"]=="show_if" and not match:return True
    return False

def calculate_next_question(sid: str, persist_completion=True):
    respondent=row("SELECT * FROM respondents WHERE id=?",(sid,))
    if not respondent and firestore_enabled():
        try: project_session(sid)
        except Exception: logger.exception("Không thể lazy-load session %s",sid)
        respondent=row("SELECT * FROM respondents WHERE id=?",(sid,))
    if not respondent: raise HTTPException(404,"Không tìm thấy phiên")
    answer_rows=rows("SELECT question_id,option_id,value_json,scores_json FROM answers WHERE respondent_id=?",(sid,))
    skip_rows=rows("SELECT question_id,variables_json FROM skipped WHERE respondent_id=?",(sid,))
    answered={x["question_id"] for x in answer_rows}
    skipped={x["question_id"] for x in skip_rows}
    pilot=row("SELECT value FROM settings WHERE key='pilot_mode'")
    snapshot={"answers":answer_rows,"skipped":skip_rows,"pilot":pilot["value"] if pilot else None,"branches":rows("SELECT * FROM branch_rules WHERE active=1"),"answer_by_question":{x["question_id"]:x for x in answer_rows}}
    questions=rows("SELECT * FROM questions WHERE active=1 ORDER BY position")
    total=len(questions)
    for raw in questions:
        if raw["id"] in answered or raw["id"] in skipped: continue
        if branch_skips(sid,raw["id"],snapshot):
            skipped_at=now();persist_skip(sid,raw["id"],[],"Branch rule",skipped_at)
            with connect() as con: con.execute("INSERT OR IGNORE INTO skipped VALUES(?,?,?,?,?)",(sid,raw["id"],"[]","Branch rule",skipped_at))
            continue
        q=hydrated_question(raw,respondent)
        if should_skip(sid,q,snapshot):
            skipped_at=now();persist_skip(sid,q["id"],q["variables"],"Điểm trung bình tích luỹ ≥ 4",skipped_at)
            with connect() as con: con.execute("INSERT OR IGNORE INTO skipped VALUES(?,?,?,?,?)",(sid,q["id"],json.dumps(q["variables"]),"Điểm trung bình tích luỹ ≥ 4",skipped_at))
            continue
        with connect() as con: con.execute("INSERT OR IGNORE INTO question_timing(respondent_id,question_id,shown_at) VALUES(?,?,?)",(sid,q["id"],now()))
        context=None
        if q["phase"] in ("Cá nhân hoá","Chuyển chặng","Mua lặp lại","Kết phiên","Nhân khẩu học"):
            context={"product":respondent.get("product"),"platform":respondent.get("platform"),"theme":respondent.get("theme") or "rose"}
        return {"done":False,"question":public_question(q),"progress":round((len(answered)+len(skipped))/total*100),"answered":len(answered),"total":total,"context":context}
    completed_at=now()
    if persist_completion:
        persist_session_update(sid,{"completed_at":completed_at,"status":"completed"})
        with connect() as con: con.execute("UPDATE respondents SET completed_at=?,status='completed' WHERE id=?",(completed_at,sid))
    result=score_result(sid);result["name"]=respondent.get("name") if respondent.get("name") not in (None,"bạn","Ẩn danh") else None
    return {"done":True,"progress":100,"result":result,"completed_at":completed_at}

@app.get("/api/sessions/{sid}/next")
def next_question(sid: str):
    return calculate_next_question(sid)

def public_manifest():
    questions=[public_question(hydrated_question(question)) for question in rows("SELECT * FROM questions WHERE active=1 ORDER BY position")]
    branches=rows("SELECT source_question,target_question,operator,expected_value,action FROM branch_rules WHERE active=1")
    version=row("SELECT value FROM settings WHERE key='manifest_version'") or row("SELECT value FROM settings WHERE key='last_import'")
    return {"version":version["value"] if version else "1","questions":questions,"branches":branches}

def bump_manifest_version():
    version=now()
    with connect() as con:con.execute("INSERT OR REPLACE INTO settings VALUES('manifest_version',?)",(version,))
    return version

@app.get("/api/manifest")
def manifest_endpoint(response:Response):
    response.headers["Cache-Control"]="public, max-age=300, stale-while-revalidate=86400"
    return public_manifest()

@app.get("/api/sessions/{sid}/manifest")
def survey_manifest(sid:str,response:Response=None):
    if not row("SELECT id FROM respondents WHERE id=?",(sid,)):raise HTTPException(404,"Không tìm thấy phiên")
    if response is not None:response.headers["Cache-Control"]="public, max-age=300, stale-while-revalidate=86400"
    return public_manifest()

def process_answer(sid: str, body: Answer, persist_remote=True, include_next=True):
    respondent=row("SELECT * FROM respondents WHERE id=?",(sid,))
    if not respondent: raise HTTPException(404,"Không tìm thấy phiên")
    if respondent["status"] == "completed": raise HTTPException(409,"Phiên khảo sát đã khóa sau khi hoàn thành")
    existing=row("SELECT option_id,value_json,scores_json,answered_at FROM answers WHERE respondent_id=? AND question_id=?",(sid,body.question_id))
    if existing:
        previous_value=json.loads(existing["value_json"] or "null")
        if str(existing["option_id"])==str(body.option_id) and previous_value==body.value:
            record={"question_id":body.question_id,"option_id":existing["option_id"],"value":previous_value,"scores":json.loads(existing["scores_json"] or "{}"),"answered_at":existing["answered_at"],"duration_ms":body.duration_ms}
            if persist_remote:persist_answer(sid,record)
            updates={}
            existing_question=row("SELECT * FROM questions WHERE id=?",(body.question_id,))
            hydrated=hydrated_question(existing_question,respondent) if existing_question else None
            selected=next((o for o in hydrated["options"] if str(o["id"])==str(body.option_id)),None) if hydrated else None
            if body.question_id=="P00" and isinstance(previous_value,str) and previous_value.strip():updates["name"]=previous_value.strip()
            if body.question_id in ("P01a","P01b") and selected:updates["product"]=selected["label"]
            if body.question_id=="P01c" and selected:updates["theme"]=selected.get("theme","rose")
            if body.question_id=="P02" and selected:updates["platform"]=selected["label"]
            return record,updates,next_question(sid) if include_next else None,True
        raise HTTPException(409,"Đáp án đã được ghi nhận và không thể chỉnh sửa")
    expected=next_question(sid)
    if expected.get("done") or expected.get("question",{}).get("id") != body.question_id:
        raise HTTPException(409,"Câu hỏi không đúng thứ tự hiện tại")
    q=row("SELECT * FROM questions WHERE id=? AND active=1",(body.question_id,))
    if not q: raise HTTPException(404,"Câu hỏi không tồn tại")
    q=hydrated_question(q,respondent)
    option=next((o for o in q["options"] if str(o["id"])==str(body.option_id)),None)
    if not option: raise HTTPException(422,"Lựa chọn không hợp lệ")
    answered_at=now();timing=row("SELECT shown_at FROM question_timing WHERE respondent_id=? AND question_id=?",(sid,body.question_id));duration=max(0,min(body.duration_ms,3600000)) if body.duration_ms is not None else None
    if timing and duration is None:
        try: duration=int((datetime.now(timezone.utc)-datetime.fromisoformat(timing["shown_at"])).total_seconds()*1000)
        except Exception: pass
    remote_record={"question_id":body.question_id,"option_id":body.option_id,"value":body.value,"scores":option.get("scores",{}),"answered_at":answered_at,"duration_ms":duration}
    if persist_remote:
        try:persist_answer(sid,remote_record)
        except DuplicateAnswer:raise HTTPException(409,"Đáp án đã được ghi nhận và không thể chỉnh sửa")
    session_updates={}
    if body.question_id=="P00" and isinstance(body.value,str) and body.value.strip():session_updates["name"]=body.value.strip()
    if body.question_id in ("P01a","P01b"):session_updates["product"]=option["label"]
    if body.question_id=="P01c":session_updates["theme"]=option.get("theme","rose")
    if body.question_id=="P02":session_updates["platform"]=option["label"]
    if session_updates and persist_remote:persist_session_update(sid,session_updates)
    with connect() as con:
        con.execute("INSERT INTO answers(respondent_id,question_id,option_id,value_json,scores_json,answered_at) VALUES(?,?,?,?,?,?)",(sid,body.question_id,body.option_id,json.dumps(body.value,ensure_ascii=False),json.dumps(option.get("scores",{})),answered_at))
        if body.question_id=="P00" and isinstance(body.value, str) and body.value.strip(): con.execute("UPDATE respondents SET name=? WHERE id=?",(body.value.strip(),sid))
        if body.question_id=="P01a": con.execute("UPDATE respondents SET product=? WHERE id=?",(option["label"],sid))
        if body.question_id=="P01b": con.execute("UPDATE respondents SET product=? WHERE id=?",(option["label"],sid))
        if body.question_id=="P01c": con.execute("UPDATE respondents SET theme=? WHERE id=?",(option.get("theme","rose"),sid))
        if body.question_id=="P02": con.execute("UPDATE respondents SET platform=? WHERE id=?",(option["label"],sid))
        if timing:
            con.execute("UPDATE question_timing SET answered_at=?,duration_ms=? WHERE respondent_id=? AND question_id=?",(answered_at,duration,sid,body.question_id))
    return remote_record,session_updates,next_question(sid) if include_next else None,False

@app.post("/api/sessions/{sid}/answers")
def answer(sid: str, body: Answer):
    _,_,next_result,replayed=process_answer(sid,body)
    return {"ok":True,"replayed":replayed,"next":next_result}

@app.post("/api/sessions/{sid}/answers/batch")
def answer_batch(sid:str,body:AnswerBatch):
    if len(body.answers)>50:raise HTTPException(422,"Mỗi batch tối đa 50 câu trả lời")
    respondent=row("SELECT * FROM respondents WHERE id=?",(sid,))
    if not respondent and firestore_enabled():
        try: project_session(sid)
        except Exception: logger.exception("Không thể lazy-load session %s",sid)
        respondent=row("SELECT * FROM respondents WHERE id=?",(sid,))
    if not respondent: raise HTTPException(404,"Không tìm thấy phiên")
    revision=body.revision if body.revision is not None else int(respondent.get("revision") or 0)+1
    idempotency_key=body.idempotency_key or f"legacy-{revision}-{uuid.uuid4().hex}"
    replay=row("SELECT response_json FROM sync_checkpoints WHERE respondent_id=? AND idempotency_key=?",(sid,idempotency_key))
    if replay:return json.loads(replay["response_json"])
    if revision!=int(respondent.get("revision") or 0)+1:raise HTTPException(409,{"code":"stale_revision","current_revision":int(respondent.get("revision") or 0)})
    accepted=0;error=None;records=[];session_updates={}
    for item in body.answers:
        try:
            record,updates,_,_=process_answer(sid,item,persist_remote=False,include_next=False)
            records.append(record);session_updates.update(updates);accepted+=1
        except HTTPException as exc:
            error={"status":exc.status_code,"detail":exc.detail};break
    next_result=calculate_next_question(sid,persist_completion=False)
    if next_result.get("done"):
        session_updates.update({"completed_at":next_result["completed_at"],"status":"completed"})
    try:
        checkpoint=commit_checkpoint(sid,records,session_updates,revision,idempotency_key)
    except ValueError as exc:
        raise HTTPException(409,{"code":"stale_revision","detail":str(exc)})
    except KeyError: raise HTTPException(404,"Không tìm thấy phiên")
    with connect() as con:
        con.execute("UPDATE respondents SET revision=? WHERE id=?",(revision,sid))
        if next_result.get("done"):
            con.execute("UPDATE respondents SET completed_at=?,status='completed' WHERE id=?",(next_result["completed_at"],sid))
    next_result.pop("completed_at",None)
    result={"ok":error is None,"committed":True,"replayed":checkpoint["replayed"],"revision":revision,"accepted":accepted,"rejected":len(body.answers)-accepted,"error":error,"next":next_result}
    with connect() as con:con.execute("INSERT OR REPLACE INTO sync_checkpoints VALUES(?,?,?,?,?)",(sid,idempotency_key,revision,json.dumps(result,ensure_ascii=False),now()))
    return result

@app.post("/api/sessions/{sid}/back")
def previous_question(sid:str):
    respondent=row("SELECT * FROM respondents WHERE id=?",(sid,))
    if not respondent: raise HTTPException(404,"Không tìm thấy phiên")
    previous=row("SELECT question_id FROM answers WHERE respondent_id=? ORDER BY answered_at DESC,id DESC LIMIT 1",(sid,))
    if not previous:return {"ok":True,"next":next_question(sid)}
    rollback_remote_answer(sid,previous["question_id"])
    with connect() as con:
        con.execute("DELETE FROM answers WHERE respondent_id=? AND question_id=?",(sid,previous["question_id"]))
        con.execute("DELETE FROM skipped WHERE respondent_id=?",(sid,))
        con.execute("DELETE FROM question_timing WHERE respondent_id=? AND question_id=?",(sid,previous["question_id"]))
        con.execute("UPDATE respondents SET status='active',completed_at=NULL WHERE id=?",(sid,))
    persist_session_update(sid,{"status":"active","completed_at":None})
    return {"ok":True,"next":next_question(sid)}

def score_result(sid):
    respondent=row("SELECT * FROM respondents WHERE id=?",(sid,)) or {}
    scores={}
    for a in rows("SELECT scores_json FROM answers WHERE respondent_id=?",(sid,)):
        for k,v in json.loads(a["scores_json"] or "{}").items(): scores.setdefault(k,[]).append(float(v))
    mean={k:sum(v)/len(v) for k,v in scores.items()}
    dimensions={
        "Mê khám phá": avg(mean,"HM","FLOW","FOMO"),
        "Mua sắm tỉnh táo": avg(mean,"PRIV","INFO","SCAM","PRICE"),
        "Tin vào cộng đồng": avg(mean,"SI","SPROOF","REVQ","AUTH"),
        "Săn giá trị": avg(mean,"PRICE","VOUCHER","BNPL"),
        "Hợp gu công nghệ": avg(mean,"PE","PERS","TRUST"),
    }
    top=max(dimensions,key=dimensions.get)
    profiles={
        "Mê khám phá":("Nhà thám hiểm deal ✦","Bạn mua sắm bằng sự tò mò và cảm hứng. Một trải nghiệm đủ cuốn có thể đưa bạn từ “xem chút thôi” đến một giỏ hàng đầy bất ngờ."),
        "Mua sắm tỉnh táo":("Người giữ ví điềm tĩnh 🛡️","Bạn có radar khá nhạy với rủi ro, thông tin nhiễu và những cú thúc mua vội. Không dễ để một chiếc đồng hồ đếm ngược điều khiển bạn."),
        "Tin vào cộng đồng":("Thám tử review 🔎","Bạn đọc dấu vết từ review, cộng đồng và độ chân thật trước khi tin. Với bạn, một lời khen hay cần có bằng chứng đi cùng."),
        "Săn giá trị":("Bậc thầy tối ưu deal ⚡","Bạn nhìn thấy giá trị ở nơi người khác chỉ thấy giá. Voucher, mức giá và cách thanh toán đều là những mảnh ghép cần tối ưu."),
        "Hợp gu công nghệ":("Người mua đúng-gu 🎯","Bạn đánh giá cao nền tảng hiểu mình, tiết kiệm thời gian và tạo cảm giác an tâm. Gợi ý đúng lúc thường chạm được sự chú ý của bạn."),
    }
    advice=[]
    if avg(mean,"FOMO","FLOW")>=4: advice.append("Thử quy tắc nghỉ 20 phút trước khi chốt deal có đồng hồ đếm ngược — món thật sự cần vẫn sẽ đáng mua sau khoảng nghỉ.")
    if avg(mean,"PRICE","VOUCHER")>=4: advice.append("Đừng chỉ nhìn số tiền được giảm; hãy so với ngân sách và giá thị trường để biết mình đang tiết kiệm hay chỉ đang chi thêm.")
    if avg(mean,"PRIV","SCAM")>=4: advice.append("Radar an toàn của bạn là lợi thế. Hãy giữ thói quen kiểm tra quyền ứng dụng, lịch sử shop và chính sách hoàn tiền.")
    if avg(mean,"REVQ","SPROOF","AUTH")>=4: advice.append("Review rất hữu ích, nhưng hãy ưu tiên đánh giá có ảnh/video và đọc cả nhóm 1–3 sao để tránh hiệu ứng đám đông.")
    if not advice: advice.append("Trước mỗi lần thanh toán, hãy tự hỏi: “Mình cần món này, hay chỉ thích cảm giác đang săn được một deal?”")
    platform=respondent.get("platform") or ""
    product=respondent.get("product") or "món đồ bạn quan tâm"
    if "TikTok" in platform: advice.append(f"Với {product} trên TikTok Shop, thử lưu sản phẩm rồi quay lại sau khi livestream kết thúc; lúc đó cảm giác khẩn cấp thường dịu hơn.")
    elif platform: advice.append(f"Khi mua {product} trên {platform}, hãy so sánh ít nhất hai shop và đọc review gần đây thay vì chỉ nhìn điểm sao tổng.")
    ac=row("SELECT option_id FROM answers WHERE respondent_id=? AND question_id='AC1'",(sid,))
    if ac and ac["option_id"]!="C":advice.append("Bạn có vẻ lướt khá nhanh ở một đoạn giữa hành trình. Với đơn giá trị cao, một nhịp dừng ngắn có thể giúp bạn không bỏ sót chi tiết quan trọng.")
    title,description=profiles[top]
    return {"profile":{"title":title,"description":description,"accent":top},"traits":[{"label":k,"value":round(v/5*100)} for k,v in sorted(dimensions.items(),key=lambda x:x[1],reverse=True)[:3]],"advice":advice[:3],"theme":respondent.get("theme") or "rose","product":respondent.get("product"),"platform":respondent.get("platform"),"note":"Chân dung mang tính tham khảo, không phải đánh giá tâm lý hay tài chính."}

def avg(values,*keys):
    found=[values[k] for k in keys if k in values]
    return sum(found)/len(found) if found else 0

@app.get("/api/admin/dashboard")
def dashboard(_: None = Depends(admin)):
    stats=row("SELECT COUNT(*) total, SUM(status='completed') completed, SUM(status='active') active FROM respondents")
    return {"stats":stats,"respondents":rows("SELECT * FROM respondents ORDER BY started_at DESC LIMIT 100"),"variables":rows("SELECT * FROM variables ORDER BY code"),"questions": [hydrated_question(q) for q in rows("SELECT * FROM questions ORDER BY position")],"last_import":row("SELECT value FROM settings WHERE key='last_import'")}

@app.post("/api/admin/import")
def reimport(user = Depends(editor)):
    result=import_workbook();result["manifest_version"]=bump_manifest_version();audit(user,"import","workbook",detail=result);return result

@app.get("/api/admin/sheets")
def sheets(_: None = Depends(admin)):
    return [decode(x,"rows_json") for x in rows("SELECT * FROM excel_sheets ORDER BY name")]

class UpdateQuestion(BaseModel):
    id: Optional[str] = None
    text: str
    active: bool = True
    phase: str = "Tự thiết kế"
    kind: str = "scenario"
    position: int = 999
    variables: list[str] = []
    options: list[dict] = []
    note: str = ""

@app.patch("/api/admin/questions/{qid}")
def update_question(qid:str, body:UpdateQuestion, user=Depends(editor)):
    with connect() as con:
        con.execute("UPDATE questions SET text=?,active=?,phase=?,kind=?,position=?,variables_json=?,options_json=?,note=? WHERE id=?",(body.text,int(body.active),body.phase,body.kind,body.position,json.dumps(body.variables),json.dumps(body.options,ensure_ascii=False),body.note,qid))
    version=bump_manifest_version();audit(user,"update","question",qid,body.model_dump());return {"ok":True,"manifest_version":version}

@app.post("/api/admin/questions")
def create_question(body: UpdateQuestion, user = Depends(editor)):
    qid=(body.id or f"CUSTOM_{uuid.uuid4().hex[:6]}").upper()
    with connect() as con:
        try: con.execute("INSERT INTO questions(id,position,phase,kind,text,variables_json,options_json,note,active) VALUES(?,?,?,?,?,?,?,?,?)",(qid,body.position,body.phase,body.kind,body.text,json.dumps(body.variables),json.dumps(body.options,ensure_ascii=False),body.note,int(body.active)))
        except Exception: raise HTTPException(409,"Mã câu hỏi đã tồn tại")
    version=bump_manifest_version();audit(user,"create","question",qid,body.model_dump());return {"ok":True,"id":qid,"manifest_version":version}

@app.delete("/api/admin/questions/{qid}")
def delete_question(qid: str, user = Depends(editor)):
    with connect() as con: con.execute("DELETE FROM questions WHERE id=?",(qid,))
    version=bump_manifest_version();audit(user,"delete","question",qid);return {"ok":True,"manifest_version":version}

class SheetRow(BaseModel):
    values: list[Union[str, int, float, None]]

@app.post("/api/admin/sheets/{name}/rows")
def add_sheet_row(name: str, body: SheetRow, user = Depends(editor)):
    sheet=decode(row("SELECT * FROM excel_sheets WHERE name=?",(name,)),"rows_json")
    if not sheet: raise HTTPException(404,"Không tìm thấy sheet")
    sheet["rows_json"].append(body.values)
    with connect() as con: con.execute("UPDATE excel_sheets SET rows_json=?,imported_at=? WHERE name=?",(json.dumps(sheet["rows_json"],ensure_ascii=False),now(),name))
    audit(user,"append_row","excel_sheet",name,{"row":len(sheet["rows_json"])});return {"ok":True,"row":len(sheet["rows_json"])}

class HeuristicConfig(BaseModel):
    weights: dict[str,float]

def heuristic_config():
    saved=row("SELECT value FROM settings WHERE key='heuristic_weights'")
    if saved: return json.loads(saved["value"])
    return {x["code"]:1.0 for x in rows("SELECT code FROM variables WHERE code NOT IN ('LATE','STRESS','PLAT','URG','FIN')")}

@app.get("/api/admin/heuristic")
def get_heuristic(_: None = Depends(admin)):
    correlations=row("SELECT value FROM settings WHERE key='correlations'")
    moderators=row("SELECT value FROM settings WHERE key='moderator_relations'")
    return {"weights":heuristic_config(),"method":"directional_centered_mean","formula":"3 + Σ[(xᵢ−3)×wᵢ] / Σ|wᵢ|","missing":"ignore","correlations":json.loads(correlations["value"]) if correlations else {},"moderators":json.loads(moderators["value"]) if moderators else []}

@app.put("/api/admin/heuristic")
def save_heuristic(body: HeuristicConfig, user = Depends(editor)):
    if any(v < -100 or v > 100 for v in body.weights.values()): raise HTTPException(422,"Trọng số phải từ -100 đến 100")
    with connect() as con: con.execute("INSERT OR REPLACE INTO settings VALUES('heuristic_weights',?)",(json.dumps(body.weights),))
    audit(user,"update","heuristic",detail={"weights":body.weights});return {"ok":True}

@app.get("/api/admin/insights")
def insights(_: None = Depends(admin)):
    respondents=rows("SELECT * FROM respondents")
    answers=rows("SELECT respondent_id,question_id,option_id,scores_json FROM answers")
    by_user={}
    distributions={}
    for a in answers:
        by_user.setdefault(a["respondent_id"],[]).append(a)
        distributions.setdefault(a["question_id"],{}).setdefault(a["option_id"],0)
        distributions[a["question_id"]][a["option_id"]]+=1
    weights=heuristic_config(); ranked=[]
    for r in respondents:
        vars={}
        for a in by_user.get(r["id"],[]):
            for k,v in json.loads(a["scores_json"] or "{}").items(): vars.setdefault(k,[]).append(float(v))
        means={k:sum(v)/len(v) for k,v in vars.items()}
        used=[(means[k],weights.get(k,0)) for k in means if weights.get(k,0)!=0]
        # Directional centered heuristic: dấu âm đảo chiều quanh mốc trung lập 3.
        score=3+sum((v-3)*w for v,w in used)/sum(abs(w) for _,w in used) if used else None
        ac=next((a["option_id"] for a in by_user.get(r["id"],[]) if a["question_id"]=="AC1"),None)
        ranked.append({"id":r["id"],"name":r["name"],"score":round(score,2) if score is not None else None,"quality":"good" if ac in (None,"C") else "low","variables":means})
    sparse=[{"question":q,"options":d} for q,d in distributions.items() if sum(d.values())>=5 and any(n/sum(d.values())<.05 for n in d.values())]
    return {"ranked":ranked,"distributions":distributions,"smart_alerts":{"low_quality":sum(x["quality"]=="low" for x in ranked),"sparse_questions":sparse,"incomplete":sum(r["status"]!="completed" for r in respondents)}}

@app.get("/api/admin/analytics")
def analytics(_: None = Depends(admin)):
    current=time.time()
    if ANALYTICS_CACHE["value"] is not None and ANALYTICS_CACHE["expires"]>current:return ANALYTICS_CACHE["value"]
    if firestore_enabled():
        try:project_recent_sessions(int(os.getenv("ANALYTICS_SESSION_PAGE_SIZE","500")))
        except Exception:logger.exception("Không thể nạp trang dữ liệu analytics từ Firestore")
    respondent_rows=rows("SELECT * FROM respondents ORDER BY started_at DESC")
    answer_rows=rows("SELECT respondent_id,question_id,option_id,value_json,scores_json,answered_at FROM answers")
    question_rows=[hydrated_question(q) for q in rows("SELECT * FROM questions ORDER BY position")]
    by_user={}; distributions={}; variable_values={}
    for answer in answer_rows:
        by_user.setdefault(answer["respondent_id"],[]).append(answer)
        distributions.setdefault(answer["question_id"],{}).setdefault(answer["option_id"],0)
        distributions[answer["question_id"]][answer["option_id"]]+=1
        for code,value in json.loads(answer.get("scores_json") or "{}").items():
            try: variable_values.setdefault(code,[]).append(float(value))
            except (TypeError,ValueError): pass
    weights=heuristic_config(); respondent_summaries=[]; durations=[]
    for respondent in respondent_rows:
        own=by_user.get(respondent["id"],[]); measured={}
        for answer in own:
            for code,value in json.loads(answer.get("scores_json") or "{}").items():
                try: measured.setdefault(code,[]).append(float(value))
                except (TypeError,ValueError): pass
        means={code:sum(values)/len(values) for code,values in measured.items()}
        used=[(means[code],weights.get(code,0)) for code in means if weights.get(code,0)!=0]
        score=3+sum((value-3)*weight for value,weight in used)/sum(abs(weight) for _,weight in used) if used else None
        duration=None
        if respondent.get("started_at") and respondent.get("completed_at"):
            try:
                duration=max(0,(datetime.fromisoformat(respondent["completed_at"])-datetime.fromisoformat(respondent["started_at"])).total_seconds())
                durations.append(duration)
            except (ValueError,TypeError): pass
        respondent_summaries.append({**respondent,"answer_count":len(own),"duration_seconds":round(duration) if duration is not None else None,"score":round(score,2) if score is not None else None})
    questions=[]
    for question in question_rows:
        counts=distributions.get(question["id"],{}); total=sum(counts.values())
        option_map={option.get("id"):option.get("label") or option.get("id") for option in question.get("options",[])}
        questions.append({"id":question["id"],"text":question["text"],"phase":question["phase"],"kind":question["kind"],"total":total,"options":[{"id":key,"label":option_map.get(key,key or "Nhập tự do"),"count":count,"percent":round(count/total*100,1) if total else 0} for key,count in sorted(counts.items(),key=lambda item:item[1],reverse=True)]})
    daily={}
    for respondent in respondent_rows:
        day=(respondent.get("started_at") or "")[:10]
        if day: daily.setdefault(day,{"date":day,"started":0,"completed":0});daily[day]["started"]+=1;daily[day]["completed"]+=respondent.get("status")=="completed"
    def grouped(field):
        result={}
        for respondent in respondent_rows:
            key=respondent.get(field) or "Chưa xác định";result[key]=result.get(key,0)+1
        return [{"label":key,"value":value} for key,value in sorted(result.items(),key=lambda item:item[1],reverse=True)]
    completed=sum(respondent.get("status")=="completed" for respondent in respondent_rows); total=len(respondent_rows)
    result={"kpis":{"total":total,"completed":completed,"active":total-completed,"completion_rate":round(completed/total*100,1) if total else 0,"answers":len(answer_rows),"average_duration_seconds":round(sum(durations)/len(durations)) if durations else None},"daily":list(sorted(daily.values(),key=lambda item:item["date"]))[-30:],"platforms":grouped("platform"),"products":grouped("product"),"variables":[{"code":code,"average":round(sum(values)/len(values),2),"responses":len(values)} for code,values in sorted(variable_values.items(),key=lambda item:sum(item[1])/len(item[1]),reverse=True)],"questions":questions,"respondents":respondent_summaries}
    ANALYTICS_CACHE.update(value=result,expires=current+15)
    return result

@app.get("/api/admin/respondents/{sid}/answers")
def respondent_answers(sid:str, _: None = Depends(admin)):
    respondent=row("SELECT * FROM respondents WHERE id=?",(sid,))
    if not respondent and firestore_enabled():
        try:project_session(sid)
        except Exception:logger.exception("Không thể lazy-load respondent %s",sid)
        respondent=row("SELECT * FROM respondents WHERE id=?",(sid,))
    if not respondent: raise HTTPException(404,"Không tìm thấy người trả lời")
    answer_rows=rows("SELECT question_id,option_id,value_json,scores_json,answered_at FROM answers WHERE respondent_id=? ORDER BY answered_at",(sid,))
    question_map={question["id"]:question for question in [hydrated_question(q) for q in rows("SELECT * FROM questions ORDER BY position")]}
    result=[]
    for answer in answer_rows:
        question=question_map.get(answer["question_id"],{}); option=next((item for item in question.get("options",[]) if item.get("id")==answer["option_id"]),{})
        result.append({**answer,"question":question.get("text",answer["question_id"]),"phase":question.get("phase",""),"answer":option.get("label") or json.loads(answer.get("value_json") or "null") or answer["option_id"],"scores":json.loads(answer.get("scores_json") or "{}")})
    return {"respondent":respondent,"answers":result}

class UpdateVariable(BaseModel):
    name_vi: str
    name_en: str = ""
    group_name: str = ""
    skip_rule: str = ""
    channel: str = ""
    active: bool = True

@app.patch("/api/admin/variables/{code}")
def update_variable(code: str, body: UpdateVariable, user = Depends(editor)):
    with connect() as con:
        con.execute("UPDATE variables SET name_vi=?,name_en=?,group_name=?,skip_rule=?,channel=?,active=? WHERE code=?", (body.name_vi,body.name_en,body.group_name,body.skip_rule,body.channel,int(body.active),code))
    audit(user,"update","variable",code,body.model_dump());return {"ok": True}

@app.get("/api/admin/export")
def export(_:None=Depends(admin)):
    data=rows("SELECT r.id,r.name,r.email,r.product,r.platform,r.status,r.started_at,r.completed_at,a.question_id,a.option_id,a.scores_json FROM respondents r LEFT JOIN answers a ON a.respondent_id=r.id ORDER BY r.started_at,a.question_id")
    out=io.StringIO(); w=csv.DictWriter(out,fieldnames=data[0].keys() if data else ["id"]); w.writeheader(); w.writerows(data)
    return Response('\ufeff'+out.getvalue(),media_type="text/csv",headers={"Content-Disposition":"attachment; filename=survey-data.csv"})

class PilotSetting(BaseModel): enabled:bool
@app.get("/api/admin/settings")
def get_settings(_:None=Depends(admin)):
    pilot=row("SELECT value FROM settings WHERE key='pilot_mode'")
    return {"pilot_mode":bool(pilot and pilot["value"]=="true")}
@app.put("/api/admin/settings/pilot")
def set_pilot(body:PilotSetting,user=Depends(editor)):
    with connect() as con:con.execute("INSERT OR REPLACE INTO settings VALUES('pilot_mode',?)",("true" if body.enabled else "false",))
    audit(user,"update","setting","pilot_mode",{"enabled":body.enabled});return {"ok":True}

class BranchRule(BaseModel):
    source_question:str;operator:str="equals";expected_value:str;target_question:str;action:str="skip";active:bool=True
@app.get("/api/admin/branches")
def list_branches(_:None=Depends(admin)):return rows("SELECT * FROM branch_rules ORDER BY id")
@app.post("/api/admin/branches")
def create_branch(body:BranchRule,user=Depends(editor)):
    with connect() as con:
        cur=con.execute("INSERT INTO branch_rules(source_question,operator,expected_value,target_question,action,active) VALUES(?,?,?,?,?,?)",(body.source_question,body.operator,body.expected_value,body.target_question,body.action,int(body.active)));rid=cur.lastrowid
    audit(user,"create","branch_rule",str(rid),body.model_dump());return {"ok":True,"id":rid}
@app.delete("/api/admin/branches/{rid}")
def delete_branch(rid:int,user=Depends(editor)):
    with connect() as con:con.execute("DELETE FROM branch_rules WHERE id=?",(rid,))
    audit(user,"delete","branch_rule",str(rid));return {"ok":True}

@app.post("/api/admin/media")
async def upload_media(question_id:str=Form(...),option_id:str=Form(...),file:UploadFile=File(...),user=Depends(editor)):
    allowed={"image/jpeg":"jpg","image/png":"png","image/webp":"webp","image/gif":"gif"}
    if file.content_type not in allowed:raise HTTPException(422,"Chỉ hỗ trợ JPG, PNG, WEBP hoặc GIF")
    content=await file.read(5*1024*1024+1)
    if len(content)>5*1024*1024:raise HTTPException(413,"Ảnh tối đa 5MB")
    filename=f"{uuid.uuid4().hex}.{allowed[file.content_type]}"
    stored_path=filename
    if firestore_enabled():
        token=uuid.uuid4().hex;object_name=f"survey-media/{filename}";blob=firebase_storage.bucket().blob(object_name);blob.metadata={"firebaseStorageDownloadTokens":token};blob.upload_from_string(content,content_type=file.content_type);blob.patch();bucket_name=blob.bucket.name;stored_path=f"https://firebasestorage.googleapis.com/v0/b/{bucket_name}/o/{quote(object_name,safe='')}?alt=media&token={token}"
    else:(MEDIA_DIR/filename).write_bytes(content)
    with connect() as con:
        old=con.execute("SELECT path FROM question_media WHERE question_id=? AND option_id=?",(question_id,option_id)).fetchone()
        con.execute("DELETE FROM question_media WHERE question_id=? AND option_id=?",(question_id,option_id))
        con.execute("INSERT INTO question_media(question_id,option_id,path,mime_type,original_name,created_at) VALUES(?,?,?,?,?,?)",(question_id,option_id,stored_path,file.content_type,file.filename,now()))
    if old:
        try:
            if not old["path"].startswith("http"):(MEDIA_DIR/old["path"]).unlink()
        except OSError:pass
    audit(user,"upload","question_media",f"{question_id}:{option_id}",{"file":file.filename});return {"ok":True,"image_url":stored_path if stored_path.startswith("http") else "/media/"+stored_path}

@app.get("/api/admin/audit")
def audit_list(limit:int=200,_=Depends(admin)):return rows("SELECT l.*,u.username FROM audit_logs l LEFT JOIN admin_users u ON u.id=l.admin_id ORDER BY l.id DESC LIMIT ?",(min(limit,500),))

class TeamNote(BaseModel):
    title:str="Ghi chú mới"
    content:str
@app.get("/api/admin/notes")
def list_notes(_:None=Depends(admin)):
    return rows("SELECT n.*,u.username author_name,u.role author_role FROM team_notes n JOIN admin_users u ON u.id=n.author_id ORDER BY n.created_at DESC")
@app.post("/api/admin/notes")
def create_note(body:TeamNote,user=Depends(admin)):
    if not body.content.strip():raise HTTPException(422,"Nội dung note không được để trống")
    with connect() as con:
        cur=con.execute("INSERT INTO team_notes(title,content,author_id,created_at,updated_at) VALUES(?,?,?,?,?)",(body.title.strip() or "Ghi chú mới",body.content.strip(),user["id"],now(),now()));note_id=cur.lastrowid
    audit(user,"create","team_note",str(note_id),{"title":body.title});return {"ok":True,"id":note_id}
@app.delete("/api/admin/notes/{note_id}")
def delete_note(note_id:int,user=Depends(admin)):
    note=row("SELECT * FROM team_notes WHERE id=?",(note_id,))
    if not note:raise HTTPException(404,"Không tìm thấy note")
    if user["role"]!="owner" and note["author_id"]!=user["id"]:raise HTTPException(403,"Bạn chỉ có thể xóa note của mình")
    with connect() as con:con.execute("DELETE FROM team_notes WHERE id=?",(note_id,))
    audit(user,"delete","team_note",str(note_id));return {"ok":True}

@app.get("/api/admin/users")
def admin_users(_:None=Depends(owner)):return rows("SELECT id,username,role,active,created_at FROM admin_users ORDER BY created_at")
class AdminUserCreate(BaseModel):username:str;password:str;role:str="editor"
@app.post("/api/admin/users")
def create_admin_user(body:AdminUserCreate,user=Depends(owner)):
    if body.role not in ("owner","editor","analyst"):raise HTTPException(422,"Role không hợp lệ")
    if len(body.password)<8:raise HTTPException(422,"Mật khẩu tối thiểu 8 ký tự")
    uid=str(uuid.uuid4())
    try:
        with connect() as con:con.execute("INSERT INTO admin_users VALUES(?,?,?,?,?,?)",(uid,body.username,password_hash(body.password),body.role,1,now()))
    except Exception:raise HTTPException(409,"Tên đăng nhập đã tồn tại")
    audit(user,"create","admin_user",uid,{"username":body.username,"role":body.role});return {"ok":True,"id":uid}

@app.post("/api/admin/backup")
def create_backup(user=Depends(owner)):
    filename=f"survey-{datetime.now().strftime('%Y%m%d-%H%M%S')}.db";target=BACKUP_DIR/filename
    import sqlite3
    source=sqlite3.connect(DB_PATH);dest=sqlite3.connect(target);source.backup(dest);dest.close();source.close()
    audit(user,"create","backup",filename);return FileResponse(target,filename=filename,media_type="application/octet-stream")
@app.post("/api/admin/restore")
async def restore_backup(file:UploadFile=File(...),user=Depends(owner)):
    content=await file.read(100*1024*1024+1)
    if len(content)>100*1024*1024 or not content.startswith(b"SQLite format 3\x00"):raise HTTPException(422,"File backup SQLite không hợp lệ")
    safety=BACKUP_DIR/f"pre-restore-{datetime.now().strftime('%Y%m%d-%H%M%S')}.db";shutil.copy2(DB_PATH,safety)
    temp=BACKUP_DIR/"restore.tmp";temp.write_bytes(content);shutil.copy2(temp,DB_PATH);temp.unlink()
    audit(user,"restore","backup",file.filename,{"safety_backup":safety.name});return {"ok":True,"safety_backup":safety.name}
