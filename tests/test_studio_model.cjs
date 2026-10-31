const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../browser-extension/studio.js'),'utf8');
const helpers=source.slice(source.indexOf('  function modelSurface(){'),source.indexOf('  async function configure('));
function fixture({name='Gemini 3.8 Flash',id='gemini-3.8-flash',heading=true,legacy=false,menu=false}={}){
  const node=text=>({text,hidden:false,parentElement:null,querySelectorAll:()=>[],matches:()=>false,closest:()=>null});
  const title=node(name),modelId=node(id),switchControl=node('Structured outputs');
  if(menu){title.closest=()=>({});modelId.closest=()=>({});}
  const root=node('Run settings '+name+' '+id);
  root.querySelector=()=>null;
  root.querySelectorAll=selector=>selector==='[role="switch"]'?[switchControl]:[title,modelId];
  const settingsHeading=node('Run settings');settingsHeading.parentElement=root;
  const body=node('body');root.parentElement=body;
  const button=node(name+' '+id);button.matches=()=>true;
  const context={document:{body},buttons:'button',visible:e=>!e.hidden,label:e=>e.text,
    elements:(selector,pattern)=>{
      const candidates=selector==='button'?(legacy?[button]:[]):(heading?[settingsHeading]:[]);
      return candidates.filter(e=>pattern.test(e.text));
    }};
  vm.createContext(context);vm.runInContext(helpers,context);
  return {context,root,title,modelId};
}
const card=fixture();assert.equal(card.context.modelSurface(),card.root);assert(card.context.expectedModel(card.root));
const wrong=fixture({name:'Gemini 4 Argon',id:'gemini-4-argon'});assert.equal(wrong.context.expectedModel(wrong.root),false);
const menu=fixture({menu:true});assert.equal(menu.context.expectedModel(menu.root),false);
const hidden=fixture();hidden.title.hidden=hidden.modelId.hidden=true;assert.equal(hidden.context.expectedModel(hidden.root),false);
const promptOnly=fixture({heading:false});assert.equal(promptOnly.context.modelSurface(),null,'A model mention outside Run settings is not selection evidence');
const legacy=fixture({heading:false,legacy:true});assert(legacy.context.expectedModel(legacy.context.modelSurface()));
console.log('Non-button model card, exact model ID, wrong model, hidden/menu text and legacy model checks passed.');
