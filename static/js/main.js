/* ============================================================
   IRONBOUND — client-side behaviour
   Charts, live form previews, exercise filters.
   ============================================================ */

(function () {
    'use strict';

    // ---------- theme helpers ----------
    function cssVar(name, fallback) {
        try {
            const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
            return v || fallback;
        } catch (e) { return fallback; }
    }

    let chartInstances = [];
    function destroyCharts() {
        chartInstances.forEach(function (c) { try { c.destroy(); } catch (e) {} });
        chartInstances = [];
    }

    function initTheme() {
        const btn = document.getElementById('themeToggle');
        if (!btn) return;
        btn.addEventListener('click', function () {
            const root = document.documentElement;
            const next = root.getAttribute('data-theme') === 'light' ? 'dark' : 'light';
            root.setAttribute('data-theme', next);
            try { localStorage.setItem('ib_theme', next); } catch (e) {}
            destroyCharts();
            initCharts();
        });
    }

    // ---------- toast notifications (rendered from server flash messages) ----------
    function initToasts() {
        const dataEl = document.getElementById('flash-data');
        const host = document.getElementById('toasts');
        if (!dataEl || !host || !dataEl.dataset.messages) return;
        let messages;
        try { messages = JSON.parse(dataEl.dataset.messages); } catch (e) { return; }
        messages.forEach(function (pair) {
            if (!Array.isArray(pair)) return;
            const cat = pair[0] || 'info';
            const msg = pair[1] || '';
            const el = document.createElement('div');
            el.className = 'toast toast-' + cat;
            el.setAttribute('role', cat === 'error' ? 'alert' : 'status');
            el.textContent = msg;
            host.appendChild(el);
            requestAnimationFrame(function () { el.classList.add('show'); });
            setTimeout(function () {
                el.classList.remove('show');
                setTimeout(function () { el.remove(); }, 320);
            }, 4200);
        });
    }

    // ---------- show / hide password buttons ----------
    function initPasswordToggles() {
        document.querySelectorAll('.pw-toggle').forEach(function (btn) {
            btn.addEventListener('click', function () {
                const input = document.getElementById(btn.getAttribute('data-pw-target'));
                if (!input) return;
                const show = input.type === 'password';
                input.type = show ? 'text' : 'password';
                btn.textContent = show ? '🙈' : '👁';
                btn.setAttribute('aria-label', show ? 'Hide password' : 'Show password');
            });
        });
    }

    // ---------- backup import: submit the form once a file is chosen ----------
    function initImport() {
        const input = document.getElementById('importFile');
        if (input) {
            input.addEventListener('change', function () {
                if (input.files && input.files.length) input.form.submit();
            });
        }
    }

    // ---------- Exercise library filters ----------
    function initExerciseFilters() {
        const chips = document.querySelectorAll('[data-filter]');
        const cards = document.querySelectorAll('.ex-card');
        if (!chips.length) return;

        chips.forEach(function (chip) {
            chip.addEventListener('click', function () {
                chips.forEach(function (c) { c.classList.remove('active'); });
                chip.classList.add('active');
                const f = chip.getAttribute('data-filter');
                cards.forEach(function (card) {
                    card.style.display = (f === 'all' || card.getAttribute('data-feeds') === f) ? '' : 'none';
                });
            });
        });
    }

    // ---------- Dashboard charts (reads JSON embed, needs Chart.js) ----------
    function initCharts() {
        if (typeof Chart === 'undefined') return;
        const dataEl = document.getElementById('chart-data');
        if (!dataEl) return;

        let data;
        try { data = JSON.parse(dataEl.textContent); } catch (e) { return; }

        const grid = cssVar('--grid-line', 'rgba(148, 163, 184, 0.10)');
        const chartText = cssVar('--chart-text', '#5c6b84');
        const legendText = cssVar('--legend-text', '#eef2f8');
        const pointBorder = cssVar('--point-border', '#05070d');
        const STAT_COLORS = { STR: '#38e0ff', END: '#a78bfa', AGI: '#2dd4bf', VIT: '#34d399', POW: '#fb923c', FLX: '#f472b6' };
        const STATS = ['STR', 'END', 'AGI', 'VIT', 'POW', 'FLX'];
        const RANGE_DAYS = { '1D': 1, '1W': 7, '1M': 30, '3M': 90, '6M': 180 };
        const history = data.history || [];

        function overallOf(entry) {
            const vals = STATS.map(k => entry[k]).filter(v => typeof v === 'number');
            if (!vals.length) return null;
            return vals.reduce((a, b) => a + b, 0) / vals.length;
        }

        function sliceByRange(rangeKey) {
            const days = RANGE_DAYS[rangeKey] || 30;
            if (!history.length) return [];
            const last = new Date(history[history.length - 1].date + 'T00:00:00');
            const cutoff = last.getTime() - days * 86400000;
            return history.filter(h => new Date(h.date + 'T00:00:00').getTime() >= cutoff);
        }

        function shortLabel(dateStr) { return dateStr.slice(5).replace('-', '/'); }

        function barGradient(ctx) {
            const g = ctx.createLinearGradient(0, 0, 0, ctx.height || 200);
            g.addColorStop(0, '#a78bfa');
            g.addColorStop(1, '#38e0ff');
            return g;
        }

        function areaFill(ctx) {
            const g = ctx.createLinearGradient(0, 0, 0, ctx.height || 200);
            g.addColorStop(0, 'rgba(251, 113, 133, 0.5)');
            g.addColorStop(1, 'rgba(244, 63, 94, 0.02)');
            return g;
        }

        function peakIndices(values, count) {
            return values
                .map((v, i) => ({ v, i }))
                .sort((a, b) => b.v - a.v)
                .slice(0, count)
                .map(o => o.i);
        }

        // radar
        const radarEl = document.getElementById('radarChart');
        if (radarEl) {
            const radarChart = new Chart(radarEl, {
                type: 'radar',
                data: {
                    labels: STATS,
                    datasets: [{
                        label: 'Stats',
                        data: STATS.map(k => data.stats[k] || 0),
                        backgroundColor: 'rgba(56, 224, 255, 0.14)',
                        borderColor: '#38e0ff',
                        borderWidth: 2,
                        pointBackgroundColor: STATS.map(k => STAT_COLORS[k]),
                        pointBorderColor: pointBorder,
                        pointRadius: 5
                    }]
                },
                options: {
                    responsive: true,
                    scales: {
                        r: {
                            min: 0, max: 100,
                            ticks: { color: chartText, backdropColor: 'transparent', stepSize: 25, font: { size: 10 } },
                            grid: { color: grid },
                            angleLines: { color: grid },
                            pointLabels: { color: legendText, font: { size: 13, weight: 'bold' } }
                        }
                    },
                    plugins: { legend: { display: false } }
                }
            });
            chartInstances.push(radarChart);
        }

        // activity-mix donut
        const donutEl = document.getElementById('donutChart');
        if (donutEl) {
            const counts = data.counts || {};
            const donutChart = new Chart(donutEl, {
                type: 'doughnut',
                data: {
                    labels: ['Strength', 'Cardio', 'Body', 'Field', 'Exercise'],
                    datasets: [{
                        data: [
                            counts.strength || 0,
                            counts.cardio || 0,
                            counts.body || 0,
                            counts.performance || 0,
                            counts.exercise || 0
                        ],
                        backgroundColor: ['#38e0ff', '#a78bfa', '#34d399', '#fb923c', '#f472b6'],
                        borderWidth: 0,
                        hoverOffset: 5
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    cutout: '68%',
                    plugins: { legend: { display: false } }
                }
            });
            chartInstances.push(donutChart);
        }

        // full history (all six stats)
        const histEl = document.getElementById('historyChart');
        if (histEl) {
            const series = [
                { key: 'STR', color: STAT_COLORS.STR },
                { key: 'END', color: STAT_COLORS.END },
                { key: 'AGI', color: STAT_COLORS.AGI },
                { key: 'VIT', color: STAT_COLORS.VIT },
                { key: 'POW', color: STAT_COLORS.POW },
                { key: 'FLX', color: STAT_COLORS.FLX }
            ];
            const historyChart = new Chart(histEl, {
                type: 'line',
                data: {
                    labels: history.map(h => shortLabel(h.date)),
                    datasets: series.map(s => ({
                        label: s.key,
                        data: history.map(h => h[s.key]),
                        borderColor: s.color,
                        borderWidth: 2,
                        tension: 0.35,
                        pointRadius: 0,
                        fill: false
                    }))
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: { labels: { color: legendText, font: { size: 12 } } },
                        tooltip: { callbacks: { label: ctx => ctx.dataset.label + ': ' + ctx.parsed.y } }
                    },
                    scales: {
                        x: { grid: { display: false }, ticks: { color: chartText, maxTicksLimit: 10 } },
                        y: { min: 0, max: 100, grid: { color: grid }, ticks: { color: chartText, maxTicksLimit: 5 } }
                    }
                }
            });
            chartInstances.push(historyChart);
        }

        // bodyweight trend
        const weightEl = document.getElementById('weightChart');
        if (weightEl) {
            const bw = data.bodyweight || [];
            const weightChart = new Chart(weightEl, {
                type: 'line',
                data: {
                    labels: bw.map(h => shortLabel(h.date)),
                    datasets: [{
                        label: 'Bodyweight',
                        data: bw.map(h => h.kg),
                        borderColor: STAT_COLORS.VIT,
                        backgroundColor: 'rgba(52, 211, 153, 0.15)',
                        borderWidth: 2,
                        tension: 0.35,
                        pointRadius: 3,
                        pointBackgroundColor: STAT_COLORS.VIT,
                        pointBorderColor: pointBorder,
                        fill: true
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: { display: false },
                        tooltip: { callbacks: { label: ctx => 'Bodyweight: ' + ctx.parsed.y + ' kg' } }
                    },
                    scales: {
                        x: { grid: { display: false }, ticks: { color: chartText, maxTicksLimit: 8 } },
                        y: { grid: { color: grid }, ticks: { color: chartText, maxTicksLimit: 5 } }
                    }
                }
            });
            chartInstances.push(weightChart);
        }

        // overall-score bars
        const barEl = document.getElementById('barChart');
        let barChart = null;
        if (barEl) {
            barChart = new Chart(barEl, {
                type: 'bar',
                data: { labels: [], datasets: [{ data: [], borderWidth: 0 }] },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    borderRadius: 4,
                    categoryPercentage: 0.7,
                    barPercentage: 0.8,
                    plugins: { legend: { display: false } },
                    scales: {
                        x: { grid: { display: false }, ticks: { color: chartText, maxTicksLimit: 8 } },
                        y: {
                            min: 0, max: 100,
                            grid: { color: grid },
                            ticks: { color: chartText, maxTicksLimit: 5 }
                        }
                    }
                }
            });
            chartInstances.push(barChart);
        }

        // STR area-peak
        const areaEl = document.getElementById('areaChart');
        let areaChart = null;
        if (areaEl) {
            areaChart = new Chart(areaEl, {
                type: 'line',
                data: { labels: [], datasets: [{ data: [] }] },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    elements: {
                        line: { tension: 0.4, borderWidth: 2 },
                        point: { radius: 0, hitRadius: 10, hoverRadius: 4 }
                    },
                    plugins: { legend: { display: false } },
                    scales: {
                        x: { grid: { display: false }, ticks: { color: chartText, maxTicksLimit: 8 } },
                        y: { min: 0, max: 100, grid: { color: grid }, ticks: { color: chartText, maxTicksLimit: 5 } }
                    }
                }
            });
            chartInstances.push(areaChart);
        }

        // END vs POW dual line
        const lineEl = document.getElementById('lineChart');
        let lineChart = null;
        if (lineEl) {
            lineChart = new Chart(lineEl, {
                type: 'line',
                data: {
                    labels: [],
                    datasets: [
                        { label: 'END', data: [], borderColor: '#a78bfa', tension: 0.4, pointRadius: 0, borderWidth: 2 },
                        { label: 'POW', data: [], borderColor: '#fb923c', tension: 0.4, pointRadius: 0, borderWidth: 2 }
                    ]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: { display: false },
                        tooltip: {
                            callbacks: {
                                label: ctx => ctx.dataset.label + ': ' + ctx.parsed.y
                            }
                        }
                    },
                    scales: {
                        x: { grid: { display: false }, ticks: { color: chartText, maxTicksLimit: 8 } },
                        y: { min: 0, max: 100, grid: { color: grid }, ticks: { color: chartText, maxTicksLimit: 5 } }
                    }
                }
            });
            chartInstances.push(lineChart);
        }

        function applyRange(rangeKey) {
            const sliced = sliceByRange(rangeKey);
            const labels = sliced.map(h => shortLabel(h.date));
            const overall = sliced.map(overallOf);
            const str = sliced.map(h => h.STR);
            const end = sliced.map(h => h.END);
            const pow = sliced.map(h => h.POW);

            if (barChart) {
                barChart.data.labels = labels;
                barChart.data.datasets[0].data = overall;
                barChart.data.datasets[0].backgroundColor = barGradient(barChart.ctx);
                barChart.update();
            }

            if (areaChart) {
                const peaks = peakIndices(str.filter(v => typeof v === 'number'), 3);
                const realIdx = [];
                let n = 0;
                str.forEach((v, i) => { if (typeof v === 'number') { if (peaks.indexOf(n) !== -1) realIdx.push(i); n++; } });
                areaChart.data.labels = labels;
                areaChart.data.datasets[0].data = str;
                areaChart.data.datasets[0].borderColor = '#fb7185';
                areaChart.data.datasets[0].backgroundColor = areaFill(areaChart.ctx);
                areaChart.data.datasets[0].fill = true;
                areaChart.data.datasets[0].pointBackgroundColor = str.map((_, i) => realIdx.indexOf(i) !== -1 ? '#ffffff' : 'rgba(0,0,0,0)');
                areaChart.data.datasets[0].pointRadius = str.map((_, i) => realIdx.indexOf(i) !== -1 ? 4 : 0);
                areaChart.update();
            }

            if (lineChart) {
                lineChart.data.labels = labels;
                lineChart.data.datasets[0].data = end;
                lineChart.data.datasets[1].data = pow;
                lineChart.update();
            }
        }

        // time-pill filtering
        const tools = document.getElementById('timeTools');
        if (tools) {
            tools.addEventListener('click', function (e) {
                const btn = e.target.closest('.time-pill');
                if (!btn || btn.dataset.range === tools.dataset.range) return;
                tools.querySelectorAll('.time-pill').forEach(p => p.classList.toggle('active', p === btn));
                applyRange(btn.dataset.range);
            });
        }

        applyRange('1M');
    }

    // ---------- sidebar mobile toggle ----------
    function initSidebar() {
        const burger = document.getElementById('burger');
        const sidebar = document.getElementById('sidebar');
        if (!burger || !sidebar) return;
        burger.addEventListener('click', function () {
            const open = sidebar.classList.toggle('open');
            burger.setAttribute('aria-expanded', String(open));
        });
    }

    // ---------- weather advisor (Open-Meteo, no API key) ----------
    function initWeather() {
        const card = document.getElementById('weatherCard');
        if (!card) return;

        const icoEl = document.getElementById('weatherIco');
        const tempEl = document.getElementById('weatherTemp');
        const condEl = document.getElementById('weatherCond');
        const tipEl = document.getElementById('weatherTip');
        const locEl = document.getElementById('weatherLoc');

        function hide() { card.style.display = 'none'; }

        function classify(code) {
            if (code === 0 || code === 1) return { ico: '☀️', label: 'Clear sky', group: 'nice' };
            if (code === 2) return { ico: '⛅', label: 'Partly cloudy', group: 'nice' };
            if (code === 3) return { ico: '☁️', label: 'Overcast', group: 'nice' };
            if (code === 45 || code === 48) return { ico: '🌫️', label: 'Foggy', group: 'fog' };
            if (code >= 51 && code <= 57) return { ico: '🌦️', label: 'Drizzle', group: 'wet' };
            if (code >= 61 && code <= 67) return { ico: '🌧️', label: 'Rain', group: 'wet' };
            if (code >= 71 && code <= 77) return { ico: '🌨️', label: 'Snow', group: 'snow' };
            if (code >= 80 && code <= 82) return { ico: '🌧️', label: 'Rain showers', group: 'wet' };
            if (code >= 85 && code <= 86) return { ico: '🌨️', label: 'Snow showers', group: 'snow' };
            if (code >= 95) return { ico: '⛈️', label: 'Thunderstorm', group: 'wet' };
            return { ico: '🌤️', label: 'Weather', group: 'nice' };
        }

        function render(w) {
            const c = classify(w.code);
            const t = w.temp;
            let tip;
            if (c.group === 'wet') {
                tip = c.group === 'snow' ? 'Snowy outside — train inside today.' : 'Wet outside — perfect day to train inside.';
            } else if (c.group === 'fog') {
                tip = 'Fog makes running risky — hit the gym.';
            } else if (t >= 33) {
                tip = 'Very hot — train early, late, or indoors.';
            } else if (t >= 28) {
                tip = 'Hot but clear — go for a shorter run.';
            } else if (t <= 0) {
                tip = 'Freezing — layer up for the run or train indoors.';
            } else if (c.group === 'nice') {
                tip = 'Great day for an outdoor run!';
            } else {
                tip = 'Mild conditions — solid day to train.';
            }
            if (locEl) locEl.textContent = w.loc || '';
            if (icoEl) icoEl.textContent = c.ico;
            if (tempEl) tempEl.textContent = Math.round(t) + '°C';
            if (condEl) condEl.textContent = c.label + (w.wind > 20 ? ' · windy ' + Math.round(w.wind) + ' km/h' : '');
            if (tipEl) tipEl.textContent = tip;
            card.style.display = '';
        }

        function fetchWeather(lat, lon, label) {
            const url = 'https://api.open-meteo.com/v1/forecast?latitude=' + lat +
                '&longitude=' + lon + '&current=temperature_2m,weather_code,wind_speed_10m&timezone=auto&forecast_days=1';
            fetch(url)
                .then(r => r.json())
                .then(function (j) {
                    if (!j || !j.current) return hide();
                    render({
                        temp: j.current.temperature_2m,
                        wind: j.current.wind_speed_10m,
                        code: j.current.weather_code,
                        loc: label
                    });
                })
                .catch(hide);
        }

        function ipFallback() {
            fetch('https://ipwho.is/')
                .then(r => r.json())
                .then(function (j) {
                    if (j && j.success !== false && j.latitude && j.longitude) {
                        fetchWeather(j.latitude.toFixed(2), j.longitude.toFixed(2), j.city || '');
                    } else hide();
                })
                .catch(hide);
        }

        function locate() {
            if (navigator.geolocation) {
                navigator.geolocation.getCurrentPosition(
                    function (p) {
                        fetchWeather(p.coords.latitude.toFixed(2), p.coords.longitude.toFixed(2), 'Your location');
                    },
                    ipFallback,
                    { timeout: 6000 }
                );
            } else {
                ipFallback();
            }
        }
        locate();
    }

    // ---------- Live previews on log forms ----------
    function setPreview(form, text) {
        const el = form.querySelector('[data-preview]');
        if (!el) return;
        if (text) { el.innerHTML = text; el.classList.add('visible'); }
        else { el.classList.remove('visible'); }
    }

    function initStrengthPreview() {
        const f = document.getElementById('strengthForm');
        if (!f) return;
        const w = f.querySelector('[name="weight_kg"]');
        const r = f.querySelector('[name="reps"]');
        const lift = f.querySelector('[name="lift"]');
        const bw = parseFloat(f.getAttribute('data-bw')) || 0;

        function upd() {
            const wv = parseFloat(w.value);
            const rv = parseInt(r.value, 10);
            if (wv > 0 && rv > 0) {
                const rm = rv === 1 ? wv : wv * (1 + rv / 30);
                let txt = 'Estimated 1RM: <b>' + rm.toFixed(1) + ' kg</b>';
                if (bw > 0) txt += ' · <b>' + (rm / bw).toFixed(2) + '×</b> bodyweight';
                const tgt = { bench: 'Enthusiast ≈ 1.25× BW', squat: 'Enthusiast ≈ 1.7× BW', deadlift: 'Enthusiast ≈ 2.0× BW' }[lift.value];
                txt += '<br><span style="font-size:12px; color:' + cssVar('--chart-text', '#5c6b84') + ';">' + tgt + '</span>';
                setPreview(f, txt);
            } else setPreview(f, null);
        }
        w.addEventListener('input', upd);
        r.addEventListener('input', upd);
        lift.addEventListener('change', upd);
    }

    function initCardioPreview() {
        const f = document.getElementById('cardioForm');
        if (!f) return;
        const d = f.querySelector('[name="distance_km"]');
        const m = f.querySelector('[name="minutes"]');

        function upd() {
            const dv = parseFloat(d.value);
            const mv = parseFloat(m.value);
            if (dv > 0 && mv > 0) {
                const norm = dv * (12 / mv);
                const vo2 = (norm * 1000 - 504.9) / 44.73;
                setPreview(f, '12-min equivalent: <b>' + norm.toFixed(2) + ' km</b> → estimated VO₂max <b>' + vo2.toFixed(1) + '</b> ml/kg/min');
            } else setPreview(f, null);
        }
        d.addEventListener('input', upd);
        m.addEventListener('input', upd);
    }

    function initBodyPreview() {
        const f = document.getElementById('bodyForm');
        if (!f) return;
        const waist = f.querySelector('[name="waist_cm"]');
        const neck = f.querySelector('[name="neck_cm"]');
        const height = f.querySelector('[name="height_cm"]');

        function upd() {
            const wv = parseFloat(waist.value);
            const nv = parseFloat(neck.value);
            const hv = parseFloat(height.value);
            if (wv > 0 && nv > 0 && hv > 0) {
                if (wv <= nv) { setPreview(f, 'Waist must be larger than neck.'); return; }
                const bf = 495 / (1.0324 - 0.19077 * Math.log10(wv - nv) + 0.15456 * Math.log10(hv)) - 450;
                setPreview(f, 'Estimated body fat: <b>' + bf.toFixed(1) + '%</b> · feeds your <b>VIT</b> stat');
            } else setPreview(f, null);
        }
        waist.addEventListener('input', upd);
        neck.addEventListener('input', upd);
        height.addEventListener('input', upd);
    }

    function initPerformancePreview() {
        const f = document.getElementById('performanceForm');
        if (!f) return;
        const inputs = {
            vertical_jump: { label: '🔥 Vertical jump', unit: 'cm', stat: 'POW' },
            sprint: { label: '⚡ 40 m sprint', unit: 's', stat: 'AGI' },
            sit_and_reach: { label: '🤸 Sit & reach', unit: 'cm', stat: 'FLX' }
        };

        function upd() {
            const rows = [];
            Object.keys(inputs).forEach(function (name) {
                const input = f.querySelector('[name="' + name + '"]');
                if (!input.value.trim()) return;
                const val = parseFloat(input.value);
                if (!isNaN(val)) rows.push('<div>' + inputs[name].label + ': <b>' + val + '</b> ' +
                    inputs[name].unit + ' → feeds <b>' + inputs[name].stat + '</b></div>');
            });
            if (rows.length) setPreview(f, rows.join('') + '<div style="font-size:12px; color:' + cssVar('--chart-text', '#5c6b84') + '; margin-top:4px;">Only filled tests are saved.</div>');
            else setPreview(f, null);
        }
        Object.keys(inputs).forEach(function (name) {
            f.querySelector('[name="' + name + '"]').addEventListener('input', upd);
        });
    }

    // ---------- Exercise logger: mode toggle + "how much does this add" preview ----------
    function initExerciseForm() {
        const f = document.getElementById('exerciseForm');
        if (!f) return;
        const sel = f.querySelector('[name="exercise_key"]');
        const repBtn = document.getElementById('modeRep');
        const timedBtn = document.getElementById('modeTimed');
        const repFields = document.getElementById('repFields');
        const timedFields = document.getElementById('timedFields');
        const sets = f.querySelector('[name="sets"]');
        const reps = f.querySelector('[name="reps"]');
        const weight = f.querySelector('[name="weight_kg"]');
        const minutes = f.querySelector('[name="minutes"]');
        let mode = 'rep';

        function setMode(m) {
            mode = m;
            if (repBtn && timedBtn) {
                repBtn.classList.toggle('active', m === 'rep');
                timedBtn.classList.toggle('active', m === 'timed');
                repBtn.setAttribute('aria-selected', m === 'rep' ? 'true' : 'false');
                timedBtn.setAttribute('aria-selected', m === 'timed' ? 'true' : 'false');
            }
            if (repFields) repFields.style.display = m === 'rep' ? '' : 'none';
            if (timedFields) timedFields.style.display = m === 'timed' ? '' : 'none';
            upd();
        }

        function isTimed() {
            const opt = sel && sel.options[sel.selectedIndex];
            return !!(opt && opt.getAttribute('data-times') === '1');
        }

        function creditFor(pts) {
            return Math.min(10, 10 * (1 - Math.exp(-pts / 420))).toFixed(1);
        }

        function upd() {
            const label = mode === 'timed' ? 'Duration (minutes)' : 'Reps per set';

            if (mode === 'timed') {
                const mv = parseFloat(minutes ? minutes.value : '');
                if (mv > 0) {
                    setPreview(f, 'Estimated training credit this week: <b>+' + creditFor(mv * 10) + '</b> ' +
                        '<span style="font-size:12px; color:' + cssVar('--chart-text', '#5c6b84') + ';">(' + mv + ' min vs measured scores)</span>');
                } else setPreview(f, null);
                return;
            }

            const sv = parseInt(sets ? sets.value : '', 10) || (sets && sets.value ? 1 : 0);
            const rv = parseInt(reps ? reps.value : '', 10) || 0;
            const wv = parseFloat(weight ? weight.value : '');

            if (rv > 0 && sv > 0) {
                const load = (wv > 0 ? wv : 60) * rv * sv;
                let txt = 'Estimated training credit this week: <b>+' + creditFor(load / 6) + '</b>';
                txt += '<div style="font-size:12px; color:' + cssVar('--chart-text', '#5c6b84') + '; margin-top:4px;">' +
                    (wv > 0 ? sv + ' × ' + rv + ' @ ' + wv + ' kg' : sv + ' × ' + rv + ' reps, bodyweight') +
                    ' → ' + load.toLocaleString() + ' kg of work</div>';
                setPreview(f, txt);
            } else {
                setPreview(f, null);
            }
        }

        if (sel) {
            sel.addEventListener('change', function () {
                setMode(isTimed() ? 'timed' : 'rep');
                const hint = document.getElementById('exerciseHint');
                if (hint) hint.textContent = sel.options[sel.selectedIndex].text;
            });
        }
        if (repBtn) repBtn.addEventListener('click', function () { setMode('rep'); });
        if (timedBtn) timedBtn.addEventListener('click', function () { setMode('timed'); });
        [sets, reps, weight, minutes].forEach(function (el) { if (el) el.addEventListener('input', upd); });

        // sensible default mode on load
        if (minutes && minutes.value) setMode('timed');
        else if (sel) setMode(isTimed() ? 'timed' : 'rep');
    }

    // ---------- installable app (service worker for static assets) ----------
    function initServiceWorker() {
        if (!('serviceWorker' in navigator)) return;
        window.addEventListener('load', function () {
            navigator.serviceWorker.register('/static/sw.js').catch(function () {});
        });
    }

    // ---------- boot ----------
    document.addEventListener('DOMContentLoaded', function () {
        initTheme();
        initExerciseFilters();
        initCharts();
        initSidebar();
        initWeather();
        initToasts();
        initPasswordToggles();
        initImport();
        initStrengthPreview();
        initCardioPreview();
        initBodyPreview();
        initPerformancePreview();
        initExerciseForm();
        initServiceWorker();
    });
})();
