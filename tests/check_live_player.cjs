// Logic smoke check for the generated offline player. No browser or npm packages.
// A minimal DOM checks controls, counts and SVG data; it does not test CSS layout.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const filename = process.argv[2] || path.join(__dirname, '../figures/01_live_queue.html');
const html = fs.readFileSync(filename, 'utf8');
const encoded = html.match(/data-role="model-data">([\s\S]*?)<\/script>/)[1];
const data = JSON.parse(encoded);
const source = html.match(/<script>\s*([\s\S]*?)<\/script>/)[1];
let animations = 0;
let chartWidth = 800;
class Element {
  constructor(role = '') {
    this.role = role; this.children = []; this.events = {}; this.attrs = {};
    this.textContent = ''; this.value = ''; this.isConnected = false;
    this.style = {setProperty() {}};
  }
  append(node) { node.parent = this; node.isConnected = true; this.children.push(node); }
  replaceChildren() { this.children.forEach(node => { node.isConnected = false; }); this.children = []; }
  setAttribute(key, value) { this.attrs[key] = String(value); }
  addEventListener(name, callback) { this.events[name] = callback; }
  getAnimations() { return []; }
  getBoundingClientRect() {
    if (this.role === 'history-chart') return {width:chartWidth, left:0, top:0};
    return {left:230 * ['incoming', 'waiting', 'outgoing'].indexOf(this.parent.role)
      + 40 * this.parent.children.indexOf(this), top:100};
  }
  animate(frames, options) { animations++; assert.ok(options.duration > 0); }
  emit(name) { assert.equal(typeof this.events[name], 'function'); this.events[name](); }
}
const elements = Object.fromEntries([...html.matchAll(/data-role="([^"]+)"/g)]
  .map(match => [match[1], new Element(match[1])]));
elements['model-data'].textContent = encoded;
elements.speed.value = html.match(/<option value="(\d+)" selected>/)[1];
const slider = new Element();
const svg = new Element();
elements['history-chart'].querySelector = selector => { assert.equal(selector, 'svg'); return svg; };
const root = new Element();
root.querySelector = selector => {
  if (selector === '#flow-day') return slider;
  const role = selector.match(/^\[data-role="([^"]+)"\]$/)[1];
  assert.ok(elements[role], 'Selector must exist: ' + selector);
  return elements[role];
};
let interval = null;
let resize = null;
vm.runInNewContext(source, {
  document: {
    getElementById: id => { assert.equal(id, 'feature-flow-player'); return root; },
    createElement: () => new Element(), createElementNS: () => new Element(),
    addEventListener() {}, hidden:false,
  },
  window: {
    matchMedia: () => ({matches:false}),
    setInterval: callback => { interval = callback; return 1; },
    clearInterval: () => { interval = null; },
  },
  ResizeObserver: class { constructor(callback) { resize = callback; } observe() {} },
  Intl, Number, Map, Set,
});
const euro = cents => new Intl.NumberFormat('en-IE', {
  style:'currency', currency:data.currency, minimumFractionDigits:2
}).format(cents / 100);
const count = role => Number(elements[role].textContent.replaceAll(',', ''));
function check(dayIndex, phase) {
  const day = data.live_days[dayIndex];
  const waiting = phase === 0 ? data.backlog[dayIndex]
    : phase === 1 ? data.backlog[dayIndex] + data.arrivals[dayIndex] : data.backlog[dayIndex + 1];
  assert.equal(count('incoming-count'), data.arrivals[dayIndex]);
  assert.equal(count('waiting-count'), waiting);
  assert.equal(count('outgoing-count'), phase === 2 ? data.tested[dayIndex] : 0);
  assert.equal(elements.money.textContent, euro(waiting * data.feature_cost_cents));
  const ranges = {
    incoming: [day.arrival_first, phase === 0 ? day.arrivals : 0],
    waiting: [day.queue_first[phase], waiting],
    outgoing: [day.tested_first, phase === 2 ? day.tested : 0],
  };
  const visible = [];
  for (const [role, [first, total]] of Object.entries(ranges)) {
    const ids = elements[role].children.filter(node => node.className === 'tile numbers').map(node => Number(node.textContent));
    assert.deepEqual(ids, Array.from({length:Math.min(total,60)}, (_,index) => first + index));
    assert.equal(elements[role + '-overflow'].hidden, total <= 60);
    visible.push(...ids);
  }
  assert.equal(new Set(visible).size, visible.length, 'No feature can occupy two areas at once');
  const completedDays = dayIndex + (phase === 2 ? 1 : 0);
  const line = svg.children.find(node => node.attrs.class === 'line');
  assert.equal((line.attrs.d.match(/ H /g) || []).length, completedDays, 'History contains only completed days');
  assert.ok(!/NaN|Infinity/.test(line.attrs.d), 'Chart coordinates must be finite');
  assert.equal(elements['history-value'].textContent, 'Day ' + completedDays + ' · ' + data.backlog[completedDays].toLocaleString('en-IE') + ' waiting');
}
for (let step = 0; step < data.days * 3; step++) {
  check(Math.floor(step / 3), step % 3);
  if (step < data.days * 3 - 1) elements.step.emit('click');
}
assert.equal(elements.step.disabled, true);
assert.equal(elements.play.textContent, 'Replay');
elements.play.emit('click'); check(0, 0);
interval(); check(0, 1);
elements.speed.value = '60'; elements.speed.emit('change');
interval(); check(0, 2);
elements.play.emit('click'); assert.equal(interval, null);
slider.value = data.days; slider.emit('input'); check(data.days - 1, 0);
elements.play.emit('click'); interval(); interval(); check(data.days - 1, 2);
assert.equal(interval, null, 'Playback stops at the end');
chartWidth = 320; resize();
assert.equal(svg.attrs.viewBox, '0 0 320 236');
if (data.arrivals.some(value => value > 0)) assert.ok(animations > 0);
console.log('Player checks passed: ' + (data.days * 3) + ' phases, FIFO tiles, exact costs, history, controls and resize.');
