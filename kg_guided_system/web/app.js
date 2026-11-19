/* 화면: 조건 패널 → KGJudge.Consultant.consult → 결과 카드. 판정은 모두 브라우저 안에서 한다. */
(function () {
  "use strict";
  var K = window.KGJudge;
  var $ = function (id) { return document.getElementById(id); };
  var STORE_KEY = "kg-service-finder:v1";
  var PAGE = 20;

  var EXAMPLE = {
    age: 22, region: "region_서울_중구", gender: "", income: "", household: "", userType: "개인",
    groups: { "대학생": "yes", "기초생활수급자": "yes", "장애인": "no" }, keywords: "월세"
  };

  var kg, consultant, chipLabels = [], state, today = localToday(), lastOut = null;
  var view = { tab: "eligible", shown: PAGE, showGeneric: false };

  function localToday() {
    var d = new Date();
    return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
  }
  function esc(s) {
    return String(s === null || s === undefined ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function load() { try { return JSON.parse(localStorage.getItem(STORE_KEY) || "null"); } catch (e) { return null; } }
  function save() { try { localStorage.setItem(STORE_KEY, JSON.stringify(state)); } catch (e) { /* 저장 불가: 무시 */ } }
  function clone(o) { return JSON.parse(JSON.stringify(o)); }

  // ------------------------------------------------------------------ 시작
  fetch("data/kg.json").then(function (r) {
    if (!r.ok) throw new Error("HTTP " + r.status);
    return r.json();
  }).then(function (data) {
    kg = new K.KG(data);
    consultant = new K.Consultant(kg);
    buildRegionSelect();
    buildChipLabels();
    var saved = load();
    state = saved && saved.groups ? saved : clone(EXAMPLE);
    state.isExample = !saved;
    bind();
    syncForm();
    run();
  }).catch(function (e) {
    $("loading").textContent = "지식그래프 파일(data/kg.json)을 불러오지 못했습니다: " + e.message +
      ". 이 폴더를 웹 서버로 열어야 합니다 (예: python -m http.server).";
  });

  function buildRegionSelect() {
    var sel = $("region"), opts = ['<option value="">선택 안 함</option>'];
    var seoul = kg.regions.filter(function (r) { return r.k.indexOf("region_서울_") === 0; })
      .sort(function (a, b) { return a.l < b.l ? -1 : 1; });
    opts.push('<option value="region_서울">서울 (구는 모름)</option>');
    seoul.forEach(function (r) { opts.push('<option value="' + esc(r.k) + '">' + esc(r.l) + "</option>"); });
    sel.innerHTML = opts.join("");
  }

  // 자주 쓰이는 대상 범주 (일반 범주·법인 계열·'…거주자' 제외)
  function buildChipLabels() {
    chipLabels = kg.concepts
      .filter(function (c) { return c.t && !c.o && c.u && !/거주자$|주민$/.test(c.l) && kg.meta.generic_labels.indexOf(c.l) < 0; })
      .sort(function (a, b) { return (b.u - a.u) || (a.l < b.l ? -1 : 1); })
      .slice(0, 22).map(function (c) { return c.l; });
  }

  // ------------------------------------------------------------------ 입력
  function bind() {
    $("age").addEventListener("input", function (e) { state.age = e.target.value === "" ? "" : Number(e.target.value); changed(); });
    $("region").addEventListener("change", function (e) { state.region = e.target.value; changed(); });
    $("income").addEventListener("input", function (e) { state.income = e.target.value; changed(); });
    $("household").addEventListener("input", function (e) { state.household = e.target.value; changed(); });
    $("keywords").addEventListener("input", function (e) { state.keywords = e.target.value; changed(); });
    segment("gender", function (v) { state.gender = v; });
    segment("usertype", function (v) { state.userType = v; });
    $("chips").addEventListener("click", function (e) {
      var b = e.target.closest(".chip");
      if (!b) return;
      cycle(b.dataset.label);
    });
    $("clear-all").addEventListener("click", function () {
      state = { age: "", region: "", gender: "", income: "", household: "", userType: "개인", groups: {}, keywords: "", isExample: false };
      syncForm(); changed();
      $("age").focus();
    });
    $("profile").addEventListener("submit", function (e) { e.preventDefault(); });
    bindSearch();
  }

  function segment(id, set) {
    $(id).addEventListener("click", function (e) {
      var b = e.target.closest("button");
      if (!b) return;
      set(b.dataset.v);
      syncSeg(id, b.dataset.v);
      changed();
    });
  }
  function syncSeg(id, v) {
    $(id).querySelectorAll("button").forEach(function (b) { b.setAttribute("aria-pressed", String(b.dataset.v === v)); });
  }

  function cycle(label) {
    var cur = state.groups[label];
    if (!cur) state.groups[label] = "yes";
    else if (cur === "yes") state.groups[label] = "no";
    else delete state.groups[label];
    renderChips(); changed();
  }

  var timer = null;
  function changed() {
    state.isExample = false;
    $("example-note").hidden = true;
    renderIncome();
    view.shown = PAGE;
    clearTimeout(timer);
    timer = setTimeout(function () { save(); run(); }, 150);
  }

  function syncForm() {
    $("age").value = state.age === "" || state.age === null ? "" : state.age;
    $("region").value = state.region || "";
    $("income").value = state.income || "";
    $("household").value = state.household || "";
    $("keywords").value = state.keywords || "";
    syncSeg("gender", state.gender || "");
    syncSeg("usertype", state.userType || "개인");
    $("example-note").hidden = !state.isExample;
    renderChips(); renderIncome();
  }

  function incomePct() {
    var inc = Number(state.income), size = Number(state.household) || 1;
    if (!state.income || !(inc > 0)) return null;
    return Math.round(inc * 10000 / K.medianIncome(size) * 1000) / 10;
  }
  function renderIncome() {
    var pct = incomePct(), size = Number(state.household) || 1;
    $("income-out").innerHTML = pct === null
      ? "소득을 넣으면 2026년 기준중위소득 대비 비율로 바꿔 판정합니다."
      : "기준중위소득의 <b>" + pct + "%</b> · " + size + "인 가구 기준 " +
        K.medianIncome(size).toLocaleString("ko-KR") + "원" + (state.household ? "" : " (가구원 수를 넣지 않아 1인으로 계산)");
  }

  function renderChips() {
    var labels = chipLabels.slice();
    Object.keys(state.groups).forEach(function (l) { if (labels.indexOf(l) < 0) labels.unshift(l); });
    $("chips").innerHTML = labels.map(function (l) {
      var st = state.groups[l] || "";
      var mark = st === "yes" ? '<span class="mark" aria-hidden="true">✓</span>' : st === "no" ? '<span class="mark" aria-hidden="true">✕</span>' : "";
      var sr = st === "yes" ? " (해당)" : st === "no" ? " (해당 안 됨)" : "";
      return '<button type="button" class="chip" data-label="' + esc(l) + '" data-state="' + st + '" aria-pressed="' +
        (st ? "true" : "false") + '">' + mark + esc(l) + '<span class="sr">' + sr + "</span></button>";
    }).join("");
  }

  // 범주 검색 (자동완성)
  function bindSearch() {
    var q = $("concept-q"), list = $("concept-list"), items = [], active = -1;
    function close() { list.hidden = true; q.setAttribute("aria-expanded", "false"); active = -1; }
    function pick(i) {
      var lab = kg.label(items[i]);
      state.groups[lab] = "yes";
      q.value = ""; close(); renderChips(); changed();
    }
    q.addEventListener("input", function () {
      items = kg.search(q.value, 8);
      if (!items.length) { close(); return; }
      list.innerHTML = items.map(function (i, k) {
        var c = kg.concepts[i];
        return '<li role="option" id="opt-' + k + '" aria-selected="false" data-k="' + k + '"><span>' + esc(c.l) +
          '</span><span class="n">사업 ' + (c.u || 0) + "</span></li>";
      }).join("");
      list.hidden = false; q.setAttribute("aria-expanded", "true"); active = -1;
    });
    q.addEventListener("keydown", function (e) {
      if (list.hidden) return;
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        active = (active + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
        list.querySelectorAll("li").forEach(function (li, k) { li.setAttribute("aria-selected", String(k === active)); });
        q.setAttribute("aria-activedescendant", "opt-" + active);
      } else if (e.key === "Enter") { e.preventDefault(); pick(active < 0 ? 0 : active); }
      else if (e.key === "Escape") close();
    });
    list.addEventListener("mousedown", function (e) {
      var li = e.target.closest("li");
      if (li) { e.preventDefault(); pick(Number(li.dataset.k)); }
    });
    q.addEventListener("blur", function () { setTimeout(close, 100); });
  }

  // ------------------------------------------------------------------ 판정
  function run() {
    var groups = [], notGroups = [];
    Object.keys(state.groups).forEach(function (l) { (state.groups[l] === "yes" ? groups : notGroups).push(l); });
    var kws = (state.keywords || "").split(/[,，\s]+/).map(function (s) { return s.trim(); }).filter(Boolean);
    lastOut = consultant.consult({
      age: state.age === "" || state.age === null ? null : Number(state.age),
      region: state.region || null, gender: state.gender || null, incomePct: incomePct(),
      groups: groups, notGroups: notGroups, userType: state.userType || "개인", keywords: kws, today: today
    });
    lastOut.keywords = kws;
    if (kws.length) {                 // 질문과 관련된 사업이 있는 판정 단계를 먼저 연다
      var relEl = lastOut.ranked.some(function (j) { return j.relevant && j.tier === K.ELIGIBLE; });
      var relNi = lastOut.ranked.some(function (j) { return j.relevant && j.tier === K.NEEDS; });
      view.tab = relEl ? "eligible" : relNi ? "needs" : view.tab;
    }
    renderDerived(lastOut.derived);
    renderResults();
  }

  function renderDerived(derived) {
    $("derived").innerHTML = derived.length
      ? "자동으로 더한 범주: " + derived.map(function (d) {
          return '<b title="' + esc(d.note) + '">' + esc(d.label) + "</b>";
        }).join(" · ") + " (나이·성별·거주지에서 계산)"
      : "";
  }

  function stamp(tier) {
    if (tier === K.ELIGIBLE) return '<div class="stamp ok" aria-label="판정: 적합">적합</div>';
    if (tier === K.NEEDS) return '<div class="stamp warn" aria-label="판정: 확인 필요">확인<br>필요</div>';
    return '<div class="stamp no" aria-label="판정: 부적합">부적합</div>';
  }
  function icon(st) { return st === K.MET ? "✓" : st === K.UNMET ? "✕" : "?"; }
  function cls(st) { return st === K.MET ? "met" : st === K.UNMET ? "unmet" : "unknown"; }

  function card(j) {
    var s = j.service;
    var conds = j.conditions.filter(function (c) { return !(c.kind === "user_type" && c.status === K.MET); });
    var st = s.st.map(function (i) { return kg.label(i); }).join(", ");
    var apply = j.apply ? '<span class="pill' + (j.apply === K.APPLY.OPEN ? " open" : "") + '">' + esc(j.apply) + "</span>" : "";
    return '<article class="card">' +
      '<div class="card-head">' + stamp(j.tier) + "<div>" +
      "<h3>" + esc(s.t) + "</h3>" +
      '<div class="meta"><span>' + esc(s.o) + "</span>" +
      (s.o === kg.regionLabel(s.r) ? "" : "<span>" + esc(kg.regionLabel(s.r)) + "</span>") + "<span>" + esc(s.f) +
      "</span><span>" + esc(st) + "</span>" + apply + "</div></div></div>" +
      '<ul class="conds">' + conds.map(function (c) {
        return '<li class="' + cls(c.status) + '"><span class="ic" aria-label="' + esc(c.status) + '">' + icon(c.status) +
          "</span><span>" + esc(c.text) + (c.evidence ? ' <span class="ev">← ' + esc(c.evidence) + "</span>" : "") + "</span></li>";
      }).join("") + "</ul>" +
      (j.caveats.length ? '<p class="note caveat">다음에 해당하면 제외: ' + esc(j.caveats.join(", ")) + "</p>" : "") +
      (j.preferred.length ? '<p class="note pref">우대: ' + esc(j.preferred.join(", ")) + "</p>" : "") +
      (s.bt ? '<p class="benefit">' + esc(s.bt) + "</p>" : "") +
      '<div class="card-foot">' +
      '<details class="raw"><summary>원문 보기</summary><dl>' +
      "<dt>지원 대상</dt><dd>" + esc(s.tt || "—") + "</dd>" +
      "<dt>지원 내용</dt><dd>" + esc(s.bt || "—") + "</dd>" +
      "<dt>신청 기한</dt><dd>" + esc(s.dt || "—") + (s.bl ? " (예산 소진 시 마감)" : "") + "</dd></dl></details>" +
      (s.url ? '<a href="' + esc(s.url) + '" target="_blank" rel="noopener">정부24에서 보기 ↗</a>' : "") +
      '<span class="id">' + esc(s.id) + "</span></div></article>";
  }

  function renderResults() {
    var out = lastOut, main = $("results");
    var el = out.ranked.filter(function (j) { return j.tier === K.ELIGIBLE; });
    var ni = out.ranked.filter(function (j) { return j.tier === K.NEEDS; });
    var list = view.tab === "eligible" ? el : ni;
    var html = [];

    html.push('<div class="summary" role="tablist" aria-label="판정 결과">' +
      tab("eligible", "적합", el.length) + tab("needs", "확인 필요", ni.length) +
      '<span class="asof">신청 가능 여부는 ' + esc(today) + " 기준</span></div>");

    var fu = out.followUps.slice(0, 3);
    if (fu.length) {
      html.push('<section class="followup" aria-label="더 알려주시면"><h2>이것만 알려주시면 판정이 더 정확해집니다</h2><ul>' +
        fu.map(followItem).join("") + "</ul></section>");
    }

    if (!list.length) {
      html.push('<div class="empty">' + (view.tab === "eligible"
        ? "조건을 모두 충족한 사업이 아직 없습니다. 「확인 필요」에서 무엇을 더 알려주면 되는지 확인해 보세요."
        : "확인이 더 필요한 사업이 없습니다.") + "</div>");
    } else {
      var rel = list.filter(function (j) { return j.relevant; });
      var rest = list.filter(function (j) { return !j.relevant; });
      var generic = view.tab === "eligible" ? rest.filter(function (j) { return j.genericOnly; }) : [];
      rest = rest.filter(function (j) { return generic.indexOf(j) < 0; });
      var budget = view.shown;
      if (out.keywords.length) {
        html.push('<h2 class="group-title">「' + esc(out.keywords.join(", ")) + "」와 관련된 사업 " + rel.length + "건</h2>");
        if (!rel.length) html.push('<div class="empty">이 단어가 사업명이나 지원 내용에 있는 사업이 이 판정 단계에는 없습니다.</div>');
        html.push(cards(rel.slice(0, budget)));
        budget -= Math.min(rel.length, budget);
        if (rest.length) html.push('<h2 class="group-title">그 밖의 사업 ' + rest.length + "건</h2>");
      }
      html.push(cards(rest.slice(0, Math.max(budget, 0))));
      var hidden = (rel.length + rest.length) - Math.min(rel.length + rest.length, view.shown);
      if (hidden > 0) html.push('<button type="button" class="btn more" id="more">' + Math.min(PAGE, hidden) + "건 더 보기 (남은 " + hidden + "건)</button>");
      if (generic.length) {
        html.push('<button type="button" class="btn generic-toggle" id="generic-toggle" aria-expanded="' + view.showGeneric + '">' +
          (view.showGeneric ? "접기: " : "펼치기: ") + "누구나 받을 수 있는 사업 " + generic.length + "건 (대한민국 국민·일반인 등으로만 충족)</button>");
        if (view.showGeneric) html.push(cards(generic.slice(0, 60)));
      }
    }
    main.innerHTML = html.join("");
    $("jump").innerHTML = '결과 보기 <b>적합 ' + el.length.toLocaleString("ko-KR") + "</b> · 확인 필요 " +
      ni.length.toLocaleString("ko-KR") + " ↓";

    main.querySelectorAll(".tab").forEach(function (b) {
      b.addEventListener("click", function () { view.tab = b.dataset.tab; view.shown = PAGE; renderResults(); });
    });
    var more = $("more");
    if (more) more.addEventListener("click", function () { view.shown += PAGE; renderResults(); });
    var gt = $("generic-toggle");
    if (gt) gt.addEventListener("click", function () { view.showGeneric = !view.showGeneric; renderResults(); });
    main.querySelectorAll("[data-answer]").forEach(function (b) {
      b.addEventListener("click", function () { answer(b.dataset.name, b.dataset.answer); });
    });
  }

  function tab(key, label, n) {
    return '<button type="button" class="tab" role="tab" data-tab="' + key + '" aria-selected="' + (view.tab === key) + '">' +
      esc(label) + ' <span class="num">' + n.toLocaleString("ko-KR") + "</span></button>";
  }
  function cards(js) { return js.length ? '<div class="cards">' + js.map(card).join("") + "</div>" : ""; }

  var BASIC = { "나이": "age", "소득(기준중위소득 %)": "income", "성별": "gender", "거주 구": "region" };
  function followItem(f) {
    var n = "<small>(" + (lastOut.keywords.length ? "관련 사업 " : "") + f.count + "건의 판정이 달라짐)</small>";
    if (BASIC[f.name]) {
      var q = { age: "나이를 알려주세요.", income: "가구 월소득과 가구원 수를 알려주세요.", gender: "성별을 알려주세요.", region: "어느 구에 사시나요?" }[BASIC[f.name]];
      return '<li><span class="q">' + q + " " + n + '</span><span class="acts"><button type="button" class="btn primary" data-answer="focus" data-name="' +
        esc(f.name) + '">입력하기</button></span></li>';
    }
    var parts = f.name.split(" | ");
    var q2 = parts.length > 1 ? "다음 중 해당하는 것이 있나요? " + esc(parts.join(", ")) : "「" + esc(f.name) + "」에 해당하시나요?";
    var sens = f.sensitive ? " <small>답하지 않아도 됩니다.</small>" : "";
    var acts = parts.length > 1
      ? parts.slice(0, 4).map(function (p) { return '<button type="button" class="btn" data-answer="yes" data-name="' + esc(p) + '">' + esc(p) + "</button>"; }).join("") +
        '<button type="button" class="btn" data-answer="none" data-name="' + esc(f.name) + '">모두 아님</button>'
      : '<button type="button" class="btn primary" data-answer="yes" data-name="' + esc(f.name) + '">해당해요</button>' +
        '<button type="button" class="btn" data-answer="no" data-name="' + esc(f.name) + '">아니에요</button>';
    return '<li><span class="q">' + q2 + " " + n + sens + '</span><span class="acts">' + acts + "</span></li>";
  }

  function answer(name, how) {
    if (how === "focus") {
      var id = { "나이": "age", "소득(기준중위소득 %)": "income", "성별": "gender", "거주 구": "region" }[name];
      var el = id === "gender" ? $("gender").querySelector("button") : $(id);
      el.scrollIntoView({ block: "center" }); el.focus();
      return;
    }
    if (how === "none") name.split(" | ").forEach(function (p) { state.groups[p] = "no"; });
    else state.groups[name] = how;
    renderChips(); changed();
  }
})();
