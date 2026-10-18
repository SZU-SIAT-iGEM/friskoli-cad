import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync,readdirSync} from 'node:fs';
import {readWorkspace,writeWorkspace,blankProject} from '../src/friskoli_cad/web/workspace.mjs';

const folder=new URL('../src/friskoli_cad/examples/',import.meta.url);
for(const name of readdirSync(folder).filter(name=>name.endsWith('.project.json'))){
  test(`${name} loads and survives an editable workspace round trip`,()=>{
    const project=JSON.parse(readFileSync(new URL(name,folder),'utf8'));
    const loaded=readWorkspace(project);
    assert.deepEqual(readWorkspace(writeWorkspace(loaded)).project,project);
    assert.deepEqual(readWorkspace({...project,controls:{}}).project.controls,{});
    assert.throws(()=>readWorkspace({...project,controls:{obsolete:{}}}),/project structure/);
    const blank=blankProject(project);
    assert.equal(Object.hasOwn(blank,'controls'),false);
    assert.deepEqual(readWorkspace(blank).project.groups,{});
  });
}
