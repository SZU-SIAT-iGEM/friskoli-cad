const CACHE='friskoli.catalog-cache.v1',RECENTS='friskoli.recent-projects.v1';
export class WorkspaceSession {
 constructor(storage,{maxProjects=8,maxBytes=8*1024*1024}={}){this.storage=storage;this.maxProjects=maxProjects;this.maxBytes=maxBytes;}
 read(key,fallback){try{return JSON.parse(this.storage?.getItem(key)??'null')??fallback;}catch{return fallback;}}
 write(key,value){const text=JSON.stringify(value);if(new TextEncoder().encode(text).byteLength>this.maxBytes)throw Error('Local workspace storage limit exceeded');this.storage?.setItem(key,text);}
 cacheCatalog(value){this.write(CACHE,{version:1,saved_at:new Date().toISOString(),...value});}
 cachedCatalog(){const value=this.read(CACHE,null);return value?.version===1&&value.registries&&value.capabilities&&value.template?value:null;}
 cacheProjectCatalog(lock,value){const all=this.read('friskoli.project-catalogs.v1',{});all[JSON.stringify(lock)]=value;this.write('friskoli.project-catalogs.v1',all);}
 projectCatalog(lock){return this.read('friskoli.project-catalogs.v1',{})[JSON.stringify(lock)]??null;}
 recent(){return this.read(RECENTS,[]);}
 remember(document,viewState={}){const id=document.project.id;let list=this.recent().filter(item=>item.id!==id);list.unshift({id,saved_at:new Date().toISOString(),document,view_state:viewState});list=list.slice(0,this.maxProjects);while(list.length>1&&new TextEncoder().encode(JSON.stringify(list)).byteLength>this.maxBytes)list.pop();this.write(RECENTS,list);}
}
export function captureViewState(state,viewport,root=document){
 return {version:1,view:state.view,left:state.left,selected_block:state.selectedBlock,selected_environment:state.selectedEnvironment,graph_selection:structuredClone(state.graphSelection),camera:viewport?.captureView?.()??null,scroll:Object.fromEntries(['inspector','objects-pane','modules-pane','data-pane'].map(id=>[id,root.getElementById(id)?.scrollTop??0]))};
}
