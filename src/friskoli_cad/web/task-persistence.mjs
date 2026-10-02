// IndexedDB stores frozen inputs and imported evidence; localStorage never holds replay arrays.
import {TASK_STORAGE_KEY} from './task-store.mjs';
const bytes=value=>new TextEncoder().encode(value).length;
const DB_NAME='friskoli-history-v1';

export function openHistoryDatabase(factory=globalThis.indexedDB){
  return new Promise((resolve,reject)=>{if(!factory)return reject(Error('IndexedDB is unavailable; durable history cannot be opened'));
    const request=factory.open(DB_NAME,1);request.onupgradeneeded=()=>{const db=request.result;db.createObjectStore('snapshots');db.createObjectStore('packages',{keyPath:'id'});};
    request.onerror=()=>reject(request.error);request.onblocked=()=>reject(Error('History database is blocked by another window'));
    request.onsuccess=()=>{const db=request.result;db.onversionchange=()=>db.close();resolve(db);};
  });
}
const read=(db,store,key)=>new Promise((resolve,reject)=>{const request=db.transaction(store).objectStore(store).get(key);request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(request.error);});

export class DurableTaskStorage {
  constructor(db,raw=null,revision=0){this.db=db;this.raw=raw;this.revision=revision;this.pending=Promise.resolve();this.error=null;}
  getItem(key){return key===TASK_STORAGE_KEY?this.raw:null;}
  setItem(key,raw){
    if(key!==TASK_STORAGE_KEY)throw Error('Unsupported task storage key');
    if(bytes(raw)>16*1024*1024)throw Error('Frozen task history exceeds 16 MiB');
    this.raw=raw;
    this.pending=this.pending.then(()=>new Promise((resolve,reject)=>{
      const transaction=this.db.transaction('snapshots','readwrite'),store=transaction.objectStore('snapshots');let conflict=false;
      const request=store.get(key);request.onsuccess=()=>{if((request.result?.revision??0)!==this.revision){conflict=true;transaction.abort();return;}store.put({raw,revision:this.revision+1},key);};
      transaction.oncomplete=()=>{this.revision++;this.error=null;resolve();};
      transaction.onabort=transaction.onerror=()=>reject(conflict?Error('Task history changed in another window; reload before submitting'):transaction.error??Error('Task history transaction failed'));
    })).catch(error=>{this.error=error;throw error;});
    // Callers must await flush before network submission. Keep rejected writes observable without unhandled rejection noise.
    this.pending.catch(()=>{});return this.pending;
  }
  flush(){return this.pending;}
}

export async function openTaskPersistence(legacyStorage=null,{factory=globalThis.indexedDB}={}){
  const db=await openHistoryDatabase(factory),saved=await read(db,'snapshots',TASK_STORAGE_KEY);
  const storage=new DurableTaskStorage(db,saved?.raw??null,saved?.revision??0);
  if(!saved){const legacy=legacyStorage?.getItem(TASK_STORAGE_KEY);if(legacy){JSON.parse(legacy);storage.setItem(TASK_STORAGE_KEY,legacy);await storage.flush();}}
  return {storage,archive:new DesignHistoryArchive(db),close:()=>db.close()};
}

export function packageRecord(payload){
  if(payload?.package_version!=='0.1.0'||typeof payload.design?.id!=='string'||!Array.isArray(payload.runs))throw Error('Expected a server-validated native design payload');
  const json=JSON.stringify(payload),size=bytes(json);if(size>128*1024*1024)throw Error('Imported design exceeds 128 MiB');
  return {id:payload.design.id,json,bytes:size,savedAt:Date.now()};
}
export class DesignHistoryArchive {
  constructor(db){this.db=db;}
  async savePackage(payload){
    const record=packageRecord(payload);
    return new Promise((resolve,reject)=>{const tx=this.db.transaction('packages','readwrite'),store=tx.objectStore('packages');let error;
      const request=store.getAll();request.onsuccess=()=>{const old=request.result.find(x=>x.id===record.id);
        if(old&&old.json!==record.json){error=Error('An archived design with this ID has different evidence; preserve both using a new design ID');tx.abort();return;}
        const other=request.result.filter(x=>x.id!==record.id);
        if(other.length>=4||other.reduce((n,x)=>n+x.bytes,0)+record.bytes>256*1024*1024){error=Error('Imported archive limit: 4 designs / 256 MiB; explicitly export and remove an archive first');tx.abort();return;}
        store.put(record);};
      tx.oncomplete=()=>resolve(record.id);tx.onabort=tx.onerror=()=>reject(error??tx.error??Error('Imported archive transaction failed'));
    });
  }
  async loadPackage(id){const record=await read(this.db,'packages',id);return record?JSON.parse(record.json):null;}
  async list(){return new Promise((resolve,reject)=>{const request=this.db.transaction('packages').objectStore('packages').getAll();request.onsuccess=()=>resolve(request.result.map(({id,bytes,savedAt})=>({id,bytes,savedAt})));request.onerror=()=>reject(request.error);});}
  async removePackage(id){return new Promise((resolve,reject)=>{const tx=this.db.transaction('packages','readwrite');tx.objectStore('packages').delete(id);tx.oncomplete=()=>resolve();tx.onabort=tx.onerror=()=>reject(tx.error);});}
}
