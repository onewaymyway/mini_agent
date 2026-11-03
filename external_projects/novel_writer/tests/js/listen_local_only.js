// 仅本地音色的音色过滤与所选音色校验（从播放器页面抽取的纯函数，用模拟的 speechSynthesis 执行）。
const voices = [
  { name: "Huihui", lang: "zh-CN", localService: true },
  { name: "Google 普通话", lang: "zh-CN", localService: false },
  { name: "Yaoyao", lang: "zh-HK", localService: true },
  { name: "English", lang: "en-US", localService: true },
];
const synth = { getVoices: () => voices };
const selEl = { value: "" };
const $ = id => (id === "voice" ? selEl : null);
let localOnly = false;
__FUNCS__
const eq = (x, y, m) => { if (x !== y) { console.log("FAIL " + m + " got " + JSON.stringify(x) + " want " + JSON.stringify(y)); process.exit(1); } };
eq(zhVoices().map(v => v.name).join(","), "Google 普通话,Huihui,Yaoyao", "默认列出全部中文音色");
localOnly = true;
eq(zhVoices().map(v => v.name).join(","), "Huihui,Yaoyao", "仅本地：过滤掉在线音色");
selEl.value = "Google 普通话";
eq(localVoiceSelected(), false, "仅本地：所选为在线音色则不允许朗读");
selEl.value = "Huihui";
eq(localVoiceSelected(), true, "仅本地：所选为本地音色则允许朗读");
localOnly = false;
selEl.value = "Google 普通话";
eq(localVoiceSelected(), true, "关闭时不做限制");
console.log("OK");
