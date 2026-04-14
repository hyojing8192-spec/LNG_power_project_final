/* ═══════════════════════════════════════════════════════
   main.js  —  LNG-OPT 대시보드 SPA
   ═══════════════════════════════════════════════════════ */

// ── 전역 상태 ───────────────────────────────────────────
const STATE = {
  activePage: 'dashboard',
  analysisData: null,
  initData: null,
  loading: false,
  schedulerStatus: 'stopped',
};

// ── 차트 인스턴스 ─────────────────────────────────────
const CHARTS = {};

// ── 초기화 ─────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', async () => {
  setupNav();
  await loadInit();
  await runAnalysis();
  startSchedulerPoll();
});

// ── 네비게이션 ─────────────────────────────────────────
function setupNav() {
  document.querySelectorAll('.nav-item').forEach(el => {
    el.addEventListener('click', () => {
      const page = el.dataset.page;
      setActivePage(page);
    });
  });
}

function setActivePage(page) {
  STATE.activePage = page;
  document.querySelectorAll('.nav-item').forEach(el => {
    el.classList.toggle('active', el.dataset.page === page);
  });
  document.querySelectorAll('.page').forEach(el => {
    el.style.display = el.id === `page-${page}` ? '' : 'none';
  });
  // 특정 페이지 진입 시 추가 로드
  if (page === 'ml') loadMLMetrics();
  if (page === 'rawdata') loadRawData();
  if (page === 'econ' && STATE.analysisData) renderEconPage(STATE.analysisData);
  if (page === 'anomaly') {
    const chartData = STATE.analysisData?.anomaly?.chart?.smp_series;
    if (chartData?.length) {
      renderAnomalyDirect(STATE.analysisData.anomaly);
      renderEconChangePage(STATE.analysisData);
    } else {
      loadAnomalyChart();
    }
  }
}

// ── Init API ────────────────────────────────────────────
async function loadInit() {
  try {
    const res = await fetch('/api/init');
    STATE.initData = await res.json();

    // localStorage에 저장된 값 우선, 없으면 서버 기본값
    const savedDate  = localStorage.getItem('lng_opt_date');
    const savedPrice = localStorage.getItem('lng_opt_price');
    const savedSpot  = localStorage.getItem('lng_opt_spot');

    document.getElementById('input-date').value =
      savedDate  || STATE.initData.default_date;
    document.getElementById('input-lng-price').value =
      savedPrice != null ? savedPrice : STATE.initData.default_lng_price;

    if (savedSpot !== null) {
      const isSpot = savedSpot === 'true';
      document.getElementById('input-spot').checked = isSpot;
      document.getElementById('spot-track').classList.toggle('on', isSpot);
    }

    // 우측 패널 자동 산출값
    document.getElementById('rp-heat').textContent =
      `${STATE.initData.lng_heat} Mcal/Nm³`;
    document.getElementById('rp-rate').textContent =
      `${Number(STATE.initData.exchange_rate).toLocaleString('ko')} 원/$`;
  } catch (e) {
    console.error('Init 실패:', e);
  }
}

// ── 분석 실행 ─────────────────────────────────────────
async function runAnalysis() {
  if (STATE.loading) return;
  STATE.loading = true;
  showLoading(true);

  const targetDate = document.getElementById('input-date').value;
  const lngPrice   = parseFloat(document.getElementById('input-lng-price').value) || 13.5;
  const isSpot     = document.getElementById('input-spot').checked;

  // 입력값 localStorage에 저장 (새로고침 후에도 유지)
  localStorage.setItem('lng_opt_date',  targetDate);
  localStorage.setItem('lng_opt_price', lngPrice);
  localStorage.setItem('lng_opt_spot',  isSpot);

  try {
    const res = await fetch('/api/analysis', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ target_date: targetDate, lng_price: lngPrice, is_spot: isSpot }),
    });
    const data = await res.json();
    STATE.analysisData = data;
    renderDashboard(data);
    if (STATE.activePage === 'econ')    renderEconPage(data);
    if (STATE.activePage === 'anomaly') { renderAnomalyPage(data); renderEconChangePage(data); }
    updateSMPStatus(data.smp_status || []);
  } catch (e) {
    console.error('분석 실패:', e);
    showError('분석 중 오류가 발생했습니다: ' + e.message);
  } finally {
    STATE.loading = false;
    showLoading(false);
  }
}

// ── 로딩 표시 ─────────────────────────────────────────
function showLoading(on) {
  const btn = document.getElementById('btn-analyze');
  if (btn) {
    btn.disabled = on;
    btn.textContent = on ? '⏳ 분석 중...' : '▶  분석 실행';
  }
}
function showError(msg) {
  const el = document.getElementById('main-error');
  if (el) { el.textContent = msg; el.style.display = msg ? '' : 'none'; }
}

// ══════════════════════════════════════════════════════
// 대시보드 렌더링
// ══════════════════════════════════════════════════════
function renderDashboard(data) {
  if (!data.has_smp) {
    renderUnavailable(data);
    return;
  }
  document.getElementById('unavail-block').style.display = 'none';
  document.getElementById('dashboard-block').style.display = '';

  renderPrimaryCard(data);
  renderKPICards(data);
  renderMainChart(data);
  renderPlanTables(data);
  renderGuidancePage(data);
}

// ── Primary Card ───────────────────────────────────────
function renderPrimaryCard(data) {
  const k = data.kpis;
  document.getElementById('pc-title').textContent =
    `${data.target_label} 경제성 판단`;
  document.getElementById('pc-value').textContent =
    `${k.avg_smp} 원/kWh`;
  document.getElementById('pc-sub').textContent =
    `평균 SMP  ·  최적 운전모드 ${k.top_mode}`;
  document.getElementById('pc-lng').textContent =
    `${k.lng_price} $/MMBtu (${k.price_type})`;
  document.getElementById('pc-rate').textContent =
    `${Number(k.exchange_rate).toLocaleString('ko')} 원/$`;

  // 임계값
  const thr = data.thresholds || {};
  const thrEl = document.getElementById('pc-threshold');
  if (thrEl) thrEl.textContent =
    `BEP 임계  LNG발전 ${thr.smp_low}원 / 기력 ${thr.smp_high}원`;
}

