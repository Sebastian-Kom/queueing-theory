// Offline controller checks with a small DOM substitute. Layout needs a browser.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const model = require('../templates/02_queue_model.js');
const filename = process.argv[2] || path.join(__dirname, '../figures/02_live_queue.html');
const html = fs.readFileSync(filename, 'utf8');
const encoded = html.match(/data-role="model-data">([\s\S]*?)<\/script>/)[1];
const input = JSON.parse(encoded);
const source = html.match(/<script>\s*([\s\S]*?)<\/script>/)[1];
let width = 736, animations = 0, interval = null, resize;
class Element {
  constructor(role='') { this.role=role;this.children=[];this.events={};this.attrs={};this.textContent='';this._value='';this.style={setProperty(){}}; }
  get value(){return this._value;}
  set value(v){this._value=String(v);}
  append(node){node.parent=this;node.isConnected=true;this.children.push(node);}
  replaceChildren(){this.children.forEach(n=>{n.isConnected=false;});this.children=[];}
  setAttribute(key,value){this.attrs[key]=String(value);}
  addEventListener(name,callback){this.events[name]=callback;}
  getAnimations(){return [];}
  animate(){animations++;}
  getBoundingClientRect(){return {width:this.role.includes('distribution')?width/2:width,left:200*['incoming','waiting','outgoing'].indexOf(this.parent?.role)+(this.parent?.children.indexOf(this)||0)*40,top:100};}
  emit(name){assert.equal(typeof this.events[name],'function','Missing event '+name+' on '+this.role);this.events[name]({preventDefault(){}});}
}
const elements=Object.fromEntries([...html.matchAll(/data-role="([^"]+)"/g)].map(m=>[m[1],new Element(m[1])]));
elements['model-data'].textContent=encoded;
elements.speed.value='600';
const svg=new Element('history-svg');
elements['history-chart'].querySelector=()=>svg;
for(const role of ['development','testing']) elements[role+'-distribution'].querySelector=()=>elements[role+'-svg'] ||= new Element(role+'-svg');
const root=new Element();
root.querySelector=selector=>{
  const role=selector.match(/^\[data-role="([^"]+)"\]$/)?.[1];
  assert.ok(elements[role],'Unknown queried element '+selector);return elements[role];
};
vm.runInNewContext(source,{
  document:{getElementById:id=>{assert.equal(id,'capacity-queue-player');return root;},createElement:()=>new Element(),createElementNS:()=>new Element(),addEventListener(){},hidden:false},
  window:{matchMedia:()=>({matches:false}),setInterval:fn=>{interval=fn;return 1;},clearInterval:()=>{interval=null;}},
  ResizeObserver:class {constructor(fn){resize=fn;}observe(){}},Intl,Number,Map,Set,console
});
assert.equal(elements.error.hidden,true, elements.error.textContent);
const euro=cents=>new Intl.NumberFormat('en-IE',{style:'currency',currency:input.currency,maximumFractionDigits:2}).format(cents/100);
const count=role=>Number(elements[role].textContent.replaceAll(',',''));
function check(reference,index,phase){
  const day=reference.live_days[index];
  assert.equal(count('incoming-count'),day.arrivals);
  assert.equal(count('waiting-count'),day.queue_counts[phase]);
  assert.equal(count('outgoing-count'),phase===2?day.tested:0);
  assert.equal(elements.money.textContent,euro(day.queue_cost_cents[phase]));
  assert.equal(elements.capacity.textContent,'Testing capacity today: '+day.capacity.toLocaleString('en-IE'));
  const visible=[];
  for(const [role,first,total] of [['incoming',day.arrival_first,phase===0?day.arrivals:0],['waiting',day.queue_first[phase],day.queue_counts[phase]],['outgoing',day.tested_first,phase===2?day.tested:0]]){
    const ids=elements[role].children.filter(n=>n.className==='tile text-small tabular-nums').map(n=>Number(n.textContent));
    assert.deepEqual(ids,Array.from({length:Math.min(total,36)},(_,i)=>first+i));
    assert.equal(elements[role+'-overflow'].hidden,total<=36);visible.push(...ids);
  }
  assert.equal(new Set(visible).size,visible.length,'Feature shown in multiple stages');
  const through=index+(phase===2?1:0);
  const line=svg.children.find(n=>n.attrs.class==='line');
  assert.equal((line.attrs.d.match(/ H /g)||[]).length,through);
  assert.ok(!/NaN|Infinity/.test(line.attrs.d));
  assert.equal(elements['history-value'].textContent,'Day '+through+' · '+reference.backlog[through].toLocaleString('en-IE')+' waiting');
  const age=day.oldest_wait[phase], mean=reference.mean_completed_wait[through];
  const label=x=>x===null?'—':Number(x.toFixed(2))+' days';
  assert.equal(elements.oldest.textContent,label(age));
  assert.equal(elements['mean-wait'].textContent,label(mean));
}
for(let step=0;step<input.days*3;step++){
  check(input.reference,Math.floor(step/3),step%3);
  if(step<input.days*3-1)elements.step.emit('click');
}
assert.equal(elements.step.disabled,true);assert.equal(elements.play.textContent,'Replay');
elements.play.emit('click');check(input.reference,0,0);interval();check(input.reference,0,1);
elements.speed.value='60';elements.speed.emit('change');interval();check(input.reference,0,2);
elements.play.emit('click');assert.equal(interval,null);
elements.day.value=input.days;elements.day.emit('input');check(input.reference,input.days-1,0);
elements.play.emit('click');interval();interval();check(input.reference,input.days-1,2);assert.equal(interval,null);

// Changing inputs pauses the old run. Apply updates all four parameters together.
for(const values of [[10,0,10,0],[10,2,12,2],[1,20,0,5],[0,0,0,0]]){
  const roles=['development-mean','development-std','testing-mean','testing-std'];
  roles.forEach((role,i)=>{elements[role].value=values[i];elements[role].emit('input');});
  assert.equal(elements.play.disabled,true);assert.equal(interval,null);
  elements.settings.emit('submit');assert.equal(elements.error.hidden,true,elements.error.textContent);
  const expected=model.simulate(model.counts(input.development_shocks,values[0],values[1]),model.counts(input.testing_shocks,values[2],values[3]),input.feature_cost_cents);
  check(expected,0,0);
  elements.day.value=input.days;elements.day.emit('input');elements.step.emit('click');elements.step.emit('click');check(expected,input.days-1,2);
}
for(const bad of ['', '-1','NaN','Infinity','1001']){
  elements['testing-std'].value=bad;elements['testing-std'].emit('input');elements.settings.emit('submit');
  assert.equal(elements.error.hidden,false);assert.equal(elements.play.disabled,true);
}
elements['testing-std'].value='0';elements['testing-std'].emit('input');elements.settings.emit('submit');
assert.equal(elements.error.hidden,true);
width=320;resize();assert.equal(svg.attrs.viewBox,'0 0 320 230');
assert.ok(animations>0 || input.reference.arrivals.every(x=>x===0));
console.log('Capacity player passed: '+input.days*3+' phases, FIFO tiles, costs, waits, history, playback, seeking, parameter changes and validation. Layout not tested by this DOM substitute.');
