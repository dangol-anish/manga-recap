const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('browser-extension/studio.js','utf8');
const start=source.indexOf('    function comparable('),end=source.indexOf('    const matches=',start);
assert(start>=0&&end>start);
const compare=vm.runInNewContext(source.slice(start,end)+';comparable');
const expected={type:'object',properties:{beats:{type:'array',items:{type:'object',properties:{narration:{type:'string'},panel_ids:{type:'array',items:{type:'string'}}},required:['narration','panel_ids']}}},required:['beats']};
const copy=()=>JSON.parse(JSON.stringify(expected));
const same=value=>JSON.stringify(compare(value,expected))===JSON.stringify(compare(expected,expected));
const normalized=copy();normalized.propertyOrdering=['beats'];normalized.properties.beats.items.propertyOrdering=['panel_ids','narration'];
assert(same(normalized),'Accept valid added propertyOrdering at root and nested schemas');
assert(same(copy()));
for(const change of [s=>delete s.properties.beats,s=>s.required=[],s=>s.properties.beats.items.properties.narration.type='number',s=>s.additionalProperties=false,s=>s.propertyOrdering=['wrong'],s=>s.propertyOrdering=['beats','beats'],s=>s.properties.beats.items.propertyOrdering=['narration']]){
 const value=JSON.parse(JSON.stringify(normalized));change(value);assert(!same(value),'Reject changed schema constraints or invalid ordering');
}
const ordered={...copy(),propertyOrdering:['beats']};assert.notDeepEqual(compare({...ordered,propertyOrdering:[]},ordered),compare(ordered,ordered),'Explicit requested ordering must be retained');
console.log('Schema comparison: AI Studio ordering accepted; changed fields, types, required constraints and malformed ordering rejected.');
