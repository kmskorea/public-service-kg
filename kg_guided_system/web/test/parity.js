/* Python 판정기와 JS 판정기(web/judge.js)의 일치 검사.
 *   node web/test/parity.js <kg.json> <cases.json>
 * cases.json 은 tests/test_web_parity.py 가 Python 판정기로 만든다. 다르면 첫 차이들을 출력하고 1 로 끝난다.
 */
"use strict";
const fs = require("fs");
const path = require("path");
const K = require(path.join(__dirname, "..", "judge.js"));

const [kgPath, casesPath] = process.argv.slice(2);
const kg = new K.KG(JSON.parse(fs.readFileSync(kgPath, "utf8")));
const judge = new K.Judge(kg);
const consultant = new K.Consultant(kg);
const cases = JSON.parse(fs.readFileSync(casesPath, "utf8"));

function canon(j) {
  return JSON.stringify([j.tier, j.conditions.map((c) => [c.kind, c.status, c.text, c.evidence, c.pending]),
    j.caveats, j.preferred, j.apply]);
}

let fails = [], checks = 0;
function eq(name, a, b) {
  checks++;
  const sa = JSON.stringify(a), sb = JSON.stringify(b);
  if (sa !== sb) fails.push(`${name}\n   python: ${sb.slice(0, 400)}\n   js    : ${sa.slice(0, 400)}`);
}

for (const c of cases.judge) {
  const r = judge.run(c.profile, c.opts);
  eq(`[${c.name}] 순서`, r.judgments.map((j) => j.service.id), c.order);
  eq(`[${c.name}] 못 찾은 표현`, r.prepared.unresolved, c.unresolved);
  const byId = new Map(r.judgments.map((j) => [j.service.id, j]));
  for (const [id, want] of Object.entries(c.detail)) {
    const j = byId.get(id);
    eq(`[${c.name}] ${id} 판정`, j ? canon(j) : null, want);
  }
  eq(`[${c.name}] 되묻기`, K.missingInfo(r.judgments, 15), c.missing);
}
for (const c of cases.consult) {
  const out = consultant.consult(c.input);
  eq(`[${c.name}] 상담 순위`, out.ranked.slice(0, 80).map((j) => j.service.id), c.ranked);
  eq(`[${c.name}] 규칙 범주`, out.derived.map((d) => d.label), c.derived);
  eq(`[${c.name}] 되묻기(상담)`, out.followUps.slice(0, 5).map((f) => [f.name, f.count]), c.followUps);
  eq(`[${c.name}] 일반 범주로만 적합`, out.ranked.filter((j) => j.genericOnly).map((j) => j.service.id).sort(),
     c.genericOnly);
}
for (const c of cases.income) eq(`소득 ${JSON.stringify(c.args)}`, K.medianIncome(c.args[0]), c.median);

if (fails.length) {
  console.log(`불일치 ${fails.length} / 검사 ${checks}`);
  console.log(fails.slice(0, 12).join("\n"));
  process.exit(1);
}
console.log(`일치: 검사 ${checks}건 (판정 ${cases.judge.length}개 프로필, 상담 ${cases.consult.length}개)`);
