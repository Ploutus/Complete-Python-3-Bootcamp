(() => {
  "use strict";

  const state = {
    rows: [],
    sectors: [],
    filter: { stage: null, sector: null },
    sort: { key: "rs_rating", dir: "desc" },
    selectedTicker: null,
    activeTab: "screener",
    thirteenF: null,
  };

  const STAGE_ORDER = [2, 1, 3, 4]; // advancing first, then basing, topping, declining
  const STAGE_LABELS = {
    1: "STAGE 1 · BASE",
    2: "STAGE 2 · ADVANCE",
    3: "STAGE 3 · TOP",
    4: "STAGE 4 · DECLINE",
  };

  const el = (id) => document.getElementById(id);
  const fmtPct = (v) => `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
  const fmtMoney = (v) => "$" + Number(v).toLocaleString("en-US", { maximumFractionDigits: 0 });
  const cls = (v) => (v >= 0 ? "up" : "down");

  async function fetchJSON(url) {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`${url} -> ${res.status}`);
    return res.json();
  }

  function tick() {
    el("clock").textContent = new Date().toLocaleTimeString("da-DK");
  }

  // ---------------- Stage cards ----------------
  function renderStageCards() {
    const counts = { 1: 0, 2: 0, 3: 0, 4: 0 };
    state.rows.forEach((r) => counts[r.stage]++);
    const wrap = el("stageCards");
    wrap.innerHTML = "";
    STAGE_ORDER.forEach((stage) => {
      const card = document.createElement("div");
      card.className = `stage-card stage-${stage}` + (state.filter.stage === stage ? " active" : "");
      card.innerHTML = `<span class="stage-name">${STAGE_LABELS[stage]}</span><span class="stage-count">${counts[stage]}</span>`;
      card.addEventListener("click", () => {
        state.filter.stage = state.filter.stage === stage ? null : stage;
        render();
      });
      wrap.appendChild(card);
    });
  }

  // ---------------- Sector strength ----------------
  function renderSectorList() {
    const wrap = el("sectorList");
    wrap.innerHTML = "";
    const maxRs = Math.max(...state.sectors.map((s) => s.avg_rs), 1);
    state.sectors.forEach((s) => {
      const row = document.createElement("div");
      row.className = "sector-row" + (state.filter.sector === s.sector ? " active" : "");
      row.innerHTML = `
        <span class="sector-name">${s.sector}</span>
        <span class="sector-rs">${s.avg_rs}</span>
        <span class="sector-chg ${cls(s.avg_rs_change)}">${s.avg_rs_change >= 0 ? "▲" : "▼"}${Math.abs(s.avg_rs_change)}</span>
        <div class="bar-track"><div class="bar-fill" style="width:${(s.avg_rs / maxRs) * 100}%"></div></div>
      `;
      row.addEventListener("click", () => {
        state.filter.sector = state.filter.sector === s.sector ? null : s.sector;
        render();
      });
      wrap.appendChild(row);
    });
  }

  // ---------------- Filter bar ----------------
  function renderFilterBar() {
    const bar = el("filterBar");
    const label = el("filterLabel");
    const breakdown = el("stageSectorBreakdown");
    breakdown.innerHTML = "";

    if (!state.filter.stage && !state.filter.sector) {
      bar.classList.add("hidden");
      return;
    }
    bar.classList.remove("hidden");

    const parts = [];
    if (state.filter.stage) parts.push(STAGE_LABELS[state.filter.stage]);
    if (state.filter.sector) parts.push(state.filter.sector.toUpperCase());
    label.textContent = "FILTER: " + parts.join(" + ");

    if (state.filter.stage && !state.filter.sector) {
      const inStage = state.rows.filter((r) => r.stage === state.filter.stage);
      const counts = {};
      inStage.forEach((r) => (counts[r.sector] = (counts[r.sector] || 0) + 1));
      const entries = Object.entries(counts).sort((a, b) => b[1] - a[1]);
      entries.forEach(([sector, count]) => {
        const chip = document.createElement("span");
        chip.className = "breakdown-chip";
        chip.textContent = `${sector} (${count})`;
        chip.addEventListener("click", () => {
          state.filter.sector = sector;
          render();
        });
        breakdown.appendChild(chip);
      });
    }
  }

  // ---------------- Main grid ----------------
  function filteredRows() {
    return state.rows.filter((r) => {
      if (state.filter.stage && r.stage !== state.filter.stage) return false;
      if (state.filter.sector && r.sector !== state.filter.sector) return false;
      return true;
    });
  }

  function sortRows(rows) {
    const { key, dir } = state.sort;
    const sorted = [...rows].sort((a, b) => {
      const av = a[key], bv = b[key];
      if (typeof av === "string") return dir === "asc" ? av.localeCompare(bv) : bv.localeCompare(av);
      return dir === "asc" ? av - bv : bv - av;
    });
    return sorted;
  }

  function renderGrid() {
    const rows = sortRows(filteredRows());
    const body = el("stockGridBody");
    body.innerHTML = "";
    rows.forEach((r) => {
      const tr = document.createElement("tr");
      if (r.ticker === state.selectedTicker) tr.classList.add("selected");
      tr.innerHTML = `
        <td><div class="rs-cell"><span class="rs-num">${r.rs_rating}</span><div class="rs-bar-track"><div class="rs-bar-fill" style="width:${r.rs_rating}%"></div></div></div></td>
        <td><b>${r.ticker}</b></td>
        <td>${r.name}</td>
        <td>${r.sector}</td>
        <td><span class="stage-pill stage-${r.stage}">${r.stage_label.split("·")[0].trim()}</span></td>
        <td>${r.price.toFixed(2)}</td>
        <td class="${cls(r.day_chg_pct)}">${fmtPct(r.day_chg_pct)}</td>
        <td class="${cls(r.week_chg_pct)}">${fmtPct(r.week_chg_pct)}</td>
      `;
      tr.addEventListener("click", () => selectTicker(r.ticker));
      body.appendChild(tr);
    });

    document.querySelectorAll("#stockGrid th.sortable").forEach((th) => {
      th.classList.remove("sorted-asc", "sorted-desc");
      if (th.dataset.sort === state.sort.key) {
        th.classList.add(state.sort.dir === "asc" ? "sorted-asc" : "sorted-desc");
      }
    });
  }

  function bindSort() {
    document.querySelectorAll("#stockGrid th.sortable").forEach((th) => {
      th.addEventListener("click", () => {
        const key = th.dataset.sort;
        if (state.sort.key === key) {
          state.sort.dir = state.sort.dir === "asc" ? "desc" : "asc";
        } else {
          state.sort.key = key;
          state.sort.dir = "desc";
        }
        renderGrid();
      });
    });
  }

  // ---------------- 13F tab ----------------
  function renderThirteenF() {
    if (!state.thirteenF) return;
    el("thirteenfQuarter").textContent = `Seneste kvartal: ${state.thirteenF.latest_quarter} — ${state.thirteenF.latest.length} positioner`;
    const body = el("thirteenfGridBody");
    body.innerHTML = "";
    state.thirteenF.latest.forEach((h) => {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>${h.filer}</td>
        <td><b>${h.ticker}</b></td>
        <td>${Number(h.shares).toLocaleString("en-US")}</td>
        <td>${fmtMoney(h.value)}</td>
        <td class="${cls(h.change_pct)}">${h.change_pct >= 0 ? "+" : ""}${h.change_pct}%</td>
        <td>${h.action}</td>
      `;
      tr.addEventListener("click", () => {
        setTab("screener");
        selectTicker(h.ticker);
      });
      body.appendChild(tr);
    });
  }

  function setTab(tab) {
    state.activeTab = tab;
    document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === tab));
    el("screenerView").classList.toggle("hidden", tab !== "screener");
    el("thirteenfView").classList.toggle("hidden", tab !== "thirteenf");
  }

  // ---------------- Detail / chart panel ----------------
  async function selectTicker(ticker) {
    state.selectedTicker = ticker;
    renderGrid();
    const meta = state.rows.find((r) => r.ticker === ticker);
    el("detailTitle").textContent = `${ticker} — ${meta ? meta.name : ""}`;

    try {
      const [ohlc, f13] = await Promise.all([
        fetchJSON(`/api/ohlc/${ticker}`),
        fetchJSON(`/api/13f/${ticker}`),
      ]);
      renderDetailMeta(ohlc, meta);
      drawChart(el("chartCanvas"), ohlc.bars);
      renderDetail13f(f13.holdings);
    } catch (e) {
      console.error(e);
    }
  }

  function renderDetailMeta(ohlc, meta) {
    el("detailMeta").innerHTML = `
      <span>Sektor: <b>${ohlc.sector}</b></span>
      <span>Stage: <b>${ohlc.stage_label}</b></span>
      <span>RS Rating: <b>${ohlc.rs_rating}</b></span>
      ${meta ? `<span>1D: <b class="${cls(meta.day_chg_pct)}">${fmtPct(meta.day_chg_pct)}</b></span>` : ""}
    `;
  }

  function renderDetail13f(rows) {
    const body = el("detail13fBody");
    body.innerHTML = "";
    if (!rows.length) {
      body.innerHTML = `<tr><td colspan="4" style="color:var(--text-dim)">Ingen institutionelle positioner fundet.</td></tr>`;
      return;
    }
    rows.slice().reverse().slice(0, 12).forEach((h) => {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>${h.filer}</td>
        <td>${h.quarter}</td>
        <td>${Number(h.shares).toLocaleString("en-US")}</td>
        <td class="${cls(h.change_pct)}">${h.change_pct >= 0 ? "+" : ""}${h.change_pct}%</td>
      `;
      body.appendChild(tr);
    });
  }

  function drawChart(canvas, bars) {
    const dpr = window.devicePixelRatio || 1;
    const cssWidth = canvas.clientWidth || 640;
    const cssHeight = canvas.clientHeight || 220;
    canvas.width = cssWidth * dpr;
    canvas.height = cssHeight * dpr;
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cssWidth, cssHeight);

    const visible = bars.slice(-104); // ~2 years of weekly bars
    if (!visible.length) return;

    const padL = 46, padR = 10, padT = 10, padB = 18;
    const plotW = cssWidth - padL - padR;
    const plotH = cssHeight - padT - padB;

    let lo = Infinity, hi = -Infinity;
    visible.forEach((b) => {
      lo = Math.min(lo, b.low, b.ma30w ?? b.low);
      hi = Math.max(hi, b.high, b.ma30w ?? b.high);
    });
    const pad = (hi - lo) * 0.06 || 1;
    lo -= pad; hi += pad;

    const x = (i) => padL + (i / Math.max(1, visible.length - 1)) * plotW;
    const y = (v) => padT + plotH - ((v - lo) / (hi - lo)) * plotH;

    // gridlines + price labels
    ctx.strokeStyle = "#131a22";
    ctx.fillStyle = "#4a5866";
    ctx.font = "10px monospace";
    const steps = 5;
    for (let s = 0; s <= steps; s++) {
      const price = lo + ((hi - lo) * s) / steps;
      const yy = y(price);
      ctx.beginPath();
      ctx.moveTo(padL, yy);
      ctx.lineTo(cssWidth - padR, yy);
      ctx.stroke();
      ctx.fillText(price.toFixed(0), 2, yy + 3);
    }

    // candles
    const bw = Math.max(2, (plotW / visible.length) * 0.6);
    visible.forEach((b, i) => {
      const xc = x(i);
      const up = b.close >= b.open;
      ctx.strokeStyle = ctx.fillStyle = up ? "#2ecc71" : "#ff4d4f";
      ctx.beginPath();
      ctx.moveTo(xc, y(b.high));
      ctx.lineTo(xc, y(b.low));
      ctx.stroke();
      const bodyTop = y(Math.max(b.open, b.close));
      const bodyBot = y(Math.min(b.open, b.close));
      ctx.fillRect(xc - bw / 2, bodyTop, bw, Math.max(1, bodyBot - bodyTop));
    });

    // MA30W line
    ctx.strokeStyle = "#37e0e0";
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    let started = false;
    visible.forEach((b, i) => {
      if (b.ma30w == null) return;
      const xc = x(i), yc = y(b.ma30w);
      if (!started) { ctx.moveTo(xc, yc); started = true; } else { ctx.lineTo(xc, yc); }
    });
    ctx.stroke();
    ctx.lineWidth = 1;

    // x-axis date labels (first / mid / last)
    ctx.fillStyle = "#4a5866";
    [0, Math.floor(visible.length / 2), visible.length - 1].forEach((i) => {
      ctx.fillText(visible[i].date, x(i) - 20, cssHeight - 4);
    });
  }

  // ---------------- Search ----------------
  function bindSearch() {
    const input = el("tickerSearch");
    const results = el("searchResults");
    input.addEventListener("input", () => {
      const q = input.value.trim().toUpperCase();
      if (!q) { results.classList.add("hidden"); return; }
      const matches = state.rows
        .filter((r) => r.ticker.includes(q) || r.name.toUpperCase().includes(q))
        .slice(0, 8);
      if (!matches.length) { results.classList.add("hidden"); return; }
      results.innerHTML = "";
      matches.forEach((r) => {
        const div = document.createElement("div");
        div.textContent = `${r.ticker} — ${r.name} (RS ${r.rs_rating})`;
        div.addEventListener("click", () => {
          setTab("screener");
          selectTicker(r.ticker);
          input.value = "";
          results.classList.add("hidden");
        });
        results.appendChild(div);
      });
      results.classList.remove("hidden");
    });
    document.addEventListener("click", (e) => {
      if (!input.contains(e.target) && !results.contains(e.target)) results.classList.add("hidden");
    });
  }

  // ---------------- Wiring ----------------
  function render() {
    renderStageCards();
    renderSectorList();
    renderFilterBar();
    renderGrid();
  }

  async function boot() {
    tick();
    setInterval(tick, 1000);

    document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => setTab(t.dataset.tab)));
    el("clearFilter").addEventListener("click", () => {
      state.filter = { stage: null, sector: null };
      render();
    });
    bindSort();
    bindSearch();

    const [universe, sectors, insights, f13] = await Promise.all([
      fetchJSON("/api/universe"),
      fetchJSON("/api/sectors"),
      fetchJSON("/api/insights"),
      fetchJSON("/api/13f"),
    ]);
    state.rows = universe.rows;
    state.sectors = sectors.sectors;
    state.thirteenF = f13;

    el("insightsTickerInner").textContent = insights.insights.join("     •     ");

    render();
    renderThirteenF();

    if (state.rows.length) selectTicker(state.rows[0].ticker);
  }

  boot().catch((e) => {
    console.error(e);
    document.body.innerHTML = `<pre style="color:#ff4d4f;padding:20px">Fejl ved indlæsning af dashboard: ${e}</pre>`;
  });
})();