// ── KPI Cards ──────────────────────────────────────────
function renderKPICards(data) {
  const k = data.kpis;
  const g = data.guidance || {};

  setKPI('kpi-lng',  `${k.lng_price}`, '$/MMBtu', `${k.price_type}`, '💰', null);
  setKPI('kpi-rate', `${Number(k.exchange_rate).toLocaleString('ko')}`, '원/$', '전일 평균', '💱', null);
  setKPI('kpi-smp',  `${k.avg_smp}`, '원/kWh', '평균 SMP', '⚡', null);
  const econ = g.total_econ != null ? `${g.total_econ > 0 ? '+' : ''}${g.total_econ.toFixed(3)} 억원` : '-';
  const econCls = (g.total_econ || 0) >= 0 ? 'badge-pos' : 'badge-neg';
  setKPI('kpi-econ', k.top_mode, '', '최적 운전모드', '🏭', { text: econ, cls: econCls });
}

function setKPI(id, val, unit, lbl, icon, badge) {
  const el = document.getElementById(id);
  if (!el) return;
  el.querySelector('.kpi-icon').textContent = icon;
  el.querySelector('.kpi-val').textContent  = val + (unit ? ' ' + unit : '');
  el.querySelector('.kpi-lbl').textContent  = lbl;
  const bdEl = el.querySelector('.kpi-badge');
  if (bdEl && badge) { bdEl.textContent = badge.text; bdEl.className = `kpi-badge ${badge.cls}`; }
  else if (bdEl) bdEl.textContent = '';
}

// ── 메인 차트 ──────────────────────────────────────────
function renderMainChart(data) {
  const c = data.chart;
  if (!c || !c.labels.length) return;

  // null → undefined (ApexCharts gap 처리)
  const smpSeries  = c.smp.map(v => v ?? null);
  const bepSeries  = c.bep.map(v => v ?? null);
  const lngLine    = c.lng_price_line;

  const opts = {
    chart: {
      id: 'main-chart', type: 'line', height: 340,
      background: 'transparent', toolbar: { show: true },
      animations: { enabled: true, speed: 600 },
    },
    stroke: { width: [3, 0, 2], curve: 'smooth', dashArray: [0, 0, 6] },
    series: [
      { name: 'SMP (원/kWh)',       type: 'line',  data: smpSeries },
      { name: 'LNG발전 BEP ($/MMBtu)', type: 'bar', data: bepSeries },
      { name: `LNG가격 ${lngLine[0]} $/MMBtu`, type: 'line', data: lngLine },
    ],
    xaxis: {
      categories: c.labels,
      labels: { rotate: -45, style: { fontSize: '10px' }, },
      tickAmount: Math.min(c.labels.length, 24),
    },
    yaxis: [
      { title: { text: 'SMP (원/kWh)', style: { fontSize: '11px' } }, labels: { formatter: v => v?.toFixed(0) } },
      { opposite: true, title: { text: '$/MMBtu', style: { fontSize: '11px' } }, labels: { formatter: v => v?.toFixed(1) } },
      { opposite: true, show: false },
    ],
    colors: ['#4F46E5', '#A5B4FC', '#F59E0B'],
    fill: {
      type: ['solid', 'gradient', 'solid'],
      gradient: { shade: 'light', type: 'vertical', opacityFrom: 0.7, opacityTo: 0.2 },
    },
    markers: { size: [3, 0, 0] },
    tooltip: { shared: true, intersect: false, theme: 'light' },
    legend: { position: 'top', horizontalAlign: 'center', fontSize: '12px' },
    grid: { borderColor: 'rgba(0,0,0,0.06)', strokeDashArray: 4 },
    plotOptions: { bar: { columnWidth: '60%', borderRadius: 3 } },
    theme: { mode: 'light' },
  };

  const el = document.getElementById('chart-main');
  if (CHARTS.main) { CHARTS.main.destroy(); }
  CHARTS.main = new ApexCharts(el, opts);
  CHARTS.main.render();
}

// ── 가동계획 테이블 ────────────────────────────────────
function renderPlanTables(data) {
  const container = document.getElementById('plan-tables');
  container.innerHTML = '';

  const pairs = data.date_pairs || [];
  pairs.forEach(pair => {
    // 야간
    const nightDiv = makePeriodBlock(
      pair.night_header, pair.night_available, pair.night_table
    );
    // 주간
    const dayDiv = makePeriodBlock(
      pair.day_header, pair.day_available, pair.day_table
    );
    container.appendChild(nightDiv);
    container.appendChild(dayDiv);
  });
}

