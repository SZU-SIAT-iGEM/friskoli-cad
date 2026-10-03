// Review a detached draft, then commit once. Cancelling never mutates the document.
export function reviewTransaction({title,description,rows=[],fields=[],confirm='Confirm',cancel='Cancel',commit,preview}){
 const dialog=document.createElement('dialog');dialog.className='transaction-dialog';
 const heading=document.createElement('h2');heading.textContent=title;dialog.append(heading);
 const text=document.createElement('p');text.textContent=description;dialog.append(text);
 const form=document.createElement('form');const values={};
 for(const field of fields){const label=document.createElement('label');label.className='edit-row';label.textContent=field.label;const input=document.createElement(field.options?'select':field.type==='textarea'?'textarea':'input');input.name=field.name;input.setAttribute('aria-label',field.label);if(field.options)for(const [value,text]of field.options)input.add(new Option(text,value));else {if(field.type!=='textarea'){input.type=field.type??'number';input.step=field.step??'any';if(field.min!==undefined)input.min=field.min;}input.required=true;}
 input.value=field.value;values[field.name]=input;label.append(input);form.append(label);}
 const list=document.createElement('ul');for(const row of rows){const li=document.createElement('li');li.textContent=row;list.append(li);}form.append(list);
 const error=document.createElement('p');error.className='error';form.append(error);
 const actions=document.createElement('div');actions.className='task-actions';
 const dismiss=document.createElement('button');dismiss.type='button';dismiss.textContent=cancel;dismiss.addEventListener('click',()=>dialog.close());
 const submit=document.createElement('button');submit.type='submit';submit.textContent=confirm;actions.append(dismiss,submit);form.append(actions);
 const read=()=>Object.fromEntries(Object.entries(values).map(([key,input])=>[key,input.type==='number'?Number(input.value):input.value]));
 form.addEventListener('input',()=>{try{preview?.(read());error.textContent='';}catch(e){error.textContent=e.message;}});
 form.addEventListener('submit',async event=>{event.preventDefault();submit.disabled=true;try{if(await commit(read())!==false)dialog.close();}catch(e){error.textContent=e.message;}finally{submit.disabled=false;}});
 dialog.append(form);document.body.append(dialog);dialog.addEventListener('close',()=>{preview?.(null);dialog.remove();},{once:true});dialog.showModal();try{preview?.(read());}catch(e){error.textContent=e.message;}return dialog;
}
