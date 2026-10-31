const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../browser-extension/studio.js'),'utf8');
const helper=source.slice(source.indexOf('  function snapshot(){'),source.indexOf('  async function readSnapshot(){'));
function read(replies,{stop='',busy=false}={}){
  const context={check(){},buttons:'button',document:{visibilityState:'visible',hasFocus:()=>true},location:{href:'https://aistudio.google.com/prompts/fixture'},
    platformText:()=>'',modelReplies:()=>replies,
    elements:(selector,pattern)=>selector==='button'?(stop&&pattern.test(stop)?[{}]:[]):(busy?[{}]:[])};
  vm.createContext(context);vm.runInContext(helper,context);return context.snapshot();
}
const done={id:'old',text:'{"beats":[]}',completed:true};
assert.equal(read([done,{id:'new',text:'',completed:false}]).finished,false);
assert.equal(read([done,{id:'new',text:'{',completed:false}]).finished,false);
assert.equal(read([done]).finished,true);
assert.equal(read([done],{stop:'stop generating'}).finished,false);
assert.equal(read([done],{stop:'Stop generating'}).generating,true);
assert.equal(read([done],{busy:true}).finished,false);
assert.equal(read([done]).protocol,4);
console.log('Latest-turn completion, lowercase Stop and busy-state checks passed.');