function makePeriodBlock(header, available, table) {
  const div = document.createElement('div');
  div.style.marginBottom = '20px';

  const hdr = document.createElement('div');
  hdr.className = 'period-header';
  hdr.textContent = header;
  div.appendChild(hdr);

  if (!available || !table) {
    const info = document.createElement('div');
    info.className = 'alert alert-warn';
    info.style.marginBottom = '8px';
    info.textContent = 'SMP 미공시 — 산출 불가';
    div.appendChild(info);
    return div;
  }

  const wrap = document.createElement('div');
  wrap.className = 'table-wrap glass-card';
  wrap.style.padding = '0';
  wrap.style.overflow = 'auto';

  const tbl = document.createElement('table');
  // 헤더
  const thead = tbl.createTHead();
  const hrow = thead.insertRow();
  const th0 = document.createElement('th');
  th0.className = 'row-header';
  th0.textContent = '항목';
  hrow.appendChild(th0);
  table.columns.forEach(col => {
    const th = document.createElement('th');
    th.textContent = col;
    hrow.appendChild(th);
  });

  // 행
  const tbody = tbl.createTBody();
  const rowKeys = ['최적운전모드', 'SMP', '수전단가', '대체단가', 'BEP', '경제성(억원)'];
  const rowLabels = {
    '최적운전모드': '최적 운전모드',
    'SMP': 'SMP (원/kWh)',
    '수전단가': '수전단가 (원/kWh)',
    '대체단가': '대체단가 (원/kWh)',
    'BEP': 'LNG발전 BEP ($/MMBtu)',
    '경제성(억원)': '경제성 (억원)',
  };

  rowKeys.forEach(key => {
    const vals = table.rows[key] || [];
    const tr = tbody.insertRow();
    const td0 = tr.insertCell();
    td0.className = 'row-header';
    td0.textContent = rowLabels[key] || key;

    vals.forEach((val, i) => {
      const td = tr.insertCell();
      if (key === '최적운전모드') {
        td.innerHTML = modeChip(val);
      } else if (key === '경제성(억원)') {
        const v = parseFloat(val);
        td.textContent = val != null ? (v > 0 ? '+' + val : val) : '-';
        td.className = val != null ? (v >= 0 ? 'text-pos' : 'text-neg') : '';
      } else {
        td.textContent = val != null ? val : '-';
      }
    });
  });

  wrap.appendChild(tbl);
  div.appendChild(wrap);
  return div;
}

function modeChip(mode) {
  if (!mode) return '<span>-</span>';
  const map = {
    '2기 full':  'chip-2gi',
    '2기 저부하': 'chip-2gi-low',
    '1기 full':  'chip-1gi',
    '정지':      'chip-off',
  };
  const cls = map[mode] || '';
  return `<span class="chip ${cls}">${mode}</span>`;
}

// ── 가동 가이던스 페이지 ──────────────────────────────
function renderGuidancePage(data) {
  const g = data.guidance;
  if (!g) return;

  // 운전 권고
  const recEl = document.getElementById('guidance-rec');
  if (recEl) {
    recEl.innerHTML = '';
    const lines = (g.recommendation || '').split('\n').filter(Boolean);
    lines.forEach(line => {
      const div = document.createElement('div');
      div.style.marginBottom = '6px';
      if (line.includes('[긴급]'))
        div.className = 'alert alert-error';
      else if (line.includes('[주의]'))
        div.className = 'alert alert-warn';
      else if (line.includes('[참고]'))
        div.className = 'alert alert-info';
      else
        div.className = 'alert alert-success';
      div.textContent = line;
      recEl.appendChild(div);
    });
  }

  // D+1 SMP 가용 여부 (서버 명시값 우선, 없으면 night_plan 내 0~7시 존재 여부로 판단)
  const nextLabel = g.next_date_label || '익일';
  // day_available이 명시적으로 false이거나, 미명시 상태에서 night_plan에 0~7시가 있으면 제거
  const dayAvailable = g.day_available === true;  // 서버가 true를 명시적으로 보낼 때만 허용

  const unavailDayHtml = `<div class="alert alert-info" style="margin:8px 0;">
    ⚠️ ${nextLabel} SMP 미공시 — 주간 가이던스 산출불가
  </div>`;
  const unavailNightHtml = `<div class="alert alert-info" style="margin-top:8px;">
    ⚠️ ${nextLabel} 00:00~08:00 SMP 미공시 — 익일 새벽 가이던스 산출불가
  </div>`;

  const dayEl = document.getElementById('guidance-day-table');
  if (dayEl) {
    if (!dayAvailable) {
      dayEl.innerHTML = unavailDayHtml;
    } else {
      renderGuidancePlanTable('guidance-day-table', g.day_plan || []);
    }
  }

  const nightEl = document.getElementById('guidance-night-table');
  if (nightEl) {
    // 클라이언트에서도 0~7시 rows 제거 (서버가 구버전이어도 안전하게 필터링)
    const filteredNight = dayAvailable
      ? (g.night_plan || [])
      : (g.night_plan || []).filter(p => p.hour >= 22);
    renderGuidancePlanTable('guidance-night-table', filteredNight);
    if (!dayAvailable) {
      nightEl.insertAdjacentHTML('beforeend', unavailNightHtml);
    }
  }

  // 모드 분포 차트
  renderModeDonut(g.mode_dist || {});

  // 일일 경제성 바
  renderEconBar(g.econ_totals || {});

  // 카카오 메시지
  const kkEl = document.getElementById('kakao-msg');
  if (kkEl) kkEl.textContent = g.kakao_message || '';

  // 다운로드
  const dlBtn = document.getElementById('btn-dl-report');
  if (dlBtn && g.text_report) {
    dlBtn.onclick = () => downloadText(g.text_report,
      `가동가이던스_${data.target_date}.txt`);
  }
}

function renderGuidancePlanTable(containerId, plan) {
  const container = document.getElementById(containerId);
  if (!container || !plan.length) return;
  container.innerHTML = '';

  const wrap = document.createElement('div');
  wrap.className = 'table-wrap';

  const tbl = document.createElement('table');
  const thead = tbl.createTHead();
  const hr = thead.insertRow();
  ['시간', 'SMP (원/kWh)', '최적모드', '판단', 'BEP ($/MMBtu)', '경제성 (억원)', '비고'].forEach(h => {
    const th = document.createElement('th');
    th.textContent = h; hr.appendChild(th);
  });

  const tbody = tbl.createTBody();
  plan.forEach(p => {
    const tr = tbody.insertRow();
    [
      { v: p.time_str, cls: '' },
      { v: p.smp?.toFixed(1) ?? '-', cls: '' },
      { v: modeChip(p.best_mode), html: true },
      { v: actionBadge(p.action), html: true },
      { v: p.bep?.toFixed(2) ?? '-', cls: '' },
      { v: p.econ_bil != null ? (p.econ_bil >= 0 ? '+' : '') + p.econ_bil.toFixed(3) : '-',
        cls: p.econ_bil != null ? (p.econ_bil >= 0 ? 'text-pos' : 'text-neg') : '' },
      { v: p.note || '', cls: 'text-sub' },
    ].forEach(({ v, html, cls }) => {
      const td = tr.insertCell();
      if (html) td.innerHTML = v; else td.textContent = v;
      if (cls) td.className = cls;
    });
  });

  wrap.appendChild(tbl);
  container.appendChild(wrap);
}

