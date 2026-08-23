// applyView の位置合わせ部分だけを取り出して挙動を確かめる
function decide(current, hostPos, elapsed, playing){
  const target = hostPos + (playing ? elapsed : 0);
  const drift = current - target;
  if (Math.abs(drift) > 1.2) return {action:"seek", to:+target.toFixed(2), rate:1};
  if (playing && Math.abs(drift) > 0.12)
    return {action:"rate", rate: drift>0 ? 0.96 : 1.04, drift:+drift.toFixed(2)};
  return {action:"none", rate:1, drift:+drift.toFixed(2)};
}
const cases = [
  ["補正なしなら遅れる", 12.5, 12.5, 0.8, true],
  ["経過を足せば一致",   13.3, 12.5, 0.8, true],
  ["わずかに遅れ",       13.0, 12.5, 0.8, true],
  ["わずかに進み",       13.6, 12.5, 0.8, true],
  ["大きくずれ",          5.0, 12.5, 0.8, true],
  ["一時停止中は足さない",12.5, 12.5, 3.0, false],
];
let ok = 0;
for (const [label, cur, pos, el, play] of cases) {
  const r = decide(cur, pos, el, play);
  console.log(`  ${label.padEnd(22)} -> ${JSON.stringify(r)}`);
  ok++;
}
// 期待どおりか
const a = decide(13.3, 12.5, 0.8, true);
const b = decide(13.0, 12.5, 0.8, true);
const c = decide(13.6, 12.5, 0.8, true);
const d = decide(5.0, 12.5, 0.8, true);
const e = decide(12.5, 12.5, 3.0, false);
const pass = a.action==="none" && b.rate===1.04 && c.rate===0.96
  && d.action==="seek" && d.to===13.3 && e.action==="none";
console.log("\n" + (pass ? "DRIFT OK" : "NG"));
process.exit(pass?0:1);
