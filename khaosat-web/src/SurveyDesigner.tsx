import {useMemo,useState} from 'react'
import {useDispatch} from 'react-redux'
import type {Question} from './types'
import {api,useBranchesQuery,useCreateQuestionMutation,useDeleteQuestionMutation,useUpdateQuestionMutation} from './store'
import {apiUrl,mediaUrl} from './config'
import './survey-designer.css'

const kindName:Record<string,string>={scenario:'Tình huống',piping:'Cá nhân hóa',likert:'Thang cảm xúc',text:'Nhập tự do',attention:'Kiểm tra tập trung',demographic:'Nhân khẩu học',transition:'Màn chuyển chặng',theme:'Chọn giao diện'}
const kindIcon:Record<string,string>={scenario:'◫',transition:'→',likert:'☺',theme:'◉',text:'T',attention:'◎',demographic:'♙',piping:'⌁'}
const phaseKind=(phase:string)=>phase==='Chuyển chặng'?'transition':phase==='Kiểm tra tập trung'?'attention':phase==='Nhân khẩu học'?'demographic':'scenario'

export default function SurveyDesigner({questions}:{questions:Question[]}){
 const[phase,setPhase]=useState('Tất cả'),[search,setSearch]=useState(''),[editing,setEditing]=useState<any>(null)
 const dispatch=useDispatch()
 const[section,setSection]=useState<'content'|'options'|'operation'>('content'),[uploads,setUploads]=useState<Record<string,File>>({})
 const[create]=useCreateQuestionMutation(),[update]=useUpdateQuestionMutation(),[remove]=useDeleteQuestionMutation();const{data:branches=[]}=useBranchesQuery()
 const safeQuestions=useMemo(()=>questions.map(q=>({...q,text:q.text||'',phase:q.phase||'Chưa phân loại',kind:q.kind||'scenario',options:Array.isArray(q.options)?q.options:[],variables:Array.isArray(q.variables)?q.variables:[]})),[questions])
 const phases=['Tất cả',...Array.from(new Set(safeQuestions.map(q=>q.phase)))]
 const shown=useMemo(()=>safeQuestions.filter(q=>(phase==='Tất cả'||q.phase===phase)&&(!search||`${q.id} ${q.text}`.toLowerCase().includes(search.toLowerCase()))),[safeQuestions,phase,search])
 const reviewGroups=useMemo(()=>Array.from(new Set(shown.map(q=>q.phase))).map(name=>({name,questions:shown.filter(q=>q.phase===name)})),[shown])
 function fresh(){const selected=phase==='Tất cả'?'Mở đầu':phase;return{id:'',text:'',phase:selected,kind:phaseKind(selected),position:Math.max(0,...safeQuestions.map(q=>Number(q.position)||0))+1,variables:[],options:[{id:'A',label:'',scores:{}},{id:'B',label:'',scores:{}}],note:'',active:true,_new:true}}
 function open(q:any){setEditing({...q});setUploads({});setSection('content')}
 async function upload(qid:string,optionId:string,file:File){const form=new FormData();form.append('file',file);form.append('question_id',qid);form.append('option_id',optionId);const response=await fetch(apiUrl('/admin/media'),{method:'POST',headers:{authorization:`Bearer ${sessionStorage.getItem('admin_token')||''}`},body:form});if(!response.ok)throw new Error('Upload ảnh thất bại')}
 async function save(){const body={...editing,variables:editing.variables||[],options:editing.options||[]};delete body._new;if(editing._new)await create(body).unwrap();else await update({id:editing.id,...body}).unwrap();await Promise.all(Object.entries(uploads).map(([key,file])=>upload(editing.id,key,file)));dispatch(api.util.invalidateTags(['Dashboard']));setEditing(null)}
 return <div className="studio">
  <section className="studio-summary"><div><span>TOÀN BỘ LUỒNG</span><b>{questions.length}</b><small>màn hình</small></div><div><span>ĐANG BẬT</span><b>{questions.filter(q=>q.active).length}</b><small>câu hoạt động</small></div><div><span>RẼ NHÁNH</span><b>{branches.length}</b><small>luật logic</small></div><div><span>GIAI ĐOẠN</span><b>{phases.length-1}</b><small>chặng trải nghiệm</small></div></section>
  <section className="studio-tools"><div className="phase-filter">{phases.map(p=><button className={phase===p?'active':''} onClick={()=>setPhase(p)} key={p}>{p}</button>)}</div><div className="studio-actions"><input placeholder="Tìm mã hoặc nội dung câu..." value={search} onChange={e=>setSearch(e.target.value)}/><button className="primary" onClick={()=>open(fresh())}>＋ Tạo {phase==='Tất cả'?'màn hình':phase.toLowerCase()}</button></div></section>
  {phase==='Tất cả'?<SurveyReview groups={reviewGroups} open={open}/>:
  <div className="flow-list">{shown.map((q,index)=>{const logic=branches.filter((r:any)=>r.source_question===q.id||r.target_question===q.id);return <article className={!q.active?'disabled':''} key={q.id}><div className="flow-order"><span>{String(index+1).padStart(2,'0')}</span><i/></div><div className={'kind-icon '+q.kind}>{kindIcon[q.kind]||'✦'}</div><div className="flow-content"><header><b>{q.id}</b><span>{kindName[q.kind]||q.kind}</span><em>{q.phase}</em>{!q.active&&<strong>Tạm tắt</strong>}</header><p>{q.text.replace(/^\[[^\]]+\]\s*/,'')}</p><footer><span>{q.options.length} lựa chọn</span>{q.variables.length>0&&<span>{q.variables.length} biến đo</span>}{logic.length>0&&<span className="logic-chip">⌁ {logic.length} luật rẽ nhánh</span>}</footer></div><div className="flow-actions"><button onClick={()=>open(q)}>Chỉnh sửa</button><button className="delete" onClick={()=>confirm(`Xóa ${q.id}?`)&&remove(q.id)}>×</button></div></article>})}</div>}
  {shown.length===0&&<div className="studio-empty">Không tìm thấy câu hỏi phù hợp.</div>}
  {editing&&<div className="studio-modal"><div className="studio-editor"><header className="editor-title"><div><span>{editing._new?'TẠO MỚI':'CHỈNH SỬA'}</span><h2>{editing._new?'Màn hình '+editing.phase:editing.id}</h2></div><button onClick={()=>setEditing(null)}>×</button></header><nav><button className={section==='content'?'active':''} onClick={()=>setSection('content')}><b>1</b>Nội dung & ảnh</button><button className={section==='options'?'active':''} onClick={()=>setSection('options')}><b>2</b>Lựa chọn & điểm</button><button className={section==='operation'?'active':''} onClick={()=>setSection('operation')}><b>3</b>Vận hành</button></nav><main>
   {section==='content'&&<div className="editor-section"><div className="field-row"><label>Mã màn hình<small>Dùng để nhận diện trong dữ liệu</small><input disabled={!editing._new} value={editing.id} onChange={e=>setEditing({...editing,id:e.target.value.toUpperCase()})}/></label><label>Loại màn hình<small>Đã gợi ý theo bộ lọc {phase}</small><select value={editing.kind} onChange={e=>setEditing({...editing,kind:e.target.value})}>{Object.entries(kindName).map(([k,v])=><option value={k} key={k}>{v}</option>)}</select></label></div><label>Nội dung người tham gia nhìn thấy<small>Có thể dùng {'{NAME}'}, {'{PRODUCT}'}, {'{PLATFORM}'}</small><textarea value={editing.text} onChange={e=>setEditing({...editing,text:e.target.value})}/></label><label className="media-upload">Ảnh minh họa câu hỏi<small>Không bắt buộc · JPG/PNG/WEBP/GIF tối đa 5MB</small><input type="file" accept="image/*" onChange={e=>e.target.files?.[0]&&setUploads({...uploads,__question__:e.target.files[0]})}/><span>{uploads.__question__?`✓ ${uploads.__question__.name}`:editing.image_url?'✓ Đang có ảnh · chọn file để thay':'＋ Chọn ảnh'}</span></label><label>Ghi chú nội bộ<textarea className="short" value={editing.note||''} onChange={e=>setEditing({...editing,note:e.target.value})}/></label></div>}
   {section==='options'&&<div className="editor-section"><div className="section-help"><b>Vector điểm ẩn</b><span>Người khảo sát không nhìn thấy. Nhập dạng PE:5, TRUST:3.</span></div>{(editing.options||[]).map((o:any,i:number)=><div className="choice-row" key={i}><span>{o.id}</span><label>Nội dung lựa chọn<input value={o.label} onChange={e=>{const a=[...editing.options];a[i]={...o,label:e.target.value};setEditing({...editing,options:a})}}/></label><label>Điểm ẩn<input value={Object.entries(o.scores||{}).map(([k,v])=>`${k}:${v}`).join(', ')} placeholder="PE:5, TRUST:3" onChange={e=>{const scores=Object.fromEntries(e.target.value.split(',').map(x=>x.split(':')).filter(x=>x[0]?.trim()).map(([k,v])=>[k.trim(),Number(v)]));const a=[...editing.options];a[i]={...o,scores};setEditing({...editing,options:a})}}/></label><label className="choice-media"><input type="file" accept="image/*" onChange={e=>e.target.files?.[0]&&setUploads({...uploads,[o.id]:e.target.files[0]})}/><span title="Ảnh đáp án">{uploads[o.id]?'✓':'▧'}</span></label><button onClick={()=>setEditing({...editing,options:editing.options.filter((_:any,n:number)=>n!==i)})}>×</button></div>)}<button className="add-choice" onClick={()=>setEditing({...editing,options:[...(editing.options||[]),{id:String.fromCharCode(65+(editing.options?.length||0)),label:'',scores:{}}]})}>＋ Thêm lựa chọn</button></div>}
   {section==='operation'&&<div className="editor-section"><div className="field-row"><label>Giai đoạn<input value={editing.phase} onChange={e=>setEditing({...editing,phase:e.target.value})}/></label><label>Thứ tự hiển thị<input type="number" value={editing.position} onChange={e=>setEditing({...editing,position:+e.target.value})}/></label></div><label>Biến nghiên cứu<input value={(editing.variables||[]).join(', ')} onChange={e=>setEditing({...editing,variables:e.target.value.split(',').map(x=>x.trim()).filter(Boolean)})}/></label><div className="activation"><div><b>Trạng thái màn hình</b><small>Tắt để ẩn nhưng không xóa cấu hình.</small></div><label className="switch"><input type="checkbox" checked={editing.active} onChange={e=>setEditing({...editing,active:e.target.checked})}/><span/>{editing.active?'Đang hoạt động':'Đang tắt'}</label></div></div>}
  </main><footer className="editor-footer"><button onClick={()=>setEditing(null)}>Hủy</button><button className="primary" disabled={!editing.id||!editing.text} onClick={save}>Lưu thay đổi</button></footer></div></div>}
 </div>
}