function actionBadge(action) {
  const map = {
    '가동': 'action-go', '감발전환': 'action-reduce',
    '기력점화검토': 'action-steam', '정지': 'action-stop',
  };
  return `<span class="${map[action] || ''}">${action || '-'}</span>`;
}

// ── 모드 도넛 차트 ────────────────────────────────────
function renderModeDonut(modeDist) {
  const entries = Object.entries(modeDist).filter(([_, v]) => v > 0);
  if (!entries.length) return;

  const labels = entries.map(([k]) => k);
  const series = entries.map(([_, v]) => v);
  const colors = ['#4F46E5', '#10B981', '#F59E0B', '#EF4444'];

  const opts = {
    chart: { type: 'donut', height: 220, background: 'transparent' },
    series,
    labels,
    colors,
    plotOptions: { pie: { donut: { size: '65%',
      labels: { show: true, total: { show: true, label: '총 시간', fontSize: '13px', color: '#1E1B4B' } }
    } } },
    legend: { position: 'bottom', fontSize: '12px' },
    dataLabels: { enabled: false },
    theme: { mode: 'light' },
  };

  const el = document.getElementById('chart-mode-donut');
  if (!el) return;
  if (CHARTS.donut) CHARTS.donut.destroy();
  CHARTS.donut = new ApexCharts(el, opts);
  CHARTS.donut.render();
}

// ── 모드별 경제성 바 ──────────────────────────────────
function renderEconBar(econTotals) {
  const entries = Object.entries(econTotals);
  if (!entries.length) return;

  const opts = {
    chart: { type: 'bar', height: 220, background: 'transparent',
      toolbar: { show: false } },
    series: [{ name: '경제성 (억원)', data: entries.map(([_, v]) => parseFloat(v?.toFixed(3) || 0)) }],
    xaxis: { categories: entries.map(([k]) => k) },
    colors: ['#4F46E5'],
    plotOptions: { bar: { borderRadius: 6, columnWidth: '50%',
      colors: { ranges: [{ from: -999, to: 0, color: '#EF4444' }] }
    } },
    dataLabels: { enabled: true, formatter: v => (v >= 0 ? '+' : '') + v.toFixed(3) },
    yaxis: { labels: { formatter: v => v.toFixed(2) } },
    theme: { mode: 'light' },
    grid: { borderColor: 'rgba(0,0,0,0.06)' },
  };

  const el = document.getElementById('chart-econ-bar');
  if (!el) return;
  if (CHARTS.econBar) CHARTS.econBar.destroy();
  CHARTS.econBar = new ApexCharts(el, opts);
  CHARTS.econBar.render();
}

// ── 이상구간 탐지 페이지 ──────────────────────────────

// STATE.analysisData.anomaly 에서 직접 렌더링 (분석 완료 후 사용)
function renderAnomalyDirect(anomaly) {
  const c = anomaly.counts || {};
  setText('anomaly-zero', c.zero ?? 0);
  setText('anomaly-low',  c.low  ?? 0);
  setText('anomaly-high', c.high ?? 0);
  renderAnomalyChart(anomaly.chart || {});
  _renderAnomalyTable(anomaly.details || []);
}

// 독립 API 호출 (분석 데이터 없거나 chart 없을 때 폴백)
async function loadAnomalyChart() {
  const el = document.getElementById('chart-anomaly');
  if (el) el.innerHTML = '<div style="padding:20px;text-align:center;color:#6B7280;">데이터 로딩 중...</div>';

  try {
    const res = await fetch('/api/anomaly-chart');
    if (!res.ok) {
      // 엔드포인트 없으면 분석 재실행으로 폴백
      console.log('[anomaly] /api/anomaly-chart 없음, 분석 재실행');
      if (!STATE.loading) {
        const origPage = STATE.activePage;
        await runAnalysis();
        if (origPage === 'anomaly' && STATE.analysisData?.anomaly) {
          renderAnomalyDirect(STATE.analysisData.anomaly);
        }
      }
      return;
    }
    const chart = await res.json();
    console.log('[anomaly] /api/anomaly-chart 응답:', chart.smp_series?.length, '개');

    if (chart.error) {
      if (el) el.innerHTML = `<div class="alert alert-warn" style="margin:16px 0;">오류: ${chart.error}</div>`;
      return;
    }

    setText('anomaly-zero', chart.counts?.zero ?? 0);
    setText('anomaly-low',  chart.counts?.low  ?? 0);
    setText('anomaly-high', chart.counts?.high ?? 0);
    renderAnomalyChart(chart);
    _renderAnomalyTable(chart.details || []);
  } catch(e) {
    console.error('[anomaly] 로드 오류:', e);
    if (el) el.innerHTML = `<div class="alert alert-warn" style="margin:16px 0;">로드 실패: ${e.message}</div>`;
  }
}

