const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../browser-extension/studio.js'),'utf8');
const helper=source.match(/async function until\(fn,timeout=.*\n/)[0];
async function exercise(readyAt,deadline=600000,problem=false){
  let now=0,checks=0;
  const context={Date:{now:()=>now},actionDeadline:deadline,
    wait:async ms=>{now+=ms;},check:()=>{checks++;if(problem)throw Error('provider limit');},
    fail:message=>{throw Error(message);}};
  vm.createContext(context);vm.runInContext(helper,context);
  const result=context.until(()=>now>=readyAt?'ready':null);
  return {result,elapsed:()=>now,checks:()=>checks};
}
(async()=>{
  const delayed=await exercise(590000);
  assert.equal(await delayed.result,'ready');assert.equal(delayed.elapsed(),590000);
  const expired=await exercise(Infinity);
  await assert.rejects(expired.result,/10-minute preparation/);assert.equal(expired.elapsed(),600000);
  const bounded=await exercise(590000,1000);
  await assert.rejects(bounded.result,/Nothing was submitted/);assert.equal(bounded.elapsed(),1000);
  const blocked=await exercise(590000,600000,true);
  await assert.rejects(blocked.result,/provider limit/);assert.equal(blocked.elapsed(),0);
  console.log('Studio waits: delayed readiness, ten-minute timeout, shared deadline and provider errors passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
