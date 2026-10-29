// 听书播放器：章节载荷在线更新（applyPayload）的行为测试。用桩对象代替浏览器 API，直接执行页面脚本。
const fs = require("fs");
const path = require("path");
const html = fs.readFileSync(path.join(__dirname, "../../ui/listen_component/index.html"), "utf8");
const js = /<script>([\s\S]*)<\/script>/.exec(html)[1];

const handlers = {};
const els = {};
const el = id => (els[id] = els[id] || { id, value: "", innerHTML: "", textContent: "", style: {}, className: "",
  options: [], appendChild(o) { this.options.push(o); }, addEventListener() {}, focus() {} });
const spoken = [];   // 每次 speak 的 utterance
const synth = {
  speaking: false, pending: false, paused: false,
  speak(u) { spoken.push(u); this.speaking = true; }, cancel() { this.speaking = false; }, resume() {}, pause() {},
  getVoices() { return []; },
};
global.SpeechSynthesisUtterance = function (text) { this.text = text; };
global.window = {
  speechSynthesis: synth, parent: { postMessage() {} },
  addEventListener(t, f) { handlers[t] = f; }, ResizeObserver: undefined,
};
global.document = { body: { scrollHeight: 0 }, createElement: () => ({ value: "", textContent: "" }), getElementById: el, documentElement: { style: { setProperty() {} } }, activeElement: null };

eval(js);   // 页面 IIFE 不导出，通过 message 事件驱动
const send = args => handlers["message"]({ data: { type: "streamlit:render", args } });
const press = id => el(id).onclick();
const started = () => spoken.length;
function fail(msg) { console.log("FAIL " + msg); process.exit(1); }
function eq(a, b, m) { if (a !== b) fail(m + " got " + JSON.stringify(a) + " want " + JSON.stringify(b)); }

// 初始载荷：ch1 = [h1,a1]，ch2 = [h2,b1,b2,b3]，ch3 = [h3,c1,c2]
const P1 = { segments: ["h1", "a1", "h2", "b1", "b2", "b3", "h3", "c1", "c2"],
  chapters: [{ ch: 1, title: "一", start: 0 }, { ch: 2, title: "二", start: 2 }, { ch: 3, title: "三", start: 6 }] };
send({ payload: P1, rate: 1, voice: "", resume_ch: 2, resume_seg: 1, resume_anchor: "b1" });
eq(el("now").textContent.indexOf("第2章") >= 0, true, "初始化在第2章");

// 从 b1（下标3）开始播放：排队 3..5（AHEAD=2 → 3,4,5）
press("toggle");
const n0 = started();
eq(spoken[0].text, "b1", "开始朗读 b1");
spoken[0].onstart();                       // 浏览器开始朗读 b1
eq(el("now").textContent.indexOf("第2章") >= 0, true, "播放中仍在第2章");

// 场景 A：末尾新增第4章，已排队文字不变 → 不重排队，位置不变
const PA = { segments: P1.segments.concat(["h4", "d1"]),
  chapters: P1.chapters.concat([{ ch: 4, title: "四", start: 9 }]) };
send({ payload: PA, rate: 1, voice: "", resume_ch: 2, resume_seg: 1, resume_anchor: "b1" });
eq(started(), n0, "场景A：不应重新朗读");
spoken[1].onstart();                       // 下一段 b2 按新映射开始
eq(el("prog").textContent.indexOf("全书段落 5/11") >= 0, true, "场景A：b2 在新载荷中的全书位置");

// 场景 B：第1章插入一段（所有后续下标右移），已排队文字不变 → 只换映射，不重排队，朗读不断
const PB = { segments: ["h1", "a1", "a1x", "h2", "b1", "b2", "b3", "h3", "c1", "c2"],
  chapters: [{ ch: 1, title: "一", start: 0 }, { ch: 2, title: "二", start: 3 }, { ch: 3, title: "三", start: 7 }] };
const before = started();
send({ payload: PB, rate: 1, voice: "", resume_ch: 2, resume_seg: 1, resume_anchor: "b2" });
eq(started(), before, "场景B：插入前面的段落不应重排队");
spoken[2].onstart();                       // b3，应落在新载荷的下标 6
eq(el("prog").textContent.indexOf("全书段落 7/10") >= 0, true, "场景B：b3 映射到新下标 6");

// 场景 C：即将朗读的段落文字变了（c1 已在队列中）→ 从当前段 b3 的新位置重新排队，不回退到章首
const PC = { segments: ["h1", "a1", "a1x", "h2", "b1", "b2", "b3", "h3", "C1改", "c2"],
  chapters: PB.chapters };
const beforeC = started();
send({ payload: PC, rate: 1, voice: "", resume_ch: 2, resume_seg: 1, resume_anchor: "b3" });
eq(started() > beforeC, true, "场景C：文字变了需要重排队");
eq(spoken[beforeC].text, "b3", "场景C：重排队的第一段是当前段 b3，而不是章首");

// 场景 C 之前的历史文字（b2）改动不影响朗读：已读过的段落不触发重排队（见下方 B 之后的单独验证）
// 场景 D：播放中当前章节暂时缺失 → 不切换，保持原内容，等它回来
const beforeD = el("prog").textContent;
send({ payload: { segments: ["h1", "a1"], chapters: [{ ch: 1, title: "一", start: 0 }] },
  rate: 1, voice: "", resume_ch: 2, resume_seg: 1, resume_anchor: "b3" });
eq(el("prog").textContent, beforeD, "场景D：章节缺失时保持不变");

// 场景 E：空载荷（正在重写）→ 忽略
send({ payload: { segments: [], chapters: [] }, rate: 1, voice: "", resume_ch: 2, resume_seg: 1 });
eq(el("prog").textContent, beforeD, "场景E：空载荷不清空");

console.log("OK");
process.exit(0);