function _renderAnomalyTable(details) {
  const container = document.getElementById('anomaly-table');
  if (!container) return;
  if (details.length) {
    container.innerHTML = '';
    const wrap = document.createElement('div');
    wrap.className = 'table-wrap';
    const tbl = document.createElement('table');
    const cols = Object.keys(details[0]);
    const thead = tbl.createTHead();
    const hr = thead.insertRow();
    cols.forEach(c => { const th = document.createElement('th'); th.textContent = c; hr.appendChild(th); });
    const tbody = tbl.createTBody();
    details.forEach(row => {
      const tr = tbody.insertRow();
      cols.forEach(col => {
        const td = tr.insertCell();
        const v = row[col];
        td.textContent = v != null ? (typeof v === 'number' ? v.toFixed(2) : v) : '-';
      });
    });
    wrap.appendChild(tbl); container.appendChild(wrap);
  } else {
    container.innerHTML = '<div class="alert alert-success">이상구간이 감지되지 않았습니다.</div>';
  }
}

function renderAnomalyPage(data) {
  renderAnomalyDirect(data.anomaly || {});
}

function renderAnomalyChart(chart) {
  const el = document.getElementById('chart-anomaly');
  if (!el) return;

  if (CHARTS.anomaly) { try { CHARTS.anomaly.destroy(); } catch(e) {} delete CHARTS.anomaly; }
  el.innerHTML = '';

  const smpData = chart.smp_series || [];
  console.log('[anomaly] smp_series 개수:', smpData.length, '/ threshold:', chart.threshold_low, chart.threshold_high);

  if (!smpData.length) {
    el.innerHTML = '<div class="alert alert-info" style="margin:16px 0;">SMP 데이터가 없습니다.</div>';
    return;
  }

  const low  = Number(chart.threshold_low)  || 100;
  const high = Number(chart.threshold_high) || 170;

  // 날짜 파싱 ("2025-11-30T23:00:00" 또는 "2025-11-30 23:00:00")
  const toMs = s => {
    const d = new Date(String(s).replace(' ', 'T'));
    return isNaN(d.getTime()) ? null : d.getTime();
  };

  // SMP 라인 시계열
  const smpSeries = smpData
    .map(p => [toMs(p.x), Number(p.y)])
    .filter(([x, y]) => x !== null && !isNaN(y));

  // 이상구간 포인트 — annotations.points 사용 (scatter 대신)
  const pts = chart.anomaly_points || [];
  const COLOR = { 'SMP 제로': '#EF4444', 'SMP 경제성 한계': '#F97316', 'SMP 과대': '#EAB308' };
  const ptAnnotations = pts.slice(0, 300).map(p => {
    const x = toMs(p.x);
    if (!x) return null;
    return {
      x,
      y: Number(p.y),
      marker: { size: 4, fillColor: COLOR[p.type] || '#6B7280', strokeColor: '#fff', strokeWidth: 1, radius: 2 },
      label: { text: undefined },
    };
  }).filter(Boolean);

  console.log('[anomaly] annotation points:', ptAnnotations.length);

  const options = {
    chart: {
      type: 'line',
      height: 400,
      background: 'transparent',
      toolbar: { show: true, tools: { download: true, zoom: true, zoomin: true, zoomout: true, pan: true, reset: true } },
      zoom: { enabled: true, type: 'x' },
      animations: { enabled: false },
    },
    series: [{ name: 'SMP (원/kWh)', data: smpSeries }],
    colors: ['#6366F1'],
    stroke: { curve: 'straight', width: 1.2 },
    markers: { size: 0 },
    xaxis: {
      type: 'datetime',
      labels: { datetimeUTC: false, format: 'yy/MM/dd' },
    },
    yaxis: {
      labels: { formatter: v => v != null ? Math.round(v) : '' },
      title: { text: '원/kWh' },
    },
    annotations: {
      yaxis: [
        { y: low,  borderColor: '#F97316', strokeDashArray: 4,
          label: { text: `LNG BEP ${low}원`, position: 'left', style: { color: '#fff', background: '#F97316', fontSize: '11px' } } },
        { y: high, borderColor: '#EAB308', strokeDashArray: 4,
          label: { text: `과대 기준 ${high}원`, position: 'left', style: { color: '#fff', background: '#B45309', fontSize: '11px' } } },
      ],
      points: ptAnnotations,
    },
    tooltip: { x: { format: 'yyyy-MM-dd HH:mm' }, theme: 'light' },
    legend: { show: false },
    grid: { borderColor: 'rgba(107,114,128,0.15)' },
  };

  try {
    CHARTS.anomaly = new ApexCharts(el, options);
    CHARTS.anomaly.render();
    console.log('[anomaly] ApexCharts.render() 호출 완료');
  } catch(e) {
    console.error('[anomaly] 차트 렌더링 오류:', e);
    el.innerHTML = '<div class="alert alert-warn" style="margin:16px 0;">차트 오류: ' + e.message + '</div>';
  }
}

// ── 미공시 화면 ───────────────────────────────────────
function renderUnavailable(data) {
  document.getElementById('dashboard-block').style.display = 'none';
  const el = document.getElementById('unavail-block');
  el.style.display = '';
  el.innerHTML = `
    <div class="glass-card unavail-card">
      <div class="uv-icon">📭</div>
      <div class="uv-title">${data.target_label || ''} — 산출불가</div>
      <div class="uv-sub">
        해당 날짜의 SMP 데이터가 아직 공시되지 않아<br>
        경제성 판단을 수행할 수 없습니다.<br>
        <span style="color:#9CA3AF; font-size:12px; margin-top:8px; display:block;">
          소스: ${data.smp_source || '미공시'}
        </span>
      </div>
    </div>`;
}

// ── SMP 상태 ──────────────────────────────────────────
function updateSMPStatus(statusList) {
  const el = document.getElementById('smp-status-list');
  if (!el) return;
  el.innerHTML = '';
  statusList.forEach(s => {
    const div = document.createElement('div');
    div.className = 'smp-status-row';
    div.innerHTML = `
      <span>${s.label}</span>
      <span style="display:flex;align-items:center;gap:5px;">
        <span style="color:${s.available ? '#10B981' : '#EF4444'};font-size:9px;">●</span>
        <span style="font-size:11px;color:#6B7280;">${s.source}</span>
      </span>`;
    el.appendChild(div);
  });
}

