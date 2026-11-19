/* KG 기반 공공서비스 자격 판정기 (브라우저 · node 공용)
 *
 * judge/judge.py 와 judge/pipeline.py 의 규칙을 그대로 옮긴 것이다. 두 구현이 같은 결과를 내는지는
 * tests/test_web_parity.py (→ web/test/parity.js) 가 Python 결과와 서비스·조건·근거 문자열 단위로 비교한다.
 * 판정 규칙을 바꿀 때는 두 곳을 함께 고치고 그 테스트를 돌린다.
 *
 * 데이터: web/data/kg.json (scripts/export_web.py 가 kg.ttl + kg_patches.ttl 에서 만든다)
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.KGJudge = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  var MET = "충족", UNMET = "불충족", UNKNOWN = "모름";
  var ELIGIBLE = "적합", NEEDS = "확인 필요", INELIGIBLE = "부적합";
  var APPLY = {
    OPEN: "지금 신청 가능", UPCOMING: "신청 기간 전", CLOSED: "신청 기간 아님",
    NO_APPLICATION: "신청 불필요", UNKNOWN: "기간 확인 필요"
  };
  var TIER_ORDER = {}; TIER_ORDER[ELIGIBLE] = 0; TIER_ORDER[NEEDS] = 1; TIER_ORDER[INELIGIBLE] = 2;

  // 2026년 기준 중위소득 (원/월). judge/extract.py 와 같다. 8인 이상은 (7인 − 6인) 차이를 더한다.
  var MEDIAN_2026 = { 1: 2564238, 2: 4199292, 3: 5359036, 4: 6494738, 5: 7556719, 6: 8555952, 7: 9515150 };
  function medianIncome(size) {
    size = Math.max(size || 1, 1);
    if (MEDIAN_2026[size]) return MEDIAN_2026[size];
    return MEDIAN_2026[7] + (MEDIAN_2026[7] - MEDIAN_2026[6]) * (size - 7);
  }

  // judge/linker.py AGE_RULES · ASSUMED_FOR_CITIZEN, judge/pipeline.py SENSITIVE_WORDS 와 같다
  var AGE_RULES = [
    ["영유아", function (a) { return a < 7; }, "영유아보육법: 7세 미만"],
    ["아동", function (a) { return a < 18; }, "아동복지법: 18세 미만"],
    ["청년", function (a) { return a >= 19 && a <= 34; }, "청년기본법: 19~34세"],
    ["노인", function (a) { return a >= 65; }, "노인복지법: 65세 이상"],
    ["고령자", function (a) { return a >= 65; }, "65세 이상"],
    ["경로우대자", function (a) { return a >= 65; }, "65세 이상"]
  ];
  var ASSUMED_FOR_CITIZEN = ["대한민국 국민", "일반인"];
  var SENSITIVE_WORDS = ["장애", "질병", "질환", "환자", "피해", "폭력", "범죄", "성매매", "노숙", "북한이탈", "탈북",
    "난민", "수형", "출소", "중독", "미혼모", "희귀"];

  function nospace(s) { return String(s).replace(/\s+/g, ""); }
  function cmp(a, b) { return a < b ? -1 : a > b ? 1 : 0; }
  // Python 의 f"{x:g}" 와 같은 꼴: 유효숫자 6자리, 정수면 소수점 없음
  function fmtNum(x) { return String(Number(Number(x).toPrecision(6))); }

  // ------------------------------------------------------------------ KG 색인
  function KG(data) {
    var self = this;
    this.meta = data.meta;
    this.concepts = data.concepts;          // [{k, l, p:[idx], a?, u?, t?, o?}]  idx 순서 = IRI 순서
    this.regions = data.regions;            // [{k, l, p:[idx]}]
    this.services = data.services;
    this.children = this.concepts.map(function () { return []; });
    this.labelMap = Object.create(null);    // 라벨(정확) → [idx]
    this.labelNospace = Object.create(null);
    this.byKey = Object.create(null);
    this.concepts.forEach(function (c, i) {
      self.byKey[c.k] = i;
      c.p.forEach(function (p) { self.children[p].push(i); });
      [c.l].concat(c.a || []).forEach(function (lab) {
        (self.labelMap[lab] = self.labelMap[lab] || []).push(i);
        var ns = nospace(lab);
        (self.labelNospace[ns] = self.labelNospace[ns] || []).push(i);
      });
    });
    this.regionByKey = Object.create(null);
    this.regions.forEach(function (r, i) { self.regionByKey[r.k] = i; });
    this._up = Object.create(null); this._down = Object.create(null); this._rup = Object.create(null);
  }
  KG.prototype.label = function (i) { return this.concepts[i].l; };
  KG.prototype.regionLabel = function (i) { return this.regions[i].l; };
  function closure(start, edges) {
    var seen = new Set([start]), stack = [start];
    while (stack.length) {
      var cur = stack.pop(), nx = edges(cur);
      for (var k = 0; k < nx.length; k++) if (!seen.has(nx[k])) { seen.add(nx[k]); stack.push(nx[k]); }
    }
    return seen;
  }
  KG.prototype.ancestors = function (i) {
    var self = this;
    return this._up[i] || (this._up[i] = closure(i, function (x) { return self.concepts[x].p; }));
  };
  KG.prototype.descendants = function (i) {
    var self = this;
    return this._down[i] || (this._down[i] = closure(i, function (x) { return self.children[x]; }));
  };
  KG.prototype.regionAncestors = function (r) {
    var self = this;
    return this._rup[r] || (this._rup[r] = closure(r, function (x) { return self.regions[x].p; }));
  };
  KG.prototype.broaderPath = function (start, goal) {         // BFS, 부모는 IRI 순 (kg_index.broader_path 와 같음)
    var prev = new Map([[start, null]]), queue = [start];
    while (queue.length) {
      var cur = queue.shift();
      if (cur === goal) {
        var path = [cur];
        while (prev.get(path[path.length - 1]) !== null) path.push(prev.get(path[path.length - 1]));
        return path.reverse();
      }
      var ps = this.concepts[cur].p.slice().sort(function (a, b) { return a - b; });
      for (var k = 0; k < ps.length; k++) if (!prev.has(ps[k])) { prev.set(ps[k], cur); queue.push(ps[k]); }
    }
    return null;
  };
  KG.prototype.conceptsForLabel = function (text) {            // 정확 일치, 없으면 공백 무시
    if (typeof text === "number") return this.concepts[text] ? [text] : [];
    text = String(text).trim();
    if (this.labelMap[text]) return this.labelMap[text].slice();
    return (this.labelNospace[nospace(text)] || []).slice();
  };
  KG.prototype.resolveRegion = function (text) {
    if (text === null || text === undefined || text === "") return null;
    if (typeof text === "number") return this.regions[text] ? text : null;
    var t = String(text).trim().replace("특별시", "").replace("광역시", "");
    if (t.indexOf("region_") === 0) return t in this.regionByKey ? this.regionByKey[t] : null;
    var parts = t.split(/\s+/), last = parts[parts.length - 1];
    var cands = ["region_" + parts.join("_"), "region_서울_" + last];
    for (var k = 0; k < cands.length; k++) if (cands[k] in this.regionByKey) return this.regionByKey[cands[k]];
    var hits = [];
    this.regions.forEach(function (r, i) { if (r.l === last) hits.push(i); });
    return hits.length === 1 ? hits[0] : null;
  };
  KG.prototype.isTopRegion = function (r) { return this.regions[r].p.length === 0; };

  // ------------------------------------------------------------------ 판정
  function sortedUnique(xs) { return Array.from(new Set(xs)).sort(function (a, b) { return a - b; }); }

  function Judge(kg) { this.kg = kg; }

  /* profile: {age, region, incomePct, gender, groups:[라벨|idx], notGroups:[…], userTypes:['개인', …]} */
  Judge.prototype.prepare = function (p) {
    var kg = this.kg, unresolved = [], roots = new Set();
    (p.groups || []).forEach(function (g) {
      var hits = kg.conceptsForLabel(g);
      if (!hits.length) unresolved.push(String(g));
      hits.forEach(function (h) { roots.add(h); });
    });
    var have = new Set(), haveRoots = new Map();
    roots.forEach(function (c) {
      kg.ancestors(c).forEach(function (a) {
        have.add(a);
        if (!haveRoots.has(a)) haveRoots.set(a, new Set());
        haveRoots.get(a).add(c);
      });
    });
    var deny = new Set();
    (p.notGroups || []).forEach(function (g) {
      var hits = kg.conceptsForLabel(g);
      if (!hits.length) unresolved.push("¬" + g);
      hits.forEach(function (h) { kg.descendants(h).forEach(function (d) { deny.add(d); }); });
    });
    have.forEach(function (h) { deny.delete(h); });
    var region = p.region === undefined || p.region === null || p.region === "" ? null : kg.resolveRegion(p.region);
    if (p.region && region === null) unresolved.push("지역:" + p.region);
    var userTypes = new Set();
    (p.userTypes || ["개인"]).forEach(function (t) {
      var key = "ut_" + String(t).replace(/\//g, "_");
      if (key in kg.byKey) userTypes.add(kg.byKey[key]);
    });
    return {
      profile: p, have: have, haveRoots: haveRoots, deny: deny, region: region,
      regionUp: region === null ? new Set() : kg.regionAncestors(region), userTypes: userTypes, unresolved: unresolved
    };
  };

  Judge.prototype._status = function (c, pp, closed) {
    if (pp.have.has(c)) return MET;
    if (pp.deny.has(c) || closed) return UNMET;
    return UNKNOWN;
  };

  Judge.prototype._why = function (c, pp) {
    var kg = this.kg, best = null;
    var roots = Array.from(pp.haveRoots.get(c) || []).sort(function (a, b) { return a - b; });
    roots.forEach(function (r) {
      var path = kg.broaderPath(r, c);
      if (path && (best === null || path.length < best.length)) best = path;
    });
    if (!best) return "";
    if (best.length === 1) return "'" + kg.label(best[0]) + "'에 해당";
    return best.map(function (x) { return kg.label(x); }).join(" ⊂ ") + " (상위 개념)";
  };

  function cond(kind, status, text, evidence, via, matched, pending) {
    return { kind: kind, status: status, text: text, evidence: evidence || "", via: via || [],
      matched: matched || [], pending: pending || [] };
  }

  Judge.prototype.judgeService = function (s, pp, closed, today) {
    var kg = this.kg, p = pp.profile, conds = [], self = this;

    var utOk = s.ut.some(function (u) { return pp.userTypes.has(u); });
    conds.push(cond("user_type", utOk ? MET : UNMET,
      "대상 유형: " + s.ut.map(function (u) { return kg.label(u); }).sort(cmp).join(", ")));

    var regText = "지역: " + kg.regionLabel(s.r);
    if (pp.region === null) {
      var top = kg.isTopRegion(s.r);
      var st0 = top ? MET : (closed ? UNMET : UNKNOWN);
      conds.push(cond("region", st0, regText, st0 === MET ? "전국 사업" : ""));
    } else if (pp.regionUp.has(s.r)) {
      conds.push(cond("region", MET, regText, kg.regionLabel(pp.region) + " 거주"));
    } else if (kg.regionAncestors(s.r).has(pp.region) && !closed) {
      conds.push(cond("region", UNKNOWN, regText, "세부 거주지(구) 확인 필요"));
    } else {
      conds.push(cond("region", UNMET, regText, kg.regionLabel(pp.region) + " 거주"));
    }

    if (s.amin !== undefined || s.amax !== undefined) {
      var lo = s.amin !== undefined ? s.amin + "세 이상" : "", hi = s.amax !== undefined ? s.amax + "세 이하" : "";
      var text = "연령: " + [lo, hi].filter(Boolean).join(" ");
      if (p.age === null || p.age === undefined) conds.push(cond("age", closed ? MET : UNKNOWN, text, ""));
      else {
        var bad = (s.amin !== undefined && p.age < s.amin) || (s.amax !== undefined && p.age > s.amax);
        conds.push(cond("age", bad ? UNMET : MET, text, p.age + "세"));
      }
    }

    if (s.imax !== undefined || s.imin !== undefined) {
      var parts = [];
      if (s.imin !== undefined) parts.push(s.imin + "% 초과");
      if (s.imax !== undefined) parts.push(s.imax + "% 이하");
      var itext = "기준중위소득 " + parts.join(" ");
      if (p.incomePct === null || p.incomePct === undefined) conds.push(cond("income", closed ? MET : UNKNOWN, itext, ""));
      else {
        var ibad = (s.imax !== undefined && s.imax < p.incomePct) || (s.imin !== undefined && s.imin >= p.incomePct);
        conds.push(cond("income", ibad ? UNMET : MET, itext, "중위소득 " + fmtNum(p.incomePct) + "%"));
      }
    }

    if (s.g) {
      if (!p.gender) conds.push(cond("gender", closed ? MET : UNKNOWN, "성별: " + s.g, ""));
      else conds.push(cond("gender", p.gender === s.g ? MET : UNMET, "성별: " + s.g, p.gender));
    }

    s.req.forEach(function (c) {
      var st = self._status(c, pp, closed);
      var ev = st === MET ? self._why(c, pp) : (pp.deny.has(c) ? "해당하지 않음" : "");
      var via = st === MET ? Array.from(pp.haveRoots.get(c) || []).sort(function (a, b) { return a - b; }) : [];
      conds.push(cond("requires", st, "필수: " + kg.label(c), ev, via,
        st === MET ? [c] : [], st === UNKNOWN ? [kg.label(c)] : []));
    });

    s.grp.forEach(function (members) {
      var sts = members.map(function (c) { return self._status(c, pp, closed); });
      var text = "하나 이상: " + members.map(function (c) { return kg.label(c); }).join(" | ");
      var met = members.filter(function (c, k) { return sts[k] === MET; });
      if (met.length) {
        var via = new Set();
        met.forEach(function (c) { (pp.haveRoots.get(c) || []).forEach(function (r) { via.add(r); }); });
        conds.push(cond("anyOf", MET, text, self._why(met[0], pp), sortedUnique(Array.from(via)), met));
      } else if (sts.every(function (x) { return x === UNMET; })) {
        conds.push(cond("anyOf", UNMET, text, "모두 해당하지 않음"));
      } else {
        var pend = members.filter(function (c, k) { return sts[k] === UNKNOWN; }).map(function (c) { return kg.label(c); });
        conds.push(cond("anyOf", UNKNOWN, text, "", [], [], pend));
      }
    });

    var caveats = [];
    s.exc.forEach(function (c) {
      if (pp.have.has(c)) conds.push(cond("excludes", UNMET, "제외: " + kg.label(c), self._why(c, pp)));
      else if (!closed && !pp.deny.has(c)) caveats.push(kg.label(c));
    });

    var preferred = s.pref.filter(function (c) { return pp.have.has(c); })
      .map(function (c) { return kg.label(c); }).sort(cmp);

    var statuses = new Set(conds.map(function (c) { return c.status; }));
    var tier = statuses.has(UNMET) ? INELIGIBLE : statuses.has(UNKNOWN) ? NEEDS : ELIGIBLE;
    return {
      service: s, tier: tier, conditions: conds, preferred: preferred, caveats: caveats,
      apply: today ? applyStatus(s, today) : null
    };
  };

  function unknowns(j) { return j.conditions.filter(function (c) { return c.status === UNKNOWN; }); }
  function matchedCount(j) {
    return j.conditions.filter(function (c) {
      return (c.kind === "requires" || c.kind === "anyOf") && c.status === MET;
    }).length;
  }
  function targeted(s) { return s.req.length > 0 || s.grp.length > 0; }

  /* opts: {mode:'open'|'closed', targetedOnly, includeIneligible, today:'YYYY-MM-DD'} */
  Judge.prototype.run = function (profile, opts) {
    opts = opts || {};
    var t0 = Date.now(), kg = this.kg, closed = opts.mode === "closed", self = this;
    var pp = this.prepare(profile), out = [];
    kg.services.forEach(function (s) {
      if (opts.targetedOnly && !targeted(s)) return;
      var j = self.judgeService(s, pp, closed, opts.today || null);
      if (j.tier === INELIGIBLE && !opts.includeIneligible) return;
      j.unknownCount = unknowns(j).length;
      j.matched = matchedCount(j);
      j.regionDepth = kg.regionAncestors(s.r).size;
      out.push(j);
    });
    out.sort(function (a, b) {                     // judge.py Judge.run 의 정렬 키와 같다
      return (TIER_ORDER[a.tier] - TIER_ORDER[b.tier]) || (a.unknownCount - b.unknownCount) ||
        (b.matched - a.matched) || (b.preferred.length - a.preferred.length) ||
        ((targeted(a.service) ? 0 : 1) - (targeted(b.service) ? 0 : 1)) ||
        (b.regionDepth - a.regionDepth) || cmp(a.service.t, b.service.t) || cmp(a.service.id, b.service.id);
    });
    return { judgments: out, prepared: pp, ms: Date.now() - t0 };
  };

  function applyStatus(s, today) {               // judge.py apply_status 와 같다. today: 'YYYY-MM-DD'
    var k = s.dk;
    if (k === "ALWAYS") return APPLY.OPEN;
    if (k === "NO_APPLICATION") return APPLY.NO_APPLICATION;
    if (k === "RANGE" || k === "FROM" || k === "UNTIL") {
      if (s.as && s.as > today) return APPLY.UPCOMING;
      if (s.ae && s.ae < today) return APPLY.CLOSED;
      return APPLY.OPEN;
    }
    if (k === "ANNUAL" && s.aem !== undefined) {
      var m = Number(today.slice(5, 7)), sm = s.asm, em = s.aem, ok;
      if (sm === undefined) ok = m <= em;
      else if (sm <= em) ok = sm <= m && m <= em;
      else ok = m >= sm || m <= em;
      return ok ? APPLY.OPEN : APPLY.CLOSED;
    }
    return APPLY.UNKNOWN;
  }

  function missingInfo(judgments, top, skip) {    // judge.py missing_info 와 같다
    var names = { age: "나이", income: "소득(기준중위소득 %)", gender: "성별", region: "거주 구" };
    var cnt = new Map();
    judgments.forEach(function (j) {
      if (j.tier !== NEEDS) return;
      var seen = new Set();
      unknowns(j).forEach(function (c) {
        if (names[c.kind]) seen.add(names[c.kind]);
        else if (c.kind === "requires" || c.kind === "anyOf") {
          var parts = c.pending.length ? c.pending : c.text.split(": ").slice(1).join(": ").split(" | ");
          if (skip && parts.every(function (x) { return skip.has(x); })) return;
          seen.add(parts.join(" | "));
        }
      });
      seen.forEach(function (n) { cnt.set(n, (cnt.get(n) || 0) + 1); });
    });
    return Array.from(cnt.entries()).sort(function (a, b) { return (b[1] - a[1]) || cmp(a[0], b[0]); })
      .slice(0, top || 10);
  }

  // ------------------------------------------------------------------ 상담 (pipeline.py 의 웹 판)
  function Consultant(kg) {
    var self = this;
    this.kg = kg; this.judge = new Judge(kg);
    var genericLabels = new Set(kg.meta.generic_labels);
    this.generic = new Set();
    kg.concepts.forEach(function (c, i) {
      if (genericLabels.has(c.l) || /거주자$/.test(c.l) || /주민$/.test(c.l)) self.generic.add(i);
    });
    this.orgLabels = new Set(kg.concepts.filter(function (c) { return c.o; }).map(function (c) { return c.l; }));
  }

  /* 나이·성별·지역에서 나오는 범주 (linker.derive). 반환: [{label, concepts:[idx], note}] */
  Consultant.prototype.derive = function (age, gender, region, groupsText) {
    var kg = this.kg, out = [];
    function add(label, note) {
      var cs = kg.conceptsForLabel(label).filter(function (c) { return kg.concepts[c].t; }).sort(function (a, b) { return a - b; });
      if (cs.length) out.push({ label: label, concepts: cs, note: note });
    }
    if (age !== null && age !== undefined) AGE_RULES.forEach(function (r) { if (r[1](age)) add(r[0], age + "세 → " + r[2]); });
    if (gender) add(gender, "성별");
    if (region !== null && region !== undefined) {
      Array.from(kg.regionAncestors(region)).sort(function (a, b) { return cmp(kg.regions[a].k, kg.regions[b].k); })
        .forEach(function (r) {
          var name = kg.regionLabel(r);
          if (name !== "전국" && name !== "서울특별시") add(name + " 거주자", "거주지");
        });
      add("지역주민", "거주지");
    }
    if (!(groupsText || []).some(function (g) { return String(g).indexOf("외국") >= 0; }))
      ASSUMED_FOR_CITIZEN.forEach(function (l) { add(l, "외국인이라고 하지 않으면 국민으로 봄"); });
    return out;
  };

  Consultant.prototype.specific = function (j) {
    var g = this.generic;
    return j.conditions.filter(function (c) {
      return (c.kind === "requires" || c.kind === "anyOf") && c.status === MET &&
        c.matched.some(function (m) { return !g.has(m); });
    }).length;
  };
  Consultant.prototype.genericOnly = function (j) { return j.tier === ELIGIBLE && this.specific(j) === 0; };
  Consultant.prototype.relevant = function (j, keywords) {
    if (!keywords || !keywords.length) return false;
    var hay = j.service.t + " " + j.service.bt;
    return keywords.some(function (k) { return k && hay.indexOf(k) >= 0; });
  };

  /* input: {age, region, incomePct, gender, groups, notGroups, userType, keywords, today}
     반환: {ranked, result, derived, followUps, counts} */
  Consultant.prototype.consult = function (input) {
    var kg = this.kg, self = this;
    var region = input.region === null || input.region === undefined || input.region === "" ? null : kg.resolveRegion(input.region);
    var groupsText = (input.groups || []).map(function (g) { return typeof g === "number" ? kg.label(g) : g; });
    var derived = this.derive(input.age, input.gender, region, groupsText);
    var groups = (input.groups || []).slice();
    derived.forEach(function (d) { d.concepts.forEach(function (c) { groups.push(c); }); });
    var userType = input.userType || "개인";
    var userTypes = userType === "개인" ? ["개인", "가구"] : [userType];
    var result = this.judge.run({
      age: input.age, region: region, incomePct: input.incomePct, gender: input.gender,
      groups: groups, notGroups: input.notGroups || [], userTypes: userTypes
    }, { today: input.today });
    var kw = (input.keywords || []).filter(Boolean);
    var pos = new Map(result.judgments.map(function (j, i) { return [j, i]; }));
    result.judgments.forEach(function (j) {
      j.relevant = self.relevant(j, kw); j.specific = self.specific(j); j.genericOnly = self.genericOnly(j);
    });
    var ranked = result.judgments.slice().sort(function (a, b) {   // pipeline.consult_understood 와 같은 순서
      return ((a.relevant ? 0 : 1) - (b.relevant ? 0 : 1)) || ((a.genericOnly ? 1 : 0) - (b.genericOnly ? 1 : 0)) ||
        (TIER_ORDER[a.tier] - TIER_ORDER[b.tier]) || (pos.get(a) - pos.get(b));
    });
    var rel = ranked.filter(function (j) { return j.relevant; });
    var skip = userType === "개인" || userType === "가구" ? this.orgLabels : new Set();
    var items = missingInfo(rel.length ? rel : ranked, 50, skip);
    var followUps = items.map(function (x) {
      var w = SENSITIVE_WORDS.some(function (s) { return x[0].indexOf(s) >= 0; }) ? 0.5 : 1;
      return { name: x[0], count: x[1], score: x[1] * w, sensitive: w < 1 };
    }).sort(function (a, b) { return (b.score - a.score) || cmp(a.name, b.name); });
    return {
      ranked: ranked, result: result, derived: derived, followUps: followUps,
      counts: {
        eligible: ranked.filter(function (j) { return j.tier === ELIGIBLE; }).length,
        needs: ranked.filter(function (j) { return j.tier === NEEDS; }).length,
        relevant: rel.length
      }
    };
  };

  /* 라벨 검색 (자동완성): 정확 → 앞부분 → 포함, 많이 쓰이는 순 */
  KG.prototype.search = function (q, limit) {
    q = nospace(q || "");
    if (!q) return [];
    var hits = [];
    this.concepts.forEach(function (c, i) {
      if (!c.t || c.o) return;
      var labs = [c.l].concat(c.a || []), best = 9;
      labs.forEach(function (l) {
        var n = nospace(l);
        if (n === q) best = Math.min(best, 0);
        else if (n.indexOf(q) === 0) best = Math.min(best, 1);
        else if (n.indexOf(q) >= 0) best = Math.min(best, 2);
      });
      if (best < 9) hits.push([best, -(c.u || 0), c.l, i]);
    });
    hits.sort(function (a, b) { return (a[0] - b[0]) || (a[1] - b[1]) || cmp(a[2], b[2]); });
    return hits.slice(0, limit || 8).map(function (h) { return h[3]; });
  };

  return {
    KG: KG, Judge: Judge, Consultant: Consultant, applyStatus: applyStatus, missingInfo: missingInfo,
    medianIncome: medianIncome, MEDIAN_2026: MEDIAN_2026,
    MET: MET, UNMET: UNMET, UNKNOWN: UNKNOWN, ELIGIBLE: ELIGIBLE, NEEDS: NEEDS, INELIGIBLE: INELIGIBLE, APPLY: APPLY
  };
});
