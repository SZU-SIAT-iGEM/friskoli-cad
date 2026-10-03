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
export function captureViewState(state,viewport,root=document,graphEditor=null){
 return {version:1,details:structuredClone(state.detailsView??{}),module_folders:structuredClone(state.moduleFolders??{}),mechanism_expanded:[...(state.mechanismExpanded??new Map())],view:state.view,left:state.left,selected_block:state.selectedBlock,selected_environment:state.selectedEnvironment,graph_selection:structuredClone(state.graphSelection),camera:viewport?.captureView?.()??null,workflow:graphEditor?{zoom:graphEditor.zoom,left:graphEditor.container.scrollLeft,top:graphEditor.container.scrollTop}:null,scroll:Object.fromEntries(['inspector','objects-pane','modules-pane','data-pane'].map(id=>[id,root.getElementById(id)?.scrollTop??0]))};
}

export function installDetailsView(state,root=document){
 const key=detail=>{const panel=detail.closest('[id]')?.id??'document',node=detail.closest('[data-bound-node]')?.dataset.boundNode??'';const context=panel==='inspector'?`${state.selectedBlock??''}:${state.selectedEnvironment??''}:${state.graphSelection?.id??''}:${state.selectedManifest??''}`:'';return `${panel}|${context}|${node}|${detail.className}|${detail.querySelector('summary')?.textContent??''}`;};
 const known=new WeakSet();
 const restore=()=>{for(const detail of root.querySelectorAll('details')){if(known.has(detail))continue;known.add(detail);const id=key(detail);detail.dataset.viewStateKey=id;if(Object.hasOwn(state.detailsView??{},id))detail.open=state.detailsView[id];}};
 const onToggle=event=>{const detail=event.target;if(detail.tagName!=='DETAILS'||!detail.isConnected)return;state.detailsView??={};state.detailsView[detail.dataset.viewStateKey??key(detail)]=detail.open;const keys=Object.keys(state.detailsView);for(const id of keys.slice(0,Math.max(0,keys.length-1000)))delete state.detailsView[id];};
 root.addEventListener('toggle',onToggle,true);const observer=new MutationObserver(restore);observer.observe(root.body,{childList:true,subtree:true});restore();return ()=>{observer.disconnect();root.removeEventListener('toggle',onToggle,true);};
}