// ── ML 모델 ────────────────────────────────────────────
async function loadMLMetrics() {
  const container = document.getElementById('ml-content');
  if (!container) return;
  container.innerHTML = '<div class="skeleton" style="height:200px;"></div>';

  const data = await fetch('/api/ml-metrics').then(r => r.json());
  if (!data.data_loaded) {
    container.innerHTML = '<div class="alert alert-warn">ML 모델 미로드</div>';
    return;
  }

  const sp = data.split || {};
  let html = `
    <div class="kpi-grid" style="grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:20px;">
      ${kpiHtml('📊', sp.n_all ?? '-', '행', '전체 데이터')}
      ${kpiHtml('🔵', sp.n_train ?? '-', '행', '학습 데이터')}
      ${kpiHtml('🟣', sp.n_test ?? '-', '행', '테스트 데이터')}
    </div>
    <div class="glass-card" style="padding:0;overflow:auto;margin-bottom:16px;">
      <table>
        <thead><tr>
          <th>운전모드</th><th>타겟</th><th>Train MAE</th>
          <th>Train R²</th><th>CV R²</th><th>Test MAE</th><th>Test R²</th>
        </tr></thead>
        <tbody>`;

  data.metrics.forEach(m => {
    const r2Color = r2Class(m.r2_cv);
    html += `<tr>
      <td>${m.mode}</td><td>${m.target}</td>
      <td>${fmt(m.mae)}</td>
      <td style="color:${r2Color}">${fmt(m.r2)}</td>
      <td style="color:${r2Color};font-weight:600">${fmt(m.r2_cv)}</td>
      <td>${fmt(m.mae_test)}</td>
      <td>${fmt(m.r2_test)}</td>
    </tr>`;
  });
  html += `</tbody></table></div>`;
  container.innerHTML = html;

  // CV R² 차트
  renderMLChart(data.metrics);

  // 재학습 버튼
  document.getElementById('btn-retrain')?.addEventListener('click', async () => {
    if (!confirm('전체 데이터로 XGBoost 모델을 재학습합니다. 계속하시겠습니까?')) return;
    const btn = document.getElementById('btn-retrain');
    btn.disabled = true; btn.textContent = '⏳ 재학습 중...';
    const res = await fetch('/api/retrain', { method: 'POST' });
    const d = await res.json();
    btn.disabled = false; btn.textContent = '🔄 모델 재학습';
    if (d.success) { alert('재학습 완료!'); loadMLMetrics(); }
    else alert('재학습 실패');
  });
}

function renderMLChart(metrics) {
  const targets = ['export', 'import', 'efficiency'];
  const modes   = [...new Set(metrics.map(m => m.mode))];
  const series  = targets.map(t => ({
    name: t,
    data: modes.map(mode => {
      const m = metrics.find(r => r.mode === mode && r.target === t);
      return m?.r2_cv != null ? parseFloat(m.r2_cv.toFixed(4)) : 0;
    }),
  }));

  const opts = {
    chart: { type: 'bar', height: 260, background: 'transparent',
      toolbar: { show: false } },
    series,
    xaxis: { categories: modes },
    yaxis: { min: 0, max: 1, labels: { formatter: v => v.toFixed(2) } },
    colors: ['#4F46E5', '#10B981', '#F472B6'],
    plotOptions: { bar: { groupPadding: 0.1, borderRadius: 3 } },
    legend: { position: 'top' },
    title: { text: '운전모드별 CV R² 점수', style: { fontSize: '13px', fontFamily: 'Sora' } },
    grid: { borderColor: 'rgba(0,0,0,0.06)' },
    theme: { mode: 'light' },
  };

  const el = document.getElementById('chart-ml-r2');
  if (!el) return;
  if (CHARTS.mlR2) CHARTS.mlR2.destroy();
  CHARTS.mlR2 = new ApexCharts(el, opts);
  CHARTS.mlR2.render();
}

function r2Class(v) {
  if (v == null) return '#6B7280';
  if (v >= 0.9) return '#10B981';
  if (v >= 0.7) return '#F59E0B';
  return '#EF4444';
}

// ── 원시 데이터 ───────────────────────────────────────
async function loadRawData() {
  const container = document.getElementById('rawdata-content');
  if (!container) return;
  container.innerHTML = '<div class="skeleton" style="height:200px;"></div>';

  const data = await fetch('/api/rawdata').then(r => r.json());
  if (!data.available) {
    container.innerHTML = '<div class="alert alert-warn">데이터를 로드할 수 없습니다.</div>';
    return;
  }

  let html = `
    <div style="font-size:14px;color:#6B7280;margin-bottom:16px;">
      총 <strong style="color:#1E1B4B;">${data.n_rows.toLocaleString('ko')}</strong>행
      × <strong style="color:#1E1B4B;">${data.n_cols}</strong>열
    </div>
    <div class="glass-card" style="padding:0;overflow:auto;margin-bottom:16px;">
      <table>
        <thead><tr>
          <th>컬럼</th><th>count</th><th>mean</th><th>std</th><th>min</th><th>25%</th><th>50%</th><th>75%</th><th>max</th>
        </tr></thead>
        <tbody>`;

  data.stats.forEach(row => {
    html += `<tr>
      <td class="row-header">${row.column}</td>
      <td>${fmt(row.count)}</td><td>${fmt(row.mean)}</td><td>${fmt(row.std)}</td>
      <td>${fmt(row.min)}</td><td>${fmt(row['25%'])}</td><td>${fmt(row['50%'])}</td>
      <td>${fmt(row['75%'])}</td><td>${fmt(row.max)}</td>
    </tr>`;
  });
  html += `</tbody></table></div>`;
  container.innerHTML = html;

  // SMP 히스토그램
  if (data.smp_histogram?.length) {
    const histEl = document.createElement('div');
    histEl.id = 'chart-smp-hist';
    histEl.style.marginTop = '16px';
    container.appendChild(histEl);

    const opts = {
      chart: { type: 'bar', height: 280, background: 'transparent',
        toolbar: { show: false } },
      series: [{ name: '빈도', data: data.smp_histogram.map(b => ({ x: b.x, y: b.y })) }],
      xaxis: { type: 'numeric', labels: { formatter: v => v.toFixed(0) } },
      title: { text: 'SMP 분포 히스토그램 (원/kWh)', style: { fontSize: '13px', fontFamily: 'Sora' } },
      colors: ['#6366F1'],
      plotOptions: { bar: { columnWidth: '90%' } },
      dataLabels: { enabled: false },
      grid: { borderColor: 'rgba(0,0,0,0.06)' },
      theme: { mode: 'light' },
    };
    if (CHARTS.hist) CHARTS.hist.destroy();
    CHARTS.hist = new ApexCharts(histEl, opts);
    CHARTS.hist.render();
  }
}

