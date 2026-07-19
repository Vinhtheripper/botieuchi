export type DraftAnswer={session_id:string;question_id:string;option_id:string;value?:string;answered_at:string;duration_ms?:number;synced:boolean}

const DB='group2-survey';const STORE='answers'
function database(){return new Promise<IDBDatabase>((resolve,reject)=>{const request=indexedDB.open(DB,1);request.onupgradeneeded=()=>{const db=request.result;if(!db.objectStoreNames.contains(STORE)){const store=db.createObjectStore(STORE,{keyPath:['session_id','question_id']});store.createIndex('session_id','session_id')}};request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(request.error)})}
async function transaction<T>(mode:IDBTransactionMode,run:(store:IDBObjectStore,done:(value:T)=>void)=>void){const db=await database();return new Promise<T>((resolve,reject)=>{let result:T;const tx=db.transaction(STORE,mode);run(tx.objectStore(STORE),value=>{result=value});tx.onerror=()=>reject(tx.error);tx.oncomplete=()=>{db.close();resolve(result)}})}
export async function saveDraft(answer:DraftAnswer){return transaction<void>('readwrite',(store,done)=>{store.put(answer);done()})}
export async function drafts(session_id:string){return transaction<DraftAnswer[]>('readonly',(store,done)=>{const request=store.index('session_id').getAll(session_id);request.onsuccess=()=>done(request.result.sort((a,b)=>a.answered_at.localeCompare(b.answered_at)))})}
export async function pendingDrafts(session_id:string){return(await drafts(session_id)).filter(answer=>!answer.synced)}
export async function markSynced(session_id:string,ids:string[]){const all=await drafts(session_id);return transaction<void>('readwrite',(store,done)=>{all.filter(a=>ids.includes(a.question_id)).forEach(a=>store.put({...a,synced:true}));done()})}
export async function removeDraft(session_id:string,question_id:string){return transaction<void>('readwrite',(store,done)=>{store.delete([session_id,question_id]);done()})}
export async function removeDrafts(session_id:string,ids:string[]){return transaction<void>('readwrite',(store,done)=>{ids.forEach(id=>store.delete([session_id,id]));done()})}
export async function latestDraft(session_id:string){const all=await drafts(session_id);return all.at(-1)}
