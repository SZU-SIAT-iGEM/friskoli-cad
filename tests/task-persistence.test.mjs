import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {openTaskPersistence,packageRecord} from '../src/friskoli_cad/web/task-persistence.mjs';
import {TASK_STORAGE_KEY} from '../src/friskoli_cad/web/task-store.mjs';
const require=createRequire(import.meta.url);let IDBFactory;try{({IDBFactory}=require('fake-indexeddb'));}catch{}
const options={skip:!IDBFactory};
test('IndexedDB migration and reload preserve exact frozen metadata without replay',options,async()=>{
  const factory=new IDBFactory(),raw=JSON.stringify({version:1,records:[{submission:{request_id:'immutable'},idempotencyKey:'same-key'}]});
  const first=await openTaskPersistence({getItem:()=>raw},{factory});assert.equal(first.storage.getItem(TASK_STORAGE_KEY),raw);
  first.close();const restored=await openTaskPersistence(null,{factory});assert.equal(restored.storage.getItem(TASK_STORAGE_KEY),raw);restored.close();
});
test('IndexedDB rejects stale-window writes and preserves the latest committed snapshot',options,async()=>{
  const factory=new IDBFactory(),a=await openTaskPersistence(null,{factory}),b=await openTaskPersistence(null,{factory});
  await a.storage.setItem(TASK_STORAGE_KEY,'{"version":1,"records":[]}');
  await assert.rejects(b.storage.setItem(TASK_STORAGE_KEY,'{"version":1,"records":[1]}'),/another window/);
  const fresh=await openTaskPersistence(null,{factory});assert.equal(fresh.storage.getItem(TASK_STORAGE_KEY),'{"version":1,"records":[]}');a.close();b.close();fresh.close();
});
test('imported package transaction persists history and rejects same-ID conflicts atomically',options,async()=>{
  const factory=new IDBFactory(),a=await openTaskPersistence(null,{factory}),payload={package_version:'0.1.0',design:{id:'d'},runs:[{id:'r',replay:{snapshots:[{frame:{time_s:0}}]}}]};
  await a.archive.savePackage(payload);a.close();const b=await openTaskPersistence(null,{factory});assert.deepEqual(await b.archive.loadPackage('d'),payload);
  await assert.rejects(b.archive.savePackage({...payload,runs:[]}),/different evidence/);assert.deepEqual(await b.archive.loadPackage('d'),payload);
  await b.archive.removePackage('d');assert.equal(await b.archive.loadPackage('d'),null);b.close();
});
test('package format is explicit and cannot silently store arbitrary payload',()=>{assert.throws(()=>packageRecord({}),/validated/);});
test('archive capacity rejects the fifth design without evicting earlier evidence',options,async()=>{
  const factory=new IDBFactory(),a=await openTaskPersistence(null,{factory});for(let i=0;i<4;i++)await a.archive.savePackage({package_version:'0.1.0',design:{id:String(i)},runs:[]});
  await assert.rejects(a.archive.savePackage({package_version:'0.1.0',design:{id:'fifth'},runs:[]}),/limit/);assert.equal((await a.archive.list()).length,4);a.close();
});