// ── 스케줄러 폴링 ─────────────────────────────────────
function startSchedulerPoll() {
  const updateBadge = async () => {
    try {
      const res = await fetch('/api/scheduler-status');
      const d = await res.json();
      STATE.schedulerStatus = d.status;
      const badge = document.getElementById('sched-badge');
      const dot   = document.getElementById('sched-dot');
      const lbl   = document.getElementById('sched-label');
      if (!badge) return;
      const cfg = {
        running:  { color: '#10B981', text: '스케줄러 가동중' },
        fetching: { color: '#F59E0B', text: 'SMP 수집중' },
        stopped:  { color: '#EF4444', text: '스케줄러 미실행' },
      };
      const c = cfg[d.status] || cfg.stopped;
      dot.style.background = c.color;
      lbl.style.color      = c.color;
      lbl.textContent      = c.text;
    } catch (_) {}
  };
  updateBadge();
  setInterval(updateBadge, 60000);
}

// ── 유틸 ──────────────────────────────────────────────
function setText(id, val) {
  const el = document.getElementById(id);
  if (el) el.textContent = val;
}

function fmt(v) {
  if (v == null || v === '') return '-';
  const n = parseFloat(v);
  if (isNaN(n)) return v;
  return Math.abs(n) < 0.01 ? n.toFixed(4) : n.toFixed(2);
}

function kpiHtml(icon, val, unit, lbl) {
  return `<div class="kpi-card">
    <div class="kpi-icon">${icon}</div>
    <div class="kpi-val">${val}${unit ? '<span style="font-size:13px;font-weight:400"> '+unit+'</span>' : ''}</div>
    <div class="kpi-lbl">${lbl}</div>
  </div>`;
}

function downloadText(content, filename) {
  const a = document.createElement('a');
  const blob = new Blob([content], { type: 'text/plain;charset=utf-8' });
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
}

// ── 경제성 분석 페이지 ────────────────────────────────
function renderEconPage(data) {
  if (!data?.has_smp) return;

  const kpis       = data.kpis       || {};
  const thresholds = data.thresholds || {};
  const chart      = data.chart      || {};
  const guidance   = data.guidance   || {};

  // 헤더
  setText('econ-subtitle', `${data.target_label || ''} 기준 24시간 경제성 분석`);

  // KPI
  setText('econ-avg-smp',  kpis.avg_smp != null ? `${kpis.avg_smp.toFixed(1)}` : '--');
  setText('econ-bep-low',  thresholds.smp_low  != null ? `${thresholds.smp_low.toFixed(1)}`  : '--');
  setText('econ-bep-high', thresholds.smp_high != null ? `${thresholds.smp_high.toFixed(1)}` : '--');
  setText('econ-top-mode', kpis.top_mode || '--');

  // 24h SMP 배열 조합 (night_plan 22-23시 + day_plan 8-21시 + night_plan 0-7시)
  const allPlan = [];
  (guidance.night_plan || []).forEach(p => allPlan.push(p));
  (guidance.day_plan   || []).forEach(p => allPlan.push(p));
  allPlan.sort((a, b) => a.hour - b.hour);

  // 차트: 24개 x축 레이블
  const labels24 = Array.from({length: 24}, (_, i) => `${i.toString().padStart(2,'0')}시`);
  const smpMap   = {};
  const bepMap   = {};
  allPlan.forEach(p => { smpMap[p.hour] = p.smp; bepMap[p.hour] = p.bep; });

  const smpVals = labels24.map((_, i) => smpMap[i] ?? null);
  const bepVals = labels24.map((_, i) => bepMap[i] ?? null);
  const low  = thresholds.smp_low  || 0;
  const high = thresholds.smp_high || 0;

  // ApexCharts 라인 차트
  const el = document.getElementById('chart-econ-smp');
  if (el) {
    if (CHARTS.econSmp) { try { CHARTS.econSmp.destroy(); } catch(e) {} }
    el.innerHTML = '';
    const opts = {
      chart: { type: 'line', height: 380, background: 'transparent',
               toolbar: { show: true }, animations: { enabled: false } },
      series: [{ name: 'SMP (원/kWh)', data: smpVals }],
      colors: ['#4F46E5'],
      stroke: { curve: 'smooth', width: 2.5 },
      markers: { size: 4 },
      xaxis: { categories: labels24, labels: { rotate: -45 } },
      yaxis: { labels: { formatter: v => v != null ? Math.round(v) : '' },
               title: { text: '원/kWh' } },
      annotations: {
        yaxis: [
          { y: low,  borderColor: '#F472B6', strokeDashArray: 5,
            label: { text: `LNG BEP ${low}원`, position: 'left',
                     style: { color: '#fff', background: '#F472B6', fontSize: '11px' } } },
          { y: high, borderColor: '#F59E0B', strokeDashArray: 5,
            label: { text: `기력 BEP ${high}원`, position: 'left',
                     style: { color: '#fff', background: '#B45309', fontSize: '11px' } } },
        ],
      },
      tooltip: { y: { formatter: v => v != null ? `${Math.round(v)} 원/kWh` : '-' } },
      grid: { borderColor: 'rgba(107,114,128,0.15)' },
    };
    CHARTS.econSmp = new ApexCharts(el, opts);
    CHARTS.econSmp.render();
  }

  // 상세 테이블
  const tblEl = document.getElementById('econ-detail-table');
  if (tblEl && allPlan.length) {
    const MODE_COLOR = {
      '2기 full': '#DBEAFE', '2기 저부하': '#DCFCE7', '1기 full': '#FEF9C3', '정지': '#FEE2E2',
    };
    let rows = '';
    allPlan.forEach(p => {
      const bg = MODE_COLOR[p.best_mode] || '';
      const smpAboveLow  = p.smp != null && p.smp >= low;
      const smpAboveHigh = p.smp != null && p.smp >= high;
      const smpColor = smpAboveHigh ? '#F59E0B' : smpAboveLow ? '#10B981' : '#EF4444';
      rows += `<tr>
        <td class="row-header">${p.time_str}</td>
        <td style="color:${smpColor};font-weight:600;">${p.smp != null ? p.smp.toFixed(1) : '-'}</td>
        <td style="background:${bg};font-weight:600;">${p.best_mode || '-'}</td>
        <td>${p.bep != null ? p.bep.toFixed(2) : '-'}</td>
        <td>${p.econ_bil != null ? p.econ_bil.toFixed(3) : '-'}</td>
        <td style="font-size:11px;color:#6B7280;">${p.action || ''}</td>
      </tr>`;
    });
    tblEl.innerHTML = `
      <div class="table-wrap">
        <table>
          <thead><tr>
            <th>시간</th><th>SMP (원/kWh)</th><th>최적모드</th>
            <th>BEP ($/MMBtu)</th><th>경제성 (억원)</th><th>판단</th>
          </tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>`;
  } else if (tblEl) {
    tblEl.innerHTML = '<div class="alert alert-warn">가이던스 데이터가 없습니다.</div>';
  }
}

