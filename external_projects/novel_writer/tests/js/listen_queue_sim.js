const fs = require("fs");
const html = fs.readFileSync("../../ui/listen_component/index.html", "utf8");
const js = html.match(/<script>([\s\S]*)<\/script>/)[1];

// --- 极简 DOM 桩 ---
const els = {};
function el(id) {
  if (!els[id]) els[id] = { id, value: "", textContent: "", innerHTML: "", className: "", style: {}, options: [],
    appendChild(){}, addEventListener(){}, set onclick(f){ this._click=f; }, get onclick(){ return this._click; },
    set onchange(f){ this._ch=f; }, get onchange(){ return this._ch; }, set oninput(f){ this._in=f; }, set onkeydown(f){} };
  return els[id];
}
const listeners = {};
global.window = { parent: { postMessage(m){ posts.push(m); } }, addEventListener(t,f){ (listeners[t] ||= []).push(f); } };
global.document = { getElementById: el, documentElement: { style: { setProperty(){} } }, body: { scrollHeight: 100 },
  createElement(){ return { value:"", textContent:"", selected:false, appendChild(){}, set onchange(f){}, get value(){return this._v||"";}, set value(v){this._v=v;} }; } };
global.ResizeObserver = undefined;
const posts = [];

// --- 假语音引擎：按 enqueue 顺序逐段「朗读」，每段 onstart 后 SEG_MS 毫秒 onend ---
const SEG_MS = 20;
let queue = [], current = null, paused = false, timer = null;
const log = [];
class Utt { constructor(t){ this.text=t; this.rate=1; this.lang=""; this.voice=null; } }
global.SpeechSynthesisUtterance = Utt;
const synth = {
  get speaking(){ return !!current; }, get pending(){ return queue.length > 0; }, get paused(){ return paused; },
  getVoices(){ return []; }, onvoiceschanged: undefined,
  speak(u){ queue.push(u); log.push("enqueue:" + u.text); pump(); },
  cancel(){
    const dropped = queue.splice(0); if (current) { dropped.push(current); clearTimeout(timer); current = null; }
    dropped.forEach(u => { log.push("cancel:" + u.text); if (u.onerror) setTimeout(() => u.onerror({ error: "canceled" }), 0); });
  },
  pause(){ paused = true; }, resume(){ paused = false; pump(); },
};
function pump(){
  if (current || paused || !queue.length) return;
  current = queue.shift();
  const u = current;
  setTimeout(() => { if (u.onstart && current === u) { log.push("start:" + u.text); u.onstart(); } }, 0);
  timer = setTimeout(() => { if (current === u) { current = null; log.push("end:" + u.text); if (u.onend) u.onend(); pump(); } }, SEG_MS);
}
global.speechSynthesis = synth; global.window.speechSynthesis = synth;
global.setInterval = () => 0; // 关闭看门狗，专注测试队列
global.setTimeout = ((orig) => (f, ms) => orig(f, ms))(global.setTimeout);

// --- 载入播放器并初始化 ---
const segs = ["第一段内容。", "第二段内容。", "第三段内容。", "第四段内容。", "第五段内容。"];
const payload = { segments: segs, chapters: [{ ch: 1, title: "甲", start: 0 }, { ch: 2, title: "乙", start: 3 }] };
eval(js);
const send = (args) => listeners.message.forEach(f => f({ data: { type: "streamlit:render", args, theme: null } }));
send({ payload, rate: 1, voice: "", resume_ch: null, resume_seg: 0 });
const wait = ms => new Promise(r => setTimeout(r, ms));
(async () => {
  el("toggle").onclick();                    // 播放
  await wait(10);
  console.log("after play start, queued ahead:", log.filter(x=>x.startsWith("enqueue")).length);
  console.log(log.join(" | "));
  await wait(200);
  console.log("\n--- finished ---");
  console.log(log.filter(x => /^(start|end|cancel)/.test(x)).join("\n"));
  console.log("final idx text:", el("text").textContent, "| now:", el("now").textContent);
  log.length = 0;
  // 跳转到第 2 段（播放中）
  el("segNum").value = 2; el("segGo").onclick();
  await wait(5);
  console.log("\n--- jump to seg 2 while idle-> then play ---");
  el("toggle").onclick(); await wait(10);
  el("stop").onclick();                      // 停止，保留位置
  await wait(30);
  console.log(log.filter(x => /^(start|cancel|end)/.test(x)).join(" | "));
  console.log("after stop text:", el("text").textContent);
})();