function SurveyReview({groups,open}:{groups:{name:string;questions:Question[]}[];open:(q:Question)=>void}){
 let order=0
 return <div className="form-review">
  {groups.map((group,groupIndex)=><section className="review-section" key={group.name}>
   <header><div><small>CHẶNG {String(groupIndex+1).padStart(2,'0')}</small><h3>{group.name}</h3></div><span>{group.questions.length} màn hình</span></header>
   {group.questions.map(q=>{order+=1;return <article className={!q.active?'disabled':''} key={q.id} onClick={()=>open(q)}>
    <div className="review-number">{String(order).padStart(2,'0')}</div><div className="review-card">
     <header><b>{q.id}</b><em>{kindName[q.kind]||q.kind}</em>{!q.active&&<strong>Tạm tắt</strong>}<button>Chỉnh sửa</button></header>
     {q.image_url&&<img className="review-hero" src={mediaUrl(q.image_url)} alt=""/>}
     <h3>{q.text.replace(/^\[[^\]]+\]\s*/,'')}</h3><QuestionPreview q={q}/>
    </div>
   </article>})}
  </section>)}
 </div>
}

function QuestionPreview({q}:{q:Question}){
 if(q.kind==='text')return <div className="review-text">Nhập câu trả lời của bạn</div>
 if(q.kind==='likert')return <div className="review-likert"><span>🙅</span><span>👎</span><span>😐</span><span>👌</span><span>🎯</span></div>
 if(q.kind==='transition')return <div className="review-transition">Tiếp tục hành trình　→</div>
 return <div className="review-options">{q.options.map(o=><div key={o.id}>{o.image_url&&<img src={mediaUrl(o.image_url)} alt=""/>}<i>{o.id}</i><span>{o.label||'Lựa chọn chưa có nội dung'}</span></div>)}</div>
}