// ── 경제성 급변 탐지 (당일 데이터 기반) ─────────────────
function renderEconChangePage(data) {
  if (!data?.has_smp) return;

  const guidance = data.guidance || {};
  const allPlan  = [
    ...(guidance.night_plan || []),
    ...(guidance.day_plan   || []),
  ].sort((a, b) => a.hour - b.hour);

  if (!allPlan.length) return;

  const econVals = allPlan.map(p => p.econ_bil ?? 0);
  const labels   = allPlan.map(p => p.time_str);

  // 급변 구간: 연속 시간 간 경제성 차이가 임계값(0.5억원) 이상
  const THRESHOLD = 0.5;
  const changePoints = [];
  for (let i = 1; i < econVals.length; i++) {
    const diff = Math.abs(econVals[i] - econVals[i-1]);
    if (diff >= THRESHOLD) {
      changePoints.push({
        x: labels[i], y: econVals[i],
        marker: { size: 7, fillColor: '#EF4444', strokeColor: '#fff', strokeWidth: 2 },
      });
    }
  }

  const el = document.getElementById('chart-econ-change');
  if (!el) return;
  if (CHARTS.econChange) { try { CHARTS.econChange.destroy(); } catch(e) {} }
  el.innerHTML = '';

  const opts = {
    chart: { type: 'line', height: 300, background: 'transparent',
             toolbar: { show: false }, animations: { enabled: false } },
    series: [{ name: '경제성 (억원)', data: econVals }],
    colors: ['#6366F1'],
    stroke: { curve: 'smooth', width: 2 },
    markers: { size: 3 },
    xaxis: { categories: labels, labels: { rotate: -45, style: { fontSize: '10px' } } },
    yaxis: { labels: { formatter: v => v != null ? v.toFixed(2) : '' },
             title: { text: '경제성 (억원)' } },
    annotations: { points: changePoints },
    tooltip: { y: { formatter: v => `${v != null ? v.toFixed(3) : '-'} 억원` } },
    grid: { borderColor: 'rgba(107,114,128,0.15)' },
  };
  CHARTS.econChange = new ApexCharts(el, opts);
  CHARTS.econChange.render();

  // 급변 테이블
  const tblEl = document.getElementById('econ-change-table');
  if (tblEl) {
    if (changePoints.length) {
      let rows = '';
      for (let i = 1; i < econVals.length; i++) {
        const diff = Math.abs(econVals[i] - econVals[i-1]);
        if (diff >= THRESHOLD) {
          const dir = econVals[i] > econVals[i-1] ? '📈 상승' : '📉 하락';
          rows += `<tr>
            <td class="row-header">${labels[i-1]} → ${labels[i]}</td>
            <td>${econVals[i-1].toFixed(3)}</td>
            <td>${econVals[i].toFixed(3)}</td>
            <td style="font-weight:600;color:${econVals[i]>econVals[i-1]?'#10B981':'#EF4444'}">
              ${dir} ${diff.toFixed(3)}억원
            </td>
          </tr>`;
        }
      }
      tblEl.innerHTML = `
        <div class="table-wrap">
          <table>
            <thead><tr>
              <th>구간</th><th>이전 경제성</th><th>이후 경제성</th><th>변화</th>
            </tr></thead>
            <tbody>${rows}</tbody>
          </table>
        </div>`;
    } else {
      tblEl.innerHTML = '<div class="alert alert-success" style="margin-top:8px;">급변 구간이 감지되지 않았습니다.</div>';
    }
  }
}
