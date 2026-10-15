// Run with node. No real sites, accounts, browser permissions or submissions.
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
let checked = false, keys = [], speech = [], attached = [], detached = [];
let messageListener;
const ax = () => [{nodeId: '1', backendDOMNodeId: 11, role: {value: 'radio'}, name: {value: 'Delivery'},
  value: {value: 'PRIVATE VALUE'}, properties: [{name: 'checked', value: {value: String(checked)}}, {name: 'value', value: {value: 'PRIVATE VALUE'}}]}];
const context = vm.createContext({chrome: {
  tabs: {query: async () => [{id: 7, url: 'https://fixture.test/'}]},
  debugger: {
    attach: async target => attached.push(target.tabId),
    detach: async target => detached.push(target.tabId),
    onDetach: {addListener: () => {}},
    sendCommand: async (target, method, params) => {
      assert.equal(target.tabId, 7);
      if (method === 'Accessibility.getFullAXTree') return {nodes: ax()};
      if (method === 'DOM.focus') {assert.equal(params.backendNodeId, 11); return {};}
      if (method === 'Input.dispatchKeyEvent') {
        keys.push(params);
        if (params.type === 'keyUp') checked = true;
        return {};
      }
      throw new Error('Unexpected command: ' + method);
    }
  },
  runtime: {id: 'test-extension', getURL: value => 'chrome-extension://test-extension/' + value,
    onMessage: {addListener: callback => {messageListener = callback;}},
    sendNativeMessage: async (host, request) => {
      assert.equal(host, 'com.voicecompanion.browser');
      assert.ok(!JSON.stringify(request).includes('PRIVATE VALUE'));
      speech.push(request);
      return {protocol: 1, ok: true};
    }
  }
}});
vm.runInContext(fs.readFileSync(path.join(__dirname, 'worker.js'), 'utf8'), context);
(async () => {
  const run = command => vm.runInContext(`run(${JSON.stringify(command)})`, context);
  assert.equal(messageListener({command: 'connect'}, {id: 'evil', url: 'https://fixture.test/'}, () => {throw new Error('Should reject website messages');}), false);
  await assert.rejects(run('read'), /Connect this tab/);
  await run('connect');assert.deepEqual(attached, [7]);
  await run('next');assert.equal(speech.at(-1).nodes[0].properties[0].value.value, 'false');
  await run('activate');
  assert.equal(keys.length, 2);assert.equal(keys[0].key, ' ');assert.equal(keys[0].type, 'keyDown');assert.equal(keys[1].type, 'keyUp');
  assert.equal(speech.at(-1).nodes[0].properties[0].value.value, 'true');
  await run('space');assert.equal(keys.length, 4);assert.ok(checked);
  assert.match(await run('next'), /End of controls/);
  await run('read');
  await run('disconnect');assert.deepEqual(detached, [7]);
  await assert.rejects(run('read'), /Connect this tab/);
  process.stdout.write('Regular-browser worker checks passed.\n');
})().catch(error => {process.stderr.write(error.stack + '\n');process.exitCode = 1;});
