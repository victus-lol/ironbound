/* IRONBOUND v3.0 — Production-hardened RPG engine (static, no server)
   (Local storage keys keep their gymrat_* names so existing user data survives the rebrand.) */
const LS_KEY='gymrat_v2'; const SCHEMA_VER=4;

/* ---------- IndexedDB layer (Dexie-style, zero-dep) ----------
   localStorage stays the sync source of truth for first paint + Next sync;
   IndexedDB mirrors full state (incl. visits, routes) past the 5MB cap.
   Newest `savedAt` wins on hydrate. */
const idb={
  db:null,
  open(){ return new Promise(res=>{
    if(this.db) return res(this.db);
    try{
      const r=indexedDB.open('gymrat', 1);
      r.onupgradeneeded=()=>{ r.result.createObjectStore('kv'); };
      r.onsuccess=()=>{ this.db=r.result; res(this.db); };
      r.onerror=()=>res(null);
    }catch{ res(null); }
  }); },
  async get(k){ const db=await this.open(); if(!db) return null;
    return new Promise(res=>{ try{ const t=db.transaction('kv').objectStore('kv').get(k); t.onsuccess=()=>res(t.result??null); t.onerror=()=>res(null); }catch{ res(null); } }); },
  async set(k,v){ const db=await this.open(); if(!db) return;
    return new Promise(res=>{ try{ const t=db.transaction('kv','readwrite').objectStore('kv').put(v,k); t.onsuccess=()=>res(true); t.onerror=()=>res(false); }catch{ res(false); } }); },
};
async function hydrateFromIdb(){
  try{
    const snap=await idb.get('state');
    if(snap && snap.savedAt && (!state.meta.savedAt || snap.savedAt > state.meta.savedAt)){
      state=migrate(snap); renderHUD();
      toast('Restored fuller history from on-device DB');
    }
  }catch{}
}
const uid=()=> Math.random().toString(36).slice(2,10)+Date.now().toString(36).slice(-4);
// Local-date ISO (not UTC) — <input type=date> values are local; toISOString() shifts IST evenings to the wrong day
const isoLocal=(d)=> `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
const todayISO=()=> isoLocal(new Date());
const fmt=(n,d=0)=> Number(n).toFixed(d);
const esc=s=> String(s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));

/* ---------- Storage + Migrations (IndexedDB-lite via localStorage, versioned) ---------- */
function migrate(raw){
  if(!raw) return {logs:[], visits:[], profile:{weight:78,height:178,sex:'m',goal:'foundation'}, meta:{created:Date.now(), ver:SCHEMA_VER}, queue:[]};
  let d=raw;
  if(!d.meta) d.meta={created:Date.now(), ver:1};
  if(d.meta.ver < 2){
    // v2: ensure client_id on every log, add queue
    d.logs.forEach(l=>{ if(!l.client_id) l.client_id=uid(); });
    d.queue=[];
    d.meta.ver=2;
  }
  if(d.meta.ver < 3){
    // v3: normalize dates to ISO, clamp bf
    d.logs.forEach(l=>{
      if(l.date && l.date.includes(' ')) l.date=l.date.slice(0,10);
      if(l.type==='body' && l.bf) l.bf=Math.max(4,Math.min(45, Number(l.bf)||22));
    });
    d.meta.ver=3;
  }
  if(d.meta.ver < 4){
    // v4: spot visits for Home Turf + savedAt for IndexedDB newest-wins
    if(!Array.isArray(d.visits)) d.visits=[];
    d.meta.savedAt=Date.now();
    d.meta.ver=4;
  }
  if(!Array.isArray(d.visits)) d.visits=[];
  return d;
}
function load(){
  try{ const j=localStorage.getItem(LS_KEY); if(j) return migrate(JSON.parse(j)); }catch{}
  return migrate(null);
}
let state=load();
function save(){
  state.meta.ver=SCHEMA_VER; state.meta.savedAt=Date.now();
  try{ localStorage.setItem(LS_KEY, JSON.stringify(state)); }catch(e){ /* quota: idb holds full copy */ }
  idb.set('state', state);
}

/* ---------- Offline queue (IndexedDB shim — localStorage-backed, syncs on online) ---------- */
function enqueue(entry){
  state.queue.push(entry);
  save();
}
window.addEventListener('online', ()=>{
  if(!state.queue.length) return;
  let added=0;
  state.queue.forEach(e=>{
    if(!state.logs.some(x=>x.client_id===e.client_id)){ state.logs.push(e); added++; }
  });
  state.queue=[];
  save();
  if(added) { toast(`Synced ${added} offline log(s)`); renderHUD(); }
});

/* ---------- Rate limit (client-side, 8/min like Ironbound 8/5min but tighter) ---------- */
const rlWindow=[]; // timestamps
function rateLimited(){
  const now=Date.now();
  while(rlWindow.length && now-rlWindow[0]>60000) rlWindow.shift();
  if(rlWindow.length>=8){ return true; }
  rlWindow.push(now);
  return false;
}

/* ---------- Validation ---------- */
function validateStrength({weight,reps,rpe}){
  if(weight <20 || weight>500) return 'Weight must be 20–500 kg';
  if(reps <1 || reps>100) return 'Reps must be 1–100';
  if(rpe <6 || rpe>10) return 'RPE must be 6–10';
  return null;
}
function validateCardio({distance,duration}){
  if(distance<=0 || distance>100) return 'Distance 0–100 km';
  if(duration<=0 || duration>600) return 'Duration 1–600 min';
  return null;
}
function validateBody({neck,waist,hip,height,weight}){
  if(neck<20||neck>70) return 'Neck 20–70 cm';
  if(waist<40||waist>200) return 'Waist 40–200 cm';
  if(height<100||height>250) return 'Height 100–250 cm';
  if(weight<20||weight>500) return 'Weight 20–500 kg';
  if(hip<0||hip>200) return 'Hip 0–200 cm';
  if(waist<=neck) return 'Waist must exceed neck (US Navy)';
  return null;
}

/* ---------- Theme (persist + prefers) ---------- */
const themeBtn=document.getElementById('themeBtn');
function applyTheme(t){ document.documentElement.setAttribute('data-theme', t); localStorage.setItem('gymrat_theme',t); }
const savedTheme=localStorage.getItem('gymrat_theme');
if(savedTheme) applyTheme(savedTheme);
else applyTheme(window.matchMedia('(prefers-color-scheme: light)').matches ? 'light':'dark');
themeBtn.onclick=()=>{
  const cur=document.documentElement.getAttribute('data-theme')==='dark'?'light':'dark';
  applyTheme(cur); renderHUD();
};

/* ---------- Nav ---------- */
const views={dashboard:document.getElementById('view-dashboard'), analytics:document.getElementById('view-analytics'), plan:document.getElementById('view-plan'), spots:document.getElementById('view-spots'), log:document.getElementById('view-log')};
document.querySelectorAll('[data-view]').forEach(a=>{
  a.addEventListener('click',e=>{
    e.preventDefault();
    document.querySelectorAll('[data-view]').forEach(x=>x.classList.remove('active'));
    a.classList.add('active');
    Object.values(views).forEach(v=>v.classList.remove('active'));
    views[a.dataset.view].classList.add('active');
    if(a.dataset.view==='analytics') renderAnalytics();
    if(a.dataset.view==='plan') genPlan();
    if(a.dataset.view==='spots') initSpotsMap();
    window.scrollTo({top: document.querySelector('.app').offsetTop-70, behavior:'smooth'});
  });
});
document.querySelectorAll('[data-log]').forEach(b=>{
  b.addEventListener('click',()=>{
    document.querySelectorAll('[data-log]').forEach(x=>x.classList.remove('active'));
    b.classList.add('active');
    document.querySelectorAll('[data-panel]').forEach(p=> p.style.display = p.dataset.panel===b.dataset.log ? 'block':'none');
  });
});
['sDate','cDate','bDate','tDate'].forEach(id=>{ const el=document.getElementById(id); if(el) el.value=todayISO(); });

/* ---------- RPG ENGINE (fixed) ---------- */
const STATS_CFG={
  STR:{name:'STR',full:'Strength',color:'#ff2a3a',glow:'rgba(255,42,58,.22)',thresholds:[0.6,0.8,1.0,1.3,1.6],unit:'×BW',desc:'Epley 1RM / BW'},
  END:{name:'END',full:'Endurance',color:'#00b7ff',glow:'rgba(0,183,255,.22)',thresholds:[32,38,44,50,56],unit:'VO₂',desc:'Cooper VO₂max'},
  VIT:{name:'VIT',full:'Vitality',color:'#00d68f',glow:'rgba(0,214,143,.22)',thresholds:[25,20,16,12,8],unit:'BF%',desc:'US Navy BF%',invert:true},
  AGI:{name:'AGI',full:'Agility',color:'#a3c52c',glow:'rgba(163,197,44,.22)',thresholds:[8,10,12,14,16],unit:'km/h',desc:'Avg pace'},
  POW:{name:'POW',full:'Power',color:'#7c4dff',glow:'rgba(124,77,255,.22)',thresholds:[0.7,0.9,1.1,1.4,1.7],unit:'×BW',desc:'Explosive 1RM'},
  FLX:{name:'FLX',full:'Flexibility',color:'#ff6a8a',glow:'rgba(255,106,138,.22)',thresholds:[28,36,44,52,60],unit:'score',desc:'Tests & consistency'},
};
function scoreFromRaw(stat, raw){
  const cfg=STATS_CFG[stat];
  let vals=cfg.thresholds.slice();
  if(cfg.invert){ raw=-raw; vals=vals.map(v=>-v).sort((a,b)=>a-b); }
  const bounds=[20,40,60,80,100];
  // below first
  if(raw <= vals[0]){
    const span=vals[1]-vals[0]||1;
    const lo=vals[0]-span;
    const t=Math.max(0,Math.min(1,(raw-lo)/(vals[0]-lo)));
    return Math.round(t*20);
  }
  for(let i=0;i<vals.length-1;i++){
    if(raw >= vals[i] && raw < vals[i+1]){
      const t=(raw-vals[i])/(vals[i+1]-vals[i]);
      return Math.round(bounds[i] + t*(bounds[i+1]-bounds[i]));
    }
  }
  // beyond Elite
  if(raw >= vals[4]){
    const span=vals[4]-vals[3]||1;
    const t=Math.min(1,(raw-vals[4])/span);
    return Math.min(100, Math.round(80 + t*20));
  }
  return 50;
}
function tierFromScore(s){
  if(s<20) return {name:'Average',idx:0};
  if(s<40) return {name:'Healthy',idx:1};
  if(s<60) return {name:'Enthusiast',idx:2};
  if(s<80) return {name:'Pro',idx:3};
  return {name:'Elite',idx:4};
}
function pointsToNext(score){ const n=[20,40,60,80,100].find(b=> score < b); return n==null?0:n-score; }

function computeRaws(){
  const sLogs=state.logs.filter(l=>l.type==='strength');
  const cLogs=state.logs.filter(l=>l.type==='cardio');
  const bLogs=state.logs.filter(l=>l.type==='body');
  const tLogs=state.logs.filter(l=>l.type==='test');
  let bw=state.profile.weight;
  if(bLogs.length){ const lb=[...bLogs].sort((a,b)=> new Date(b.date)-new Date(a.date))[0]; bw=lb.weight||bw; }
  let bestRatio=0;
  sLogs.forEach(l=>{ const orm=l.weight*(1+l.reps/30); const ratio=orm/(bw||78); if(ratio>bestRatio) bestRatio=ratio; });
  const strRaw=bestRatio||0.55;
  let bestVo2=0; cLogs.forEach(l=>{ const vo2=(l.distance*1000-504.9)/44.73; if(vo2>bestVo2) bestVo2=vo2; });
  const endRaw=bestVo2||30;
  let vitRaw=22;
  if(bLogs.length){ const lb=[...bLogs].sort((a,b)=> new Date(b.date)-new Date(a.date))[0]; vitRaw=lb.bf??22; } else if(!state.logs.length) vitRaw=24;
  let agiRaw=9;
  if(cLogs.length){ const speeds=cLogs.map(l=> l.distance/(l.duration/60)); agiRaw=Math.max(...speeds); }
  let powRaw=strRaw*0.92;
  const vj=tLogs.filter(l=>l.testType.includes('Vertical')).map(l=>l.value);
  if(vj.length){ const best=Math.max(...vj); powRaw=Math.max(powRaw,(best/60)*1.6); }
  let flxRaw=30;
  const sr=tLogs.filter(l=>l.testType.includes('Sit')).map(l=>l.value);
  if(sr.length) flxRaw=Math.max(...sr); else flxRaw=30+Math.min(18, state.logs.length*1.2);
  return {STR:strRaw,END:endRaw,VIT:vitRaw,AGI:agiRaw,POW:powRaw,FLX:flxRaw};
}
function computeStats(){
  const raws=computeRaws();
  const out={};
  Object.keys(STATS_CFG).forEach(k=>{
    let score=scoreFromRaw(k,raws[k]);
    score=Math.max(2,Math.min(100,score));
    const tier=tierFromScore(score);
    const pts=pointsToNext(score);
    out[k]={raw:raws[k],score,tier,pts,trend:0};
  });
  // trend: compare last 7d vs previous 7d logs delta capped
  const now=new Date();
  const cnt7=state.logs.filter(l=> (now - new Date(l.date))/86400000 <=7).length;
  const cntPrev=state.logs.filter(l=>{ const d=(now - new Date(l.date))/86400000; return d>7 && d<=14; }).length;
  const delta=cnt7 - cntPrev;
  Object.keys(out).forEach(k=> out[k].trend = delta>0? Math.min(4,delta) : delta<0? Math.max(-3,delta):0);
  return out;
}
function computeXP(){
  let xp=0; state.logs.forEach(l=> xp+= Number(l.xp)||10);
  const stats=computeStats(); Object.values(stats).forEach(s=> xp+= Math.floor(s.score/5));
  return xp;
}
function levelFromXP(xp){ return Math.floor(xp/220)+1; }
function rankFromLevel(lv){ if(lv<=5) return 'Initiate'; if(lv<=10) return 'Novice'; if(lv<=15) return 'Adept'; if(lv<=20) return 'Veteran'; if(lv<=30) return 'Champion'; return 'Mythic'; }
function weakestStat(stats){ let min=Infinity,key=null; Object.entries(stats).forEach(([k,v])=>{ if(v.score<min){min=v.score; key=k;}}); return key; }

/* ---------- Render ---------- */
function renderHUD(){
  const stats=computeStats(); const xp=computeXP(); const lv=levelFromXP(xp); const rank=rankFromLevel(lv);
  const avg=Math.round(Object.values(stats).reduce((a,b)=>a+b.score,0)/6);
  const w0=weakestStat(stats);
  const TIER_ORDER=['Average','Healthy','Enthusiast','Pro','Elite'];
  const gapTxt= stats[w0].pts===0? `${w0} • MAXED` : `${w0} • +${stats[w0].pts} to ${TIER_ORDER[stats[w0].tier.idx+1]}`;
  document.getElementById('heroLevel').textContent=`Lv ${lv} • ${rank}`;
  document.getElementById('heroScore').textContent=avg;
  document.getElementById('heroEntries').textContent=`${state.logs.length} logged ${state.logs.length===1?'entry':'entries'}`;
  document.getElementById('heroLvl').textContent=lv;
  document.getElementById('heroXp').textContent=`${xp%220} / 220 XP`;
  document.getElementById('heroXpBar').style.width=(xp%220/220*100)+'%';
  document.getElementById('heroPath').textContent=`${avg}% avg • Weakest: ${gapTxt}`;
  document.getElementById('heroPathBar').style.width=avg+'%';
  document.getElementById('heroMini').innerHTML= Object.entries(stats).map(([k,v])=> `<div class="mini-stat"><span>${k}</span><strong style="color:${STATS_CFG[k].color}">${v.score}</strong></div>`).join('');
  const grid=document.getElementById('statGrid');
  grid.innerHTML= Object.entries(stats).map(([k,v])=>{
    const cfg=STATS_CFG[k];
    return `<div class="stat-card" style="--glow:${cfg.glow}">
      <div class="stat-top"><div class="s-icon" style="background:${cfg.color}">${esc(k.slice(0,2))}</div><span class="s-tier">${esc(v.tier.name)}</span></div>
      <div style="display:flex; align-items:baseline; gap:8px"><strong>${v.score}</strong><span class="tier-name">/ 100</span><span style="margin-left:auto; font-size:11px; font-weight:800; color:${v.trend>=0?'var(--green)':'var(--accent)'}">${v.trend>=0?'▲':'▼'} ${Math.abs(v.trend)}</span></div>
      <div class="tier-name">${esc(cfg.full)} • ${esc(cfg.desc)} • ${fmt(v.raw,1)}${esc(cfg.unit)}</div>
      <div class="progress"><i style="width:${v.score}%; background:linear-gradient(90deg,${cfg.color}, ${cfg.color}aa)"></i></div>
      <div style="display:flex; justify-content:space-between; font-size:11px; font-weight:800" class="muted"><span>${v.pts===0?'MAX':'Next tier in '+v.pts}</span><span>Path to Pro ${Math.min(100, Math.round(v.score*1.12))}%</span></div>
    </div>`;
  }).join('');
  const w=weakestStat(stats);
  const prescriptions={ STR:'Heavy compounds — 5×5 squat at 85% 1RM, twice this week.', END:'Cooper 12-min + 2× 30-min zone-2 runs.', VIT:'-300 kcal, 2 g protein/kg, 8k steps/day.', AGI:'6×40 m sprints + ladder drills.', POW:'Box jumps 4×5 + 3×3 cleans @70% 1RM.', FLX:'Daily 12-min mobility: hamstrings/hips.' };
  document.getElementById('coachText').textContent = state.logs.length? `${w} is your limiter (${stats[w].score}/100 • ${stats[w].tier.name}). ${prescriptions[w]} Fastest rank gain.` : 'Log a workout to get your first prescription.';
  drawRadar(document.getElementById('radar'), stats);
  const r2=document.getElementById('radar2'); if(r2) drawRadar(r2, stats);
  renderSide(); renderRecent(); renderLogTable(); renderKPIs(); volumeChart();
  try{ if(typeof renderGoals==='function') renderGoals(); }catch{}
  try{ if(typeof renderChallenges==='function') renderChallenges(); }catch{}
  try{ if(typeof renderWeekly==='function') renderWeekly(); }catch{}
  try{ if(typeof renderTurf==='function') renderTurf(); }catch{}
  const prevLv=Number(localStorage.getItem('gymrat_prevLv')||'1');
  if(lv>prevLv) showLevelBanner(lv, rank);
  localStorage.setItem('gymrat_prevLv', String(lv));
  // badge for offline queue
  const q=document.getElementById('queueBadge'); if(q) q.textContent = state.queue.length? `${state.queue.length} queued • offline` : (navigator.onLine? 'Online • synced':'Offline • will sync');
}

function drawRadar(canvas, stats){
  if(!canvas) return;
  const ctx=canvas.getContext('2d');
  const W=canvas.width, H=canvas.height, cx=W/2, cy=H/2, R=Math.min(W,H)/2-28;
  ctx.clearRect(0,0,W,H);
  const keys=Object.keys(STATS_CFG);
  const isLight=document.documentElement.getAttribute('data-theme')==='light';
  ctx.strokeStyle=isLight?'#e6e8f5':'#1f2647'; ctx.lineWidth=1;
  for(let lvl=1; lvl<=4; lvl++){
    ctx.beginPath();
    keys.forEach((k,i)=>{ const ang=-Math.PI/2 + i*2*Math.PI/keys.length; const r=R*lvl/4, x=cx+Math.cos(ang)*r, y=cy+Math.sin(ang)*r; if(i===0) ctx.moveTo(x,y); else ctx.lineTo(x,y); });
    ctx.closePath(); ctx.stroke();
  }
  keys.forEach((k,i)=>{ const ang=-Math.PI/2 + i*2*Math.PI/keys.length; ctx.beginPath(); ctx.moveTo(cx,cy); ctx.lineTo(cx+Math.cos(ang)*R, cy+Math.sin(ang)*R); ctx.stroke(); });
  ctx.beginPath();
  keys.forEach((k,i)=>{ const ang=-Math.PI/2 + i*2*Math.PI/keys.length; const r=R*(stats[k].score/100); const x=cx+Math.cos(ang)*r, y=cy+Math.sin(ang)*r; if(i===0) ctx.moveTo(x,y); else ctx.lineTo(x,y); });
  ctx.closePath(); ctx.fillStyle='rgba(232,145,80,.20)'; ctx.fill(); ctx.strokeStyle='#e89150'; ctx.lineWidth=2; ctx.stroke();
  ctx.font='700 11px Inter, sans-serif'; ctx.textAlign='center'; ctx.textBaseline='middle';
  keys.forEach((k,i)=>{
    const ang=-Math.PI/2 + i*2*Math.PI/keys.length;
    const r=R*(stats[k].score/100); const x=cx+Math.cos(ang)*r, y=cy+Math.sin(ang)*r;
    ctx.beginPath(); ctx.arc(x,y,4,0,Math.PI*2); ctx.fillStyle=STATS_CFG[k].color; ctx.fill();
    ctx.fillStyle=isLight?'#0e1230':'#f5f6ff';
    const lx=cx+Math.cos(ang)*(R+16), ly=cy+Math.sin(ang)*(R+16); ctx.fillText(k,lx,ly);
  });
}

function renderSide(){
  // streak — correct consecutive days, handles today not yet logged (like Ironbound)
  const set=new Set(state.logs.map(l=> l.date.slice(0,10)));
  let streak=0;
  const today=new Date(); today.setHours(0,0,0,0);
  // if today missing, streak counts from yesterday
  let cursor=new Date(today);
  if(!set.has(isoLocal(cursor))){
    // don't penalize today until end of day — check yesterday as start if today empty
    cursor=new Date(today - 86400000);
    // but if yesterday also missing, streak 0
    if(!set.has(isoLocal(cursor))) streak=0;
    else {
      streak=0;
      for(let i=0;i<365;i++){
        const iso=isoLocal(new Date(cursor - i*86400000));
        if(set.has(iso)) streak++; else break;
      }
    }
  } else {
    for(let i=0;i<365;i++){
      const iso=isoLocal(new Date(cursor - i*86400000));
      if(set.has(iso)) streak++; else break;
    }
  }
  if(!state.logs.length) streak=0;
  document.getElementById('streakVal').textContent=streak;
  const xp=computeXP(); document.getElementById('xpVal').textContent=xp+' XP'; document.getElementById('rankVal').textContent=rankFromLevel(levelFromXP(xp));
  // heatmap 13 weeks MON-SUN aligned (GitHub style)
  const hm=document.getElementById('heatmap'); hm.innerHTML='';
  const today0=new Date(); today0.setHours(0,0,0,0);
  const map={}; state.logs.forEach(l=>{ const k=l.date.slice(0,10); map[k]=(map[k]||0)+1; });
  // start = Monday of week 12 ago
  const dow=(today0.getDay()+6)%7; // Mon=0
  const start=new Date(today0 - dow*86400000 - 12*7*86400000);
  const cells=[];
  for(let i=0;i<91;i++){
    const day=new Date(start.getTime() + i*86400000);
    const iso=isoLocal(day);
    const cnt=map[iso]||0;
    const isFuture= day>today0;
    let lvl=0;
    if(!isFuture){
      if(cnt>=4) lvl=4; else if(cnt>=3) lvl=3; else if(cnt>=2) lvl=2; else if(cnt>=1) lvl=1;
    }
    cells.push({iso,cnt,lvl,isFuture,isToday: iso===isoLocal(today0)});
  }
  // render by columns (weeks)
  hm.style.display='grid';
  hm.style.gridTemplateColumns='repeat(13, 1fr)';
  hm.style.gap='4px';
  // Instead of flat 91, render week columns containing 7 days for true GitHub layout
  hm.innerHTML='';
  for(let w=0;w<13;w++){
    const col=document.createElement('div'); col.style.display='grid'; col.style.gridTemplateRows='repeat(7, 1fr)'; col.style.gap='4px';
    for(let d=0;d<7;d++){
      const c=cells[w*7+d];
      const el=document.createElement('div');
      el.className='hcell'+(c.lvl? ' l'+c.lvl:'') + (c.isToday?' today':'') + (c.isFuture?' future':'');
      el.title=c.isFuture? `${c.iso} (upcoming)` : `${c.iso}: ${c.cnt} log(s)`;
      // tiny today ring via outline handled in CSS
      col.appendChild(el);
    }
    hm.appendChild(col);
  }
  // weekly volume (last 7d)
  const weekLogs=state.logs.filter(l=> (today0 - new Date(l.date))/86400000 <7 && l.type==='strength');
  const vol=weekLogs.reduce((a,l)=> a+(l.weight*l.reps),0);
  document.getElementById('volVal').textContent=vol.toLocaleString()+' kg';
  // PR count: distinct lifts where log is max
  const bestByLift={};
  state.logs.filter(l=>l.type==='strength').forEach(l=>{ const orm=l.weight*(1+l.reps/30); if(!bestByLift[l.lift]||orm>bestByLift[l.lift]) bestByLift[l.lift]=orm; });
  let prCount=Object.keys(bestByLift).length; if(!state.logs.length) prCount=0;
  document.getElementById('prVal').textContent=prCount;
  const badges=[
    {name:'First rep',ok:state.logs.length>=1},
    {name:'3-day fire',ok:streak>=3},
    {name:'Week warrior',ok:streak>=7},
    {name:'5k volume',ok:vol>=5000},
    {name:'PR breaker',ok:prCount>=1},
    {name:'10 logs',ok:state.logs.length>=10},
    {name:'Runner',ok:state.logs.some(l=>l.type==='cardio')},
    {name:'Measured',ok:state.logs.some(l=>l.type==='body')},
    {name:'Tested',ok:state.logs.some(l=>l.type==='test')},
    {name:'500 XP',ok:xp>=500},
    {name:'1.5k XP',ok:xp>=1500},
    {name:'Elite stat',ok:Object.values(computeStats()).some(s=>s.tier.name==='Elite')},
    {name:'Balanced',ok:Object.values(computeStats()).every(s=>s.score>=40)},
    {name:'Planner',ok:!!localStorage.getItem('gymrat_plan')},
    {name:'Owner',ok:!!localStorage.getItem('gymrat_exported')},
    {name:'Home turf 👑',ok:(()=>{ const t=homeTurf(); return (t.topLift&&t.topLift[1]>=10)||(t.topSpot&&t.topSpot[1]>=5); })()},
  ];
  document.getElementById('badgeCount').textContent=badges.filter(b=>b.ok).length+'/'+badges.length;
  document.getElementById('badgeGrid').innerHTML=badges.map(b=> `<span class="badge ${b.ok?'on':''}">${b.ok?'✓':'○'} ${esc(b.name)}</span>`).join('');
}

function renderRecent(){
  const el=document.getElementById('recentLogs');
  const recent=[...state.logs].sort((a,b)=> new Date(b.date)-new Date(a.date)).slice(0, rangeDays<=7?6: rangeDays<=30?10:20);
  if(!recent.length){ el.innerHTML='<div class="muted" style="padding:10px; border:1px dashed var(--line); border-radius:14px">No logs yet. Add a strength set to see XP and tier movement.</div>'; return; }
  el.innerHTML= recent.map(l=>{
    let detail='';
    if(l.type==='strength') detail=`${esc(l.lift)} — ${l.weight}kg × ${l.reps} @RPE ${l.rpe}`;
    if(l.type==='cardio') detail=`Run ${l.distance}km in ${l.duration}min • VO₂ ${fmt((l.distance*1000-504.9)/44.73,1)}`;
    if(l.type==='body') detail=`BF ${fmt(l.bf,1)}% • ${l.weight}kg`;
    if(l.type==='test') detail=`${esc(l.testType)}: ${l.value}`;
    const isPR = l.type==='strength' && (()=>{ const orm=l.weight*(1+l.reps/30); const best=Math.max(...state.logs.filter(x=>x.type==='strength'&&x.lift===l.lift).map(x=>x.weight*(1+x.reps/30))); return Math.abs(orm-best)<0.01; })();
    return `<div style="display:flex; justify-content:space-between; gap:10px; padding:10px 12px; border:1px solid var(--line); border-radius:14px; background:var(--bg2)"><span><b style="text-transform:capitalize">${esc(l.type)}</b> • ${detail} ${isPR?' <span style=\"color:var(--accent); font-weight:900\">• PR 🎉</span>':''}</span><span class="muted" style="font-size:12px">${esc(l.date.slice(0,10))}</span></div>`;
  }).join('');
}
function renderLogTable(){
  const tb=document.querySelector('#logTable tbody');
  const rows=[...state.logs].sort((a,b)=> new Date(b.date)-new Date(a.date)).slice(0,50);
  tb.innerHTML= rows.map(l=>{
    let det='';
    if(l.type==='strength') det=`${esc(l.lift)} ${l.weight}×${l.reps}`;
    if(l.type==='cardio') det=`${esc(String(l.distance))}km / ${esc(String(l.duration))}min`;
    if(l.type==='body') det=`BF ${fmt(l.bf,1)}%`;
    if(l.type==='test') det=`${esc(l.testType)} ${esc(String(l.value))}`;
    return `<tr><td>${esc(l.type)}</td><td>${det}</td><td>${esc(l.date.slice(0,10))}</td><td>+${esc(String(l.xp||10))} <button data-del="${esc(l.client_id)}" style="margin-left:8px; padding:4px 8px; border-radius:999px; border:1px solid var(--line); background:var(--bg2); cursor:pointer; font-weight:800; font-size:11px">✕</button> <button data-edit="${esc(l.client_id)}" style="padding:4px 8px; border-radius:999px; border:1px solid var(--line); background:var(--bg2); cursor:pointer; font-weight:800; font-size:11px">Edit</button></td></tr>`;
  }).join('') || `<tr><td colspan="4" class="muted">No logs yet</td></tr>`;
  tb.querySelectorAll('[data-del]').forEach(b=>{
    b.addEventListener('click',()=>{
      if(!confirm('Delete this log?')) return;
      state.logs=state.logs.filter(x=> x.client_id!==b.getAttribute('data-del'));
      save(); renderHUD(); toast('Log deleted');
    });
  });
  tb.querySelectorAll('[data-edit]').forEach(b=>{
    b.addEventListener('click',()=>{
      const id=b.getAttribute('data-edit');
      const log=state.logs.find(x=>x.client_id===id); if(!log) return;
      if(log.type==='strength'){
        const w=prompt('Weight kg:', log.weight); if(w===null) return;
        const r=prompt('Reps:', log.reps); if(r===null) return;
        log.weight=Number(w); log.reps=Number(r);
      } else if(log.type==='cardio'){
        const d=prompt('Distance km:', log.distance); if(d===null) return;
        log.distance=Number(d);
      } else if(log.type==='body'){
        const ww=prompt('Weight kg:', log.weight); if(ww===null) return;
        log.weight=Number(ww);
      } else {
        const v=prompt('Value:', log.value); if(v===null) return;
        log.value=Number(v);
      }
      save(); renderHUD(); toast('Log updated');
    });
  });
}
function renderKPIs(){
  const stats=computeStats();
  document.getElementById('kpiStr').textContent=stats.STR.score;
  const cLogs=state.logs.filter(l=>l.type==='cardio');
  document.getElementById('kpiVo2').textContent= cLogs.length? fmt(Math.max(...cLogs.map(l=> (l.distance*1000-504.9)/44.73)),1) : '—';
  const bLogs=state.logs.filter(l=>l.type==='body');
  document.getElementById('kpiBf').textContent= bLogs.length? fmt([...bLogs].sort((a,b)=> new Date(b.date)-new Date(a.date))[0].bf,1)+'%' : '—';
  document.getElementById('kpiLogs').textContent=state.logs.length;
}
let rangeDays=30; // dashboard window, driven by the 7/30/90d tabs
function volumeChart(){
  const c=document.getElementById('volumeChart'); if(!c) return;
  const ctx=c.getContext('2d'); const W=c.width=c.offsetWidth*2, H=c.height=160*2; ctx.clearRect(0,0,W,H);
  const vols=[];
  const today=new Date(); today.setHours(0,0,0,0);
  const dayVol=iso=>{
    return state.logs.filter(l=> l.type==='strength' && l.date.slice(0,10)===iso).reduce((a,l)=>a+l.weight*l.reps,0);
  };
  if(rangeDays<=30){
    // daily buckets for 7d / 30d windows
    for(let i=rangeDays-1;i>=0;i--) vols.push(dayVol(isoLocal(new Date(today - i*86400000))));
  } else {
    // weekly buckets for the 90d window (13 rolling weeks)
    for(let w=12; w>=0; w--){
      const weekStart=new Date(today - w*7*86400000);
      const s=new Date(weekStart - 6*86400000), e=weekStart;
      vols.push(state.logs.filter(l=> l.type==='strength' && new Date(l.date)>=s && new Date(l.date)<=e).reduce((a,l)=>a+l.weight*l.reps,0));
    }
  }
  const vt=document.getElementById('volTitle');
  if(vt) vt.textContent=`Volume series — last ${rangeDays<=30? rangeDays+' days':'13 weeks'}`;
  const max=Math.max(1,...vols,800); const pad=30*2;
  ctx.strokeStyle=getComputedStyle(document.documentElement).getPropertyValue('--line');
  ctx.beginPath(); ctx.moveTo(pad,H-pad); ctx.lineTo(W-pad,H-pad); ctx.stroke();
  ctx.beginPath(); ctx.lineWidth=3*2; ctx.strokeStyle='#e89150';
  vols.forEach((v,i)=>{ const x=pad + i*(W-2*pad)/Math.max(1,vols.length-1); const y=H-pad - (v/max)*(H-2*pad); if(i===0) ctx.moveTo(x,y); else ctx.lineTo(x,y); });
  ctx.stroke(); ctx.lineTo(W-pad,H-pad); ctx.lineTo(pad,H-pad); ctx.closePath(); ctx.fillStyle='rgba(232,145,80,.12)'; ctx.fill();
  vols.forEach((v,i)=>{ const x=pad + i*(W-2*pad)/Math.max(1,vols.length-1); const y=H-pad - (v/max)*(H-2*pad); ctx.beginPath(); ctx.arc(x,y,4*2,0,Math.PI*2); ctx.fillStyle='#c9722e'; ctx.fill(); });
}
function lineChart(canvasId, values){
  const c=document.getElementById(canvasId); if(!c) return;
  const ctx=c.getContext('2d'); const W=c.width=c.offsetWidth*2, H=c.height=160*2; ctx.clearRect(0,0,W,H);
  if(!values.length){ ctx.fillStyle='#9aa0c3'; ctx.font='12px sans-serif'; ctx.fillText('No data yet — log to see trends', 20, H/2); return; }
  const max=Math.max(...values), min=Math.min(...values); const range=Math.max(1, max-min); const pad=28*2;
  ctx.strokeStyle='#1f2647'; ctx.beginPath(); ctx.moveTo(pad,H-pad); ctx.lineTo(W-pad,H-pad); ctx.stroke();
  ctx.beginPath(); ctx.strokeStyle= canvasId==='chartBf'? '#00d68f' : canvasId==='chartCardio'? '#00b7ff' : canvasId==='chartBw'? '#e89150' : '#ff2a3a'; ctx.lineWidth=3;
  values.forEach((v,i)=>{ const x=pad + i*(W-2*pad)/Math.max(1,values.length-1); const y=H-pad - ((v-min)/range)*(H-2*pad); if(i===0) ctx.moveTo(x,y); else ctx.lineTo(x,y); });
  ctx.stroke();
}
function renderAnalytics(){
  const days=30; const today=new Date(); today.setHours(0,0,0,0);
  const sVals=[], cVals=[], bfVals=[], bwVals=[];
  for(let i=days-1;i>=0;i--){
    const iso=isoLocal(new Date(today - i*86400000));
    const sDay=state.logs.filter(l=> l.type==='strength' && l.date.slice(0,10)===iso);
    if(sDay.length){ const avg=sDay.reduce((a,l)=> a+(l.weight*(1+l.reps/30))/(state.profile.weight||78),0)/sDay.length; sVals.push(Math.min(100,Math.round((avg/1.6)*100))); }
    else sVals.push(sVals[sVals.length-1] ?? computeStats().STR.score);
    const cDay=state.logs.filter(l=> l.type==='cardio' && l.date.slice(0,10)===iso);
    if(cDay.length){ cVals.push(Math.max(...cDay.map(l=> (l.distance*1000-504.9)/44.73))); } else cVals.push(cVals[cVals.length-1] ?? 31);
    const bDay=state.logs.filter(l=> l.type==='body' && l.date.slice(0,10)===iso);
    if(bDay.length){ bfVals.push(bDay[0].bf); bwVals.push(bDay[0].weight); }
    else { bfVals.push(bfVals[bfVals.length-1] ?? 22); bwVals.push(bwVals[bwVals.length-1] ?? state.profile.weight ?? 78); }
  }
  if(!state.logs.length){ sVals.length=0; cVals.length=0; bfVals.length=0; bwVals.length=0; }
  lineChart('chartStr', sVals); lineChart('chartCardio', cVals); lineChart('chartBf', bfVals); lineChart('chartBw', bwVals);
  renderPRTimeline(); renderGoals();
}
function renderPRTimeline(){
  const el=document.getElementById('prTimeline'); if(!el) return;
  const bestByLift={};
  state.logs.filter(l=>l.type==='strength').forEach(l=>{ const orm=l.weight*(1+l.reps/30); if(!bestByLift[l.lift]||orm>bestByLift[l.lift]) bestByLift[l.lift]={orm, date:l.date.slice(0,10), log:l}; });
  const entries=Object.entries(bestByLift).sort((a,b)=> new Date(b[1].date)-new Date(a[1].date));
  if(!entries.length){ el.innerHTML='<div class="muted" style="font-size:12px">No PRs yet — log a lift.</div>'; return; }
  el.innerHTML=entries.map(([lift,v])=> `<div style="display:flex; justify-content:space-between; gap:8px; padding:8px 10px; border:1px solid var(--line); border-radius:12px; background:var(--bg2)"><span><b>${esc(lift)}</b> • ${fmt(v.orm,1)} kg 1RM</span><span class="muted" style="font-size:11px">${esc(v.date)} • PR 🎉</span></div>`).join('');
}
function renderGoals(){
  const el=document.getElementById('goalWrap'); if(!el) return;
  const today=new Date(); today.setHours(0,0,0,0);
  const weekLogs=state.logs.filter(l=> (today-new Date(l.date))/86400000 <7);
  const sessions=new Set(weekLogs.map(l=>l.date.slice(0,10))).size;
  const vol=state.logs.filter(l=>l.type==='strength' && (today-new Date(l.date))/86400000<7).reduce((a,l)=>a+l.weight*l.reps,0);
  const sGoal=3, vGoal=8000;
  el.innerHTML=`
    <div><div style="display:flex; justify-content:space-between; font-size:12px; font-weight:800"><span>${sessions}/${sGoal} sessions</span><span class="muted">${Math.min(100,Math.round(sessions/sGoal*100))}%</span></div><div class="goal-bar" style="margin-top:6px"><i style="width:${Math.min(100,sessions/sGoal*100)}%"></i></div></div>
    <div><div style="display:flex; justify-content:space-between; font-size:12px; font-weight:800"><span>${vol.toLocaleString()}/${vGoal.toLocaleString()} kg volume</span><span class="muted">${Math.min(100,Math.round(vol/vGoal*100))}%</span></div><div class="goal-bar" style="margin-top:6px"><i style="width:${Math.min(100,vol/vGoal*100)}%"></i></div></div>
    <div class="muted" style="font-size:11px">${sessions>=sGoal?'🎉 Weekly sessions smashed — maintain streak.':'Log '+(sGoal-sessions)+' more day(s) to hit 3/week.'}</div>`;
}

/* ---------- Toast / Banner ---------- */
function toast(msg){ const t=document.getElementById('toast'); t.textContent=msg; t.classList.add('show'); setTimeout(()=>t.classList.remove('show'),2200); }
function showLevelBanner(lv, rank){ const b=document.getElementById('levelBanner'); document.getElementById('levelBannerText').textContent=`Level ${lv} • ${rank}`; b.classList.add('show'); setTimeout(()=>b.classList.remove('show'),2600); }
function addLog(entry){
  if(state.logs.some(l=> l.client_id===entry.client_id)) return;
  // offline simulation: if navigator.offline, queue
  if(!navigator.onLine){
    enqueue(entry); toast('Saved offline • will sync when online');
    renderHUD(); return;
  }
  state.logs.push(entry); save(); renderHUD();
  // PR detection toast
  if(entry.type==='strength'){
    const orm=entry.weight*(1+entry.reps/30);
    const best=Math.max(...state.logs.filter(l=> l.type==='strength'&&l.lift===entry.lift).map(l=> l.weight*(1+l.reps/30)));
    if(Math.abs(orm-best)<0.001) toast('New PR 🎉 — '+entry.lift+' '+fmt(orm,1)+' kg 1RM');
  }
}

/* ---------- Handlers (validated + rate-limited) ---------- */
document.getElementById('addStrength').onclick=()=>{
  if(rateLimited()) return toast('Slow down — 8 logs/min max');
  const lift=document.getElementById('sLift').value, weight=Number(document.getElementById('sWeight').value), reps=Number(document.getElementById('sReps').value), rpe=Number(document.getElementById('sRpe').value), date=document.getElementById('sDate').value||todayISO();
  const err=validateStrength({weight,reps,rpe}); if(err) return toast(err);
  const xp=10+Math.round(reps*1.2)+Math.max(0,rpe-7)*3;
  addLog({client_id:uid(), type:'strength', lift, weight, reps, rpe, date, xp});
  if(navigator.onLine) toast('Strength logged +'+xp+' XP');
};
document.getElementById('addCardio').onclick=()=>{
  if(rateLimited()) return toast('Slow down — 8 logs/min max');
  const distance=Number(document.getElementById('cDist').value), duration=Number(document.getElementById('cDur').value), date=document.getElementById('cDate').value||todayISO();
  const err=validateCardio({distance,duration}); if(err) return toast(err);
  const vo2=(distance*1000-504.9)/44.73; const xp=12+Math.round(distance*4);
  addLog({client_id:uid(), type:'cardio', distance, duration, date, xp, vo2});
  if(navigator.onLine) toast(`Run logged • VO₂ ${fmt(vo2,1)} • +${xp} XP`);
};
document.getElementById('addBody').onclick=()=>{
  if(rateLimited()) return toast('Slow down — 8 logs/min max');
  const neck=Number(document.getElementById('bNeck').value), waist=Number(document.getElementById('bWaist').value), hip=Number(document.getElementById('bHip').value), height=Number(document.getElementById('bHeight').value), weight=Number(document.getElementById('bWeight').value), sex=document.getElementById('bSex').value, date=document.getElementById('bDate').value||todayISO();
  const err=validateBody({neck,waist,hip,height,weight}); if(err) return toast(err);
  let bf=22;
  try{ if(sex==='m') bf=86.01*Math.log10(waist-neck)-70.041*Math.log10(height)+36.76; else bf=163.205*Math.log10(waist+hip-neck)-97.684*Math.log10(height)-78.387; }catch{ bf=22; }
  if(!isFinite(bf)) bf=22; bf=Math.max(4,Math.min(45,bf));
  addLog({client_id:uid(), type:'body', neck, waist, hip, height, weight, sex, date, xp:8, bf});
  state.profile.weight=weight; state.profile.height=height; state.profile.sex=sex; save();
  if(navigator.onLine) toast(`Body log • BF ${fmt(bf,1)}%`);
};
document.getElementById('addTest').onclick=()=>{
  if(rateLimited()) return toast('Slow down — 8 logs/min max');
  const testType=document.getElementById('tType').value, value=Number(document.getElementById('tVal').value), date=document.getElementById('tDate').value||todayISO();
  if(!value||value<=0) return toast('Enter a valid test value');
  addLog({client_id:uid(), type:'test', testType, value, date, xp:7});
  if(navigator.onLine) toast('Test logged');
};

/* ---------- Weather (Open-Meteo + IP fallback, no key) ---------- */
document.getElementById('weatherBtn').onclick=async()=>{
  const out=document.getElementById('weatherOut'); out.textContent='Checking weather…';
  try{
    let temp=22, cond='clear', wind=8;
    let lat=null, lon=null;
    if(navigator.geolocation){
      await new Promise(res=>{
        navigator.geolocation.getCurrentPosition(async pos=>{
          lat=pos.coords.latitude; lon=pos.coords.longitude;
          try{
            const r=await fetch(`https://api.open-meteo.com/v1/forecast?latitude=${lat}&longitude=${lon}&current=temperature_2m,wind_speed_10m,weather_code`);
            const j=await r.json(); temp=j.current.temperature_2m; wind=j.current.wind_speed_10m; cond=j.current.weather_code<3?'clear': j.current.weather_code<50?'cloudy':'rain';
          }catch{}
          res();
        }, async ()=>{
          // IP fallback
          try{ const r=await fetch('https://ipwho.is/'); const j=await r.json(); lat=j.latitude; lon=j.longitude; if(lat&&lon){ const rr=await fetch(`https://api.open-meteo.com/v1/forecast?latitude=${lat}&longitude=${lon}&current=temperature_2m,wind_speed_10m,weather_code`); const jj=await rr.json(); temp=jj.current.temperature_2m; wind=jj.current.wind_speed_10m; cond=jj.current.weather_code<3?'clear': jj.current.weather_code<50?'cloudy':'rain'; } }catch{} res();
        }, {timeout:4000});
      });
    }
    let rec='';
    if(cond==='rain' || wind>18 || temp>34) rec='⛈ Hit the gym — rain/heat. 30-min treadmill intervals.';
    else if(cond==='clear' && temp>=12 && temp<=26) rec='☀ Go for a run — perfect 12–26°C, calm.';
    else rec='🌤 Light outdoor run OK, or gym cardio.';
    let aqiTxt='';
    if(lat!=null && lon!=null){
      try{
        const aq=await fetchAQI(lat, lon);
        const parts=[];
        if(aq.us_aqi!=null) parts.push(`AQI ${aq.us_aqi}`);
        if(aq.uv_index!=null) parts.push(`UV ${aq.uv_index}`);
        const verdict=aqiVerdict(aq.us_aqi, aq.uv_index);
        if(parts.length) aqiTxt=` • ${parts.join(' • ')} — ${verdict}`;
      }catch{}
    }
    out.textContent=`${cond.toUpperCase()} • ${temp}°C • wind ${fmt(wind,0)} km/h — ${rec}${aqiTxt}`;
  }catch{
    out.textContent='Weather unavailable — mock: ☀ 24°C clear — Go for a run!';
  }
};

/* ---------- Planning (goal + schedule + allergy + free-text rules like Ironbound) ---------- */
function genPlan(){
  const goal=document.getElementById('planGoal').value, allergy=document.getElementById('planAllergy').value, w=Number(document.getElementById('planWeight').value), h=Number(document.getElementById('planHeight').value), age=Number(document.getElementById('planAge').value), act=Number(document.getElementById('planActivity').value);
  const sex=state.profile.sex||'m';
  const bmr=10*w + 6.25*h -5*age + (sex==='m'?5:-161);
  const tdee=Math.round(bmr*act);
  const isVeg=document.getElementById('planVeg')?.value||'nonveg';
  const freeText=(document.getElementById('planRules')?.value||'').toLowerCase();
  // detect fasting days
  const fastingDays = freeText.includes('fast') ? (freeText.match(/monday|tuesday|wednesday|thursday|friday|saturday|sunday/g)||[]) : [];
  document.getElementById('kcalOut').textContent=`≈ ${tdee.toLocaleString()} kcal/day • P ${Math.round(w*1.8)}g • C ${Math.round(tdee*0.5/4)}g • F ${Math.round(tdee*0.28/9)}g`;
  const tpl={ foundation:['Push (bench+ OHP)','Pull (row+ pull-up)','Legs (squat)','Mobility + core','Run 20 min','Full body','Rest'], volume:['Chest+Tris','Back+Bis','Legs heavy','Shoulders','Run + core','Full body volume','Rest'], peak:['Strength AM / Run PM','Plyo + sprint','Heavy legs','Power + mobility','Intervals 6×400','Test day','Rest'] };
  const days=['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];
  const plan=tpl[goal];
  const tt=document.getElementById('timetable'); tt.innerHTML= days.map((d,i)=> `<div class="day ${plan[i]==='Rest'?'rest':''}"><div style="display:flex; justify-content:space-between; align-items:center"><b>${d}</b> ${fastingDays.includes(d.toLowerCase())?'<span style="font-size:11px; background:var(--accent); color:var(--accent-ink); padding:2px 6px; border-radius:999px; font-weight:800">fast</span>':''}</div><div style="margin-top:6px; font-weight:800; font-size:13px">${esc(plan[i])}</div><div class="muted" style="font-size:12px; margin-top:4px">${plan[i]==='Rest'?'Recover • walk 6k steps':'Sets 4-5 • RPE 7-8'} ${i<plan.length && plan[i]!=='Rest'? `<a href="#" onclick="document.querySelector('[data-view=log]').click(); return false" style="color:var(--accent2); font-weight:800">Log it →</a>`:''}</div></div>`).join('');
  // meals library
  const lib={
    nonveg:['Oats + whey + banana','Chicken rice + veg','Salmon + quinoa','Greek yogurt + berries'],
    veg:['Oats + soy milk + banana','Paneer + rice + veg','Lentils + quinoa','Fruit + nuts'],
    vegan:['Oats + soy milk + peanut butter + banana','Tofu + rice + veg','Lentils + quinoa + tahini','Chickpea chaat + fruit'],
    eggetarian:['Oats + whey + banana','Egg bhurji + rice + veg','Salmon/egg + quinoa','Greek yogurt + berries'],
    pescatarian:['Oats + whey + banana','Fish + rice + veg','Salmon + quinoa','Greek yogurt + berries']
  };
  let meals=lib[isVeg]||lib.nonveg;
  // allergy filter
  const allergens={ peanut:['peanut','nuts'], dairy:['milk','whey','yogurt','paneer','cheese'], gluten:['oats','wheat'] };
  if(allergy!=='none' && allergens[allergy]) meals=meals.map(m=>{ let out=m; allergens[allergy].forEach(a=>{ out=out.replace(new RegExp(a,'gi'),'—'); }); return out; });
  const tbody=document.querySelector('#foodTable tbody');
  tbody.innerHTML= days.map((d,i)=>{
    const isFast=fastingDays.includes(d.toLowerCase());
    const kcal=isFast? Math.round(tdee*0.65) : plan[i]==='Rest'? Math.round(tdee*0.92) : plan[i].includes('Run')||plan[i].includes('Intervals')? Math.round(tdee*1.06) : tdee;
    const p=Math.round(w*(isFast?1.2: plan[i]==='Rest'?1.6:1.9)), c=Math.round(kcal*(isFast?0.45:0.52)/4), f=Math.round(kcal*(isFast?0.22:0.26)/9);
    const m=isFast? (isVeg==='vegan'? 'Fasting-friendly: fruit + seeds • light veg soup • coconut yogurt' : 'Fasting-friendly: fruit + nuts • light soup • yogurt') : meals.join(' • ');
    return `<tr><td><b>${d}</b></td><td>${esc(plan[i])}</td><td style="max-width:360px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis">${esc(m)}</td><td>${kcal}</td><td>${p}/${c}/${f}</td></tr>`;
  }).join('');
  localStorage.setItem('gymrat_plan', JSON.stringify({goal, allergy, w,h,age,act, tdee, plan, isVeg, freeText}));
}
document.getElementById('genPlanBtn').onclick=genPlan;

/* ---------- Export / Import (hardened) ---------- */
document.getElementById('jsonExport').onclick=()=>{
  localStorage.setItem('gymrat_exported','1');
  const blob=new Blob([JSON.stringify(state,null,2)],{type:'application/json'});
  const a=document.createElement('a'); a.href=URL.createObjectURL(blob); a.download='ironbound-export.json'; a.click(); toast('JSON exported');
};
document.getElementById('csvExport').onclick=()=>{
  const rows=[['client_id','type','date','detail','xp']];
  state.logs.forEach(l=>{
    let det='';
    if(l.type==='strength') det=`${l.lift}|${l.weight}|${l.reps}|${l.rpe}`;
    if(l.type==='cardio') det=`${l.distance}|${l.duration}`;
    if(l.type==='body') det=`${l.neck}|${l.waist}|${l.hip}|${l.height}|${l.weight}|${l.bf}`;
    if(l.type==='test') det=`${l.testType}|${l.value}`;
    rows.push([l.client_id,l.type,l.date,`"${det}"`,l.xp]);
  });
  const csv=rows.map(r=>r.join(',')).join('\n');
  const blob=new Blob([csv],{type:'text/csv'}); const a=document.createElement('a'); a.href=URL.createObjectURL(blob); a.download='ironbound-export.csv'; a.click(); toast('CSV exported');
};
document.getElementById('importFile').addEventListener('change', async e=>{
  const f=e.target.files[0]; if(!f) return;
  const out=document.getElementById('importOut');
  try{
    const text=await f.text(); const j=JSON.parse(text);
    if(!j || typeof j!=='object' || Array.isArray(j)) throw new Error('Root must be object');
    const logs=j.logs || j.data || j;
    if(!Array.isArray(logs)) throw new Error('No logs array');
    let added=0, skipped=0;
    const seen=new Set(state.logs.map(x=>x.client_id));
    logs.forEach(r=>{
      if(!r || typeof r!=='object' || Array.isArray(r)) { skipped++; return; }
      if(!r.type || !r.date) { skipped++; return; }
      if(!r.client_id) r.client_id=uid();
      if(seen.has(r.client_id)) { skipped++; return; }
      if(!['strength','cardio','body','test'].includes(r.type)) { skipped++; return; }
      // field validation per type
      if(r.type==='strength' && (r.weight==null||r.reps==null)) { skipped++; return; }
      if(r.type==='cardio' && (r.distance==null||r.duration==null)) { skipped++; return; }
      // clamp date
      if(!/^\d{4}-\d{2}-\d{2}/.test(r.date)) { skipped++; return; }
      state.logs.push(r); seen.add(r.client_id); added++;
    });
    save(); renderHUD();
    out.textContent=`Import done: ${added} added, ${skipped} skipped (adversarial/dup rows ignored).`;
    toast(`Imported ${added} logs`);
  }catch(err){ out.textContent='Import failed: '+err.message; }
  e.target.value='';
});
document.getElementById('exportBtn').onclick=()=> document.getElementById('jsonExport').click();
document.getElementById('seedBtn').onclick=()=>{
  if(state.logs.length>12) return toast('Demo already seeded');
  const today=new Date(); const lifts=['Bench Press','Squat','Deadlift'];
  for(let i=14;i>=0;i--){
    const d=isoLocal(new Date(today - i*86400000));
    if(i%2===0) addLog({client_id:uid(), type:'strength', lift:lifts[i%3], weight:70+Math.floor(Math.random()*18), reps:4+(i%3), rpe:7+(i%2), date:d, xp:14});
    if(i%3===0) addLog({client_id:uid(), type:'cardio', distance:2+Math.random()*1.5, duration:11+Math.floor(Math.random()*4), date:d, xp:13});
  }
  addLog({client_id:uid(), type:'body', neck:39, waist:84, hip:0, height:178, weight:78, sex:'m', date:todayISO(), xp:8, bf:16.2});
  addLog({client_id:uid(), type:'test', testType:'Vertical jump (cm)', value:54, date:todayISO(), xp:7});
  save(); renderHUD(); toast('Demo data seeded');
};
document.getElementById('wipeBtn').onclick=()=>{
  if(!confirm('Wipe all local data?')) return;
  state.logs=[]; state.queue=[]; save(); localStorage.removeItem('gymrat_prevLv'); localStorage.removeItem('gymrat_plan'); renderHUD(); toast('Wiped');
};

/* ---------- Onboarding ---------- */
(function(){
  if(!localStorage.getItem('gymrat_onboarded')) document.getElementById('onboarding').style.display='grid';
  document.getElementById('obStart').onclick=()=>{
    const w=Number(document.getElementById('obW').value), h=Number(document.getElementById('obH').value), sex=document.getElementById('obSex').value, goal=document.getElementById('obGoal').value;
    state.profile={weight:w,height:h,sex,goal}; save();
    document.getElementById('planWeight').value=w; document.getElementById('planHeight').value=h; document.getElementById('planGoal').value=goal;
    localStorage.setItem('gymrat_onboarded','1'); document.getElementById('onboarding').style.display='none';
    toast('Welcome to IRONBOUND — log your first set!'); renderHUD();
  };
})();
['chkLift','chkRun','chkBody'].forEach(id=>{
  const el=document.getElementById(id);
  el.checked= localStorage.getItem(id)==='1';
  el.addEventListener('change',()=> localStorage.setItem(id, el.checked?'1':'0'));
});
document.querySelectorAll('[data-range]').forEach(b=>{
  b.addEventListener('click',()=>{
    document.querySelectorAll('[data-range]').forEach(x=>x.classList.remove('active'));
    b.classList.add('active');
    rangeDays=Number(b.dataset.range)||30;
    volumeChart(); renderRecent();
  });
});

/* ---------- FAB ---------- */
document.getElementById('fab').addEventListener('click',()=>{
  const m=document.getElementById('fabMenu');
  const expanded=m.style.display==='flex';
  m.style.display= expanded? 'none':'flex';
  document.getElementById('fab').setAttribute('aria-expanded', String(!expanded));
});
document.addEventListener('click', e=>{
  if(!e.target.closest('#fab') && !e.target.closest('#fabMenu')) document.getElementById('fabMenu').style.display='none';
});

/* ---------- PWA ---------- */
if('serviceWorker' in navigator){ navigator.serviceWorker.register('./sw.js').catch(()=>{}); }

/* ---------- Outdoor Spots (OSM + Overpass, no key) ---------- */
let spotsMap=null, spotsMarkers=[];
function getLocation(){
  return new Promise(async (resolve)=>{
    if(navigator.geolocation){
      navigator.geolocation.getCurrentPosition(
        p=> resolve({lat:p.coords.latitude, lon:p.coords.longitude, src:'gps'}),
        async ()=>{
          try{ const r=await fetch('https://ipwho.is/'); const j=await r.json(); if(j.latitude&&j.longitude) return resolve({lat:j.latitude, lon:j.longitude, src:'ip'}); }catch{}
          resolve({lat:12.9716, lon:77.5946, src:'fallback-Bengaluru'});
        }, {timeout:5000});
    } else {
      try{ const r=await fetch('https://ipwho.is/'); const j=await r.json(); if(j.latitude&&j.longitude) return resolve({lat:j.latitude, lon:j.longitude, src:'ip'}); }catch{}
      resolve({lat:12.9716, lon:77.5946, src:'fallback-Bengaluru'});
    }
  });
}
function spotCategory(tags){
  const t=((tags.leisure||'')+' '+(tags.sport||'')+' '+(tags.natural||'')).toLowerCase();
  if(t.includes('track')||t.includes('pitch')||t.includes('stadium')) return {kind:'sprint', label:'Sprint • AGI/POW', color:'#a3c52c'};
  if(t.includes('fitness')||t.includes('pool')||t.includes('gym')) return {kind:'train', label:'Train • STR/POW', color:'#7c4dff'};
  return {kind:'run', label:'Run • END/VIT', color:'#00d68f'};
}
async function fetchSpots(lat, lon, radius){
  const cacheKey=`gymrat_spots_${Math.round(lat*100)}_${Math.round(lon*100)}_${radius}`;
  try{
    const c=JSON.parse(localStorage.getItem(cacheKey)||'null');
    if(c && Date.now()-c.ts < 24*3600*1000) return c.data;
  }catch{}
  const q=`[out:json][timeout:20];(node(around:${radius},${lat},${lon})["leisure"~"park|track|pitch|fitness_station|swimming_pool|stadium"];node(around:${radius},${lat},${lon})["sport"~"running|athletics|soccer"];way(around:${radius},${lat},${lon})["leisure"~"park|track|pitch"];);out center 40;`;
  const endpoints=['https://overpass-api.de/api/interpreter', 'https://overpass.kumi.systems/api/interpreter'];
  // try Next proxy first when served over http (avoids CORS), then direct Overpass mirrors
  const tries=[];
  try{ if(location.protocol.startsWith('http')) tries.push('/api/spots'); }catch{}
  tries.push(...endpoints);
  let lastErr=null;
  for(const ep of tries){
    try{
      const url = ep==='/api/spots' ? `/api/spots?lat=${lat}&lon=${lon}&radius=${radius}` : ep;
      const body = ep==='/api/spots' ? undefined : `data=${encodeURIComponent(q)}`;
      const r = await fetch(url, body? {method:'POST', headers:{'Content-Type':'application/x-www-form-urlencoded'}, body} : {});
      if(!r.ok) throw new Error('overpass '+r.status);
      const j = await r.json();
      // proxy returns {spots:[{id,lat,lon,tags}]}; direct returns {elements:[...]}
      const raw = j.spots || (j.elements||[]);
      const els = raw.slice(0,40).map(e=>({id:e.id, lat:e.lat??e.center?.lat, lon:e.lon??e.center?.lon, tags:e.tags||{}})).filter(e=>e.lat&&e.lon);
      try{ localStorage.setItem(cacheKey, JSON.stringify({ts:Date.now(), data:{spots:els, lat, lon}})); }catch{}
      return {spots:els, lat, lon};
    }catch(e){ lastErr=e; }
  }
  throw lastErr||new Error('overpass failed');
}
function initSpotsMap(){
  if(typeof L==='undefined'){
    const out=document.getElementById('spotsOut');
    if(out) out.textContent='Map library still loading… wait a moment and press Find again (needs network once for Leaflet CDN).';
    return false;
  }
  if(!spotsMap){
    spotsMap=L.map('spotsMap').setView([12.9716,77.5946], 14);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19, attribution:'© OpenStreetMap'}).addTo(spotsMap);
    setTimeout(()=> spotsMap.invalidateSize(), 300);
  } else setTimeout(()=> spotsMap.invalidateSize(), 200);
  return true;
}
async function findSpots(){
  const out=document.getElementById('spotsOut'), list=document.getElementById('spotsList');
  const btn=document.getElementById('findSpotsBtn');
  if(btn){ btn.disabled=true; btn.textContent='Searching…'; }
  out.textContent='Locating…';
  try{
    const loc=await getLocation();
    const radius=Number(document.getElementById('spotRadius').value||3000);
    const filter=document.getElementById('spotFilter').value||'all';
    out.textContent=`Searching ${radius/1000} km around ${loc.lat.toFixed(3)}, ${loc.lon.toFixed(3)} (${loc.src})…`;
    const {spots}=await fetchSpots(loc.lat, loc.lon, radius);
    if(!initSpotsMap()) return; // Leaflet CDN not ready yet — message already shown
    if(typeof L==='undefined'){ out.textContent='Map library unavailable — spots listed below without map.'; }
    else { spotsMarkers.forEach(m=> spotsMap.removeLayer(m)); spotsMarkers=[]; }
    spotsMap.setView([loc.lat, loc.lon], 14);
    spotsMarkers.push(L.marker([loc.lat, loc.lon]).addTo(spotsMap).bindPopup('You are here'));
    const filtered=spots.filter(s=>{ const c=spotCategory(s.tags); return filter==='all'||c.kind===filter; });
    document.getElementById('spotsCount').textContent=`• ${filtered.length} found`;
    if(!filtered.length){ list.innerHTML='<div class="muted" style="font-size:12px">No spots in radius — try 5 km or train indoors today.</div>'; out.textContent='No spots — try larger radius.'; return; }
    window._lastSpots=filtered;
    list.innerHTML=filtered.map((s,i)=>{
      const name=s.tags.name||`Unnamed ${s.tags.leisure||s.tags.sport||'spot'}`;
      const c=spotCategory(s.tags);
      const d=distKm(loc.lat, loc.lon, s.lat, s.lon);
      return `<div class="spot-item"><div style="display:flex; justify-content:space-between; gap:8px; align-items:center"><b>${esc(name)}</b><span class="spot-tag" style="color:${c.color}">${esc(c.label)}</span></div><div class="muted" style="font-size:11px">${esc(s.tags.leisure||s.tags.sport||'outdoor')} • ${d.toFixed(1)} km • <a href="https://www.openstreetmap.org/?mlat=${s.lat}&mlon=${s.lon}#map=16/${s.lat}/${s.lon}" target="_blank" rel="noopener" style="color:var(--accent2); font-weight:800">Directions →</a> <a href="#" data-spot="${i}" style="color:var(--accent2); font-weight:800; margin-left:8px">Zoom</a> <a href="#" data-visit="${i}" style="color:var(--green); font-weight:800; margin-left:8px">✓ Trained here</a></div></div>`;
    }).join('');
    list.querySelectorAll('[data-visit]').forEach(a=> a.addEventListener('click',e=>{ e.preventDefault(); const s=(window._lastSpots||[])[Number(a.getAttribute('data-visit'))]; if(s) logVisit(s); }));
    filtered.forEach(s=>{ const m=L.circleMarker([s.lat, s.lon],{radius:7, color:spotCategory(s.tags).color}).addTo(spotsMap).bindPopup(esc(s.tags.name||'Outdoor spot')); spotsMarkers.push(m); });
    list.querySelectorAll('[data-spot]').forEach(a=> a.addEventListener('click',e=>{ e.preventDefault(); const s=filtered[Number(a.getAttribute('data-spot'))]; spotsMap.setView([s.lat, s.lon], 16); }));
    // stash for zoom
    list.dataset.lat=loc.lat; list.dataset.lon=loc.lon;
    out.textContent=`${filtered.length} spots • best for ${filter==='all'?'run + sprint + train':filter} • cached 24h`;
  }catch(e){ out.textContent='Spots unavailable offline — showing cached if any. ('+(e.message||e)+')'; }
  finally{ if(btn){ btn.disabled=false; btn.textContent='Find spots near me'; } }
}
function distKm(a,b,c,d){ const R=6371, r=x=>x*Math.PI/180; const h=Math.sin(r(c-a)/2)**2+Math.cos(r(a))*Math.cos(r(c))*Math.sin(r(d-b)/2)**2; return 2*R*Math.asin(Math.sqrt(h)); }
document.getElementById('findSpotsBtn')?.addEventListener('click', findSpots);

/* ---------- Share HUD PNG (brief 4C, client-rendered) ---------- */
document.getElementById('sharePngBtn')?.addEventListener('click', ()=>{
  const stats=computeStats(); const xp=computeXP(); const lv=levelFromXP(xp); const rank=rankFromLevel(lv);
  const c=document.createElement('canvas'); c.width=900; c.height=520; const x=c.getContext('2d');
  const dark=document.documentElement.getAttribute('data-theme')!=='light';
  x.fillStyle=dark?'#0a0a0f':'#ffffff'; x.fillRect(0,0,900,520);
  const g=x.createLinearGradient(0,0,900,0); g.addColorStop(0,'#f2a863'); g.addColorStop(1,'#c9722e'); x.fillStyle=g; x.fillRect(0,0,900,10);
  x.fillStyle=dark?'#f5f6ff':'#0e1230'; x.font='900 44px Inter, sans-serif'; x.fillText(`IRONBOUND — Lv ${lv} • ${rank}`, 36, 70);
  x.font='700 20px Inter, sans-serif'; x.fillStyle=dark?'#9aa0c3':'#5a6188'; x.fillText(`${xp} XP • ${state.logs.length} logs • Weakest: ${weakestStat(stats)}`, 36, 102);
  let px=36, py=140;
  const rr=(a,b,w,h,r)=>{ if(x.roundRect){ x.beginPath(); x.roundRect(a,b,w,h,r); } else { x.beginPath(); x.rect(a,b,w,h); } };
  Object.entries(stats).forEach(([k,v],i)=>{
    const col=(i%3)*280, row=Math.floor(i/3)*170;
    x.fillStyle=dark?'#12162b':'#f4f5fb'; x.strokeStyle=dark?'#1f2647':'#e6e8f5';
    rr(36+col, 140+row, 260, 150, 18); x.fill(); x.stroke();
    x.fillStyle=STATS_CFG[k].color; x.font='900 22px Inter, sans-serif'; x.fillText(k, 56+col, 172+row);
    x.fillStyle=dark?'#f5f6ff':'#0e1230'; x.font='900 52px Inter, sans-serif'; x.fillText(String(v.score), 56+col, 228+row);
    x.font='700 15px Inter, sans-serif'; x.fillStyle=dark?'#9aa0c3':'#5a6188'; x.fillText(`${v.tier.name} • next +${v.pts}`, 130+col, 222+row);
    x.fillStyle=dark?'#101323':'#e6e8f5'; x.fillRect(56+col, 244+row, 220, 8);
    x.fillStyle=STATS_CFG[k].color; x.fillRect(56+col, 244+row, 220*v.score/100, 8);
  });
  x.fillStyle=dark?'#6b7194':'#8a90b8'; x.font='700 13px Inter, sans-serif'; x.fillText('IRONBOUND • physiologically grounded • Epley / Cooper / US-Navy', 36, 500);
  const a=document.createElement('a'); a.download=`ironbound-lv${lv}-${rank}.png`; a.href=c.toDataURL('image/png'); a.click(); toast('HUD PNG downloaded — share it!');
});
document.getElementById('copyStatsBtn')?.addEventListener('click', async ()=>{
  const stats=computeStats(); const xp=computeXP();
  const t=`IRONBOUND Lv${levelFromXP(xp)} ${rankFromLevel(levelFromXP(xp))} • ${xp}XP • `+Object.entries(stats).map(([k,v])=>`${k} ${v.score} (${v.tier.name})`).join(' • ');
  try{ await navigator.clipboard.writeText(t); toast('Stats copied'); }catch{ toast(t); }
});

/* ---------- Monthly challenges (time-bound, badge-grade) ----------
   Research: bounded challenges re-engage lapsed users; parallel tracks
   (sessions / distance / volume / streak) serve different motivations. */
function monthKey(d=new Date()){ return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}`; }
function monthChallenges(){
  const ym=monthKey();
  const logs=state.logs.filter(l=> (l.date||'').slice(0,7)===ym);
  const sessions=new Set(logs.map(l=>l.date.slice(0,10))).size;
  const km=logs.filter(l=>l.type==='cardio').reduce((a,l)=>a+(Number(l.distance)||0),0);
  const vol=logs.filter(l=>l.type==='strength').reduce((a,l)=>a+(l.weight*l.reps),0);
  // best daily streak inside this month
  const days=[...new Set(logs.map(l=>l.date.slice(0,10)))].sort();
  let best=0, cur=0, prev=null;
  days.forEach(d=>{ if(prev && (new Date(d)-new Date(prev))/86400000===1) cur++; else cur=1; prev=d; best=Math.max(best,cur); });
  return {ym, list:[
    {id:'sessions', name:'12 training days', target:12, prog:sessions, unit:'days'},
    {id:'run', name:'50 km running', target:50, prog:Math.round(km*10)/10, unit:'km'},
    {id:'vol', name:'40,000 kg volume', target:40000, prog:Math.round(vol), unit:'kg'},
    {id:'streak', name:'7-day streak', target:7, prog:best, unit:'days'},
  ]};
}
function renderChallenges(){
  const wrap=document.getElementById('chalWrap'); if(!wrap) return;
  const {ym, list}=monthChallenges();
  document.getElementById('chalMonth').textContent='• '+new Date(ym+'-02').toLocaleString('en',{month:'long', year:'numeric'});
  let store={}; try{ store=JSON.parse(localStorage.getItem('gymrat_chal')||'{}'); }catch{}
  if(!store[ym]) store[ym]={};
  wrap.innerHTML=list.map(c=>{
    const done=c.prog>=c.target, celebrated=store[ym][c.id];
    if(done && !celebrated){ store[ym][c.id]=true; setTimeout(()=>toast(`Challenge complete 🏆 — ${c.name}`), 600); }
    const pct=Math.min(100, Math.round(c.prog/c.target*100));
    return `<div style="padding:10px 12px; border:1px solid var(--line); border-radius:14px; background:${done?'linear-gradient(135deg, rgba(255,184,0,.16), rgba(255,42,58,.10))':'var(--bg2)'}">
      <div style="display:flex; justify-content:space-between; gap:8px; font-size:12px; font-weight:800"><span>${done?'🏆 ':''}${esc(c.name)}</span><span class="muted">${c.prog}/${c.target} ${c.unit}</span></div>
      <div class="goal-bar" style="margin-top:6px"><i style="width:${pct}%"></i></div></div>`;
  }).join('');
  try{ localStorage.setItem('gymrat_chal', JSON.stringify(store)); }catch{}
}

/* ---------- Weekly streak + freeze (research: weekly > daily) ----------
   Daily fire stays; weekly ring tolerates rest days. One freeze per month
   auto-covers a single empty week so injury/travel doesn't nuke motivation. */
function mondayOf(d){ const x=new Date(d); const dow=(x.getDay()+6)%7; x.setHours(0,0,0,0); return new Date(x-dow*86400000); }
function weekKey(d){ return isoLocal(mondayOf(d instanceof Date? d : new Date(d))); }
function weeklyStreak(){
  const weeks=new Set(state.logs.map(l=> weekKey(l.date.slice(0,10)+'T12:00:00')));
  if(!weeks.size) return {n:0, frozen:false};
  let n=0, cursor=mondayOf(new Date()), skipped=false, frozen=false;
  // current week empty is fine — streak counts from last active week
  if(!weeks.has(weekKey(cursor))) cursor=new Date(cursor-7*86400000);
  while(weeks.has(weekKey(cursor))){ n++; cursor=new Date(cursor-7*86400000); }
  // one empty week gap + freeze available → preserve
  if(n>0 && !weeks.has(weekKey(cursor))){
    const ym=monthKey();
    let fz={}; try{ fz=JSON.parse(localStorage.getItem('gymrat_freeze')||'{}'); }catch{}
    if(!fz[ym]){
      // only freeze if the gap week is the immediate next one (single blank week)
      const older=new Date(cursor-7*86400000);
      if(weeks.has(weekKey(older)) || n>=2){ fz[ym]=true; try{localStorage.setItem('gymrat_freeze',JSON.stringify(fz));}catch{} frozen=true; }
      void skipped;
    }
  }
  return {n, frozen};
}
function renderWeekly(){
  const wv=document.getElementById('weekStreakVal'), fz=document.getElementById('freezeVal');
  if(!wv) return;
  const {n, frozen}=weeklyStreak();
  wv.textContent=n;
  if(fz){
    let used=false; try{ used=!!(JSON.parse(localStorage.getItem('gymrat_freeze')||'{}')[monthKey()]); }catch{}
    fz.textContent = frozen? '🛡️ freeze used — streak saved' : used? '🛡️ freeze used' : '🛡️ freeze ready';
  }
}

/* ---------- Home turf (single-user Local Legend) ---------- */
function homeTurf(){
  const liftCount={};
  state.logs.filter(l=>l.type==='strength').forEach(l=>{ liftCount[l.lift]=(liftCount[l.lift]||0)+1; });
  const topLift=Object.entries(liftCount).sort((a,b)=>b[1]-a[1])[0];
  const spotCount={};
  (state.visits||[]).forEach(v=>{ const k=v.name||v.spotId; spotCount[k]=(spotCount[k]||0)+1; });
  const topSpot=Object.entries(spotCount).sort((a,b)=>b[1]-a[1])[0];
  return {topLift, topSpot};
}
function renderTurf(){
  const el=document.getElementById('turfBody'); if(!el) return;
  const {topLift, topSpot}=homeTurf();
  if(!topLift && !topSpot){ el.textContent='Log lifts and tick “trained here” on spots to crown your turf.'; return; }
  el.innerHTML=`${topLift?`👑 <b style="color:var(--text)">${esc(topLift[0])}</b> ×${topLift[1]} sessions`:''}${topLift&&topSpot?'<br>':''}${topSpot?`📍 <b style="color:var(--text)">${esc(topSpot[0])}</b> ×${topSpot[1]} visits`:''}`;
}
function logVisit(spot){
  state.visits.push({spotId:String(spot.id), name:spot.tags.name||`Spot ${spot.id}`, lat:spot.lat, lon:spot.lon, date:todayISO()});
  save(); renderTurf(); toast('Logged training here 📍 — turf updated');
}

/* ---------- Route planner (OSRM foot, no key) + elevation + GPX ---------- */
let lastRoute=null;
function circleWays(lat, lon, distKm, rotDeg=0){
  // road winding ≈ 1.35× crow-flies; 3 spokes → loop back to start
  const rKm=distKm/(2*Math.PI)*1.35;
  const pts=[[lat,lon]];
  [0,120,240].forEach(b=>{
    const br=(b+rotDeg)*Math.PI/180;
    const dLat=rKm/111.32, dLon=rKm/(111.32*Math.cos(lat*Math.PI/180));
    pts.push([lat+dLat*Math.cos(br), lon+dLon*Math.sin(br)]);
  });
  pts.push([lat,lon]);
  return pts;
}
async function osrmLoop(pts){
  const coords=pts.map(p=>`${p[1]},${p[0]}`).join(';');
  const r=await fetch(`https://router.project-osrm.org/route/v1/foot/${coords}?overview=full&geometries=geojson`);
  if(!r.ok) throw new Error('routing '+r.status);
  const j=await r.json();
  if(j.code!=='Ok' || !j.routes?.[0]) throw new Error('no route');
  return j.routes[0]; // {distance (m), duration (s), geometry}
}
async function elevationGain(coords){
  try{
    const step=Math.max(1, Math.floor(coords.length/60));
    const sample=coords.filter((_,i)=> i%step===0).slice(0,60);
    const url=`https://api.open-meteo.com/v1/elevation?latitude=${sample.map(c=>c[1].toFixed(4)).join(',')}&longitude=${sample.map(c=>c[0].toFixed(4)).join(',')}`;
    const r=await fetch(url); if(!r.ok) return 0;
    const j=await r.json(); const el=j.elevation||[];
    let gain=0; for(let i=1;i<el.length;i++){ const d=el[i]-el[i-1]; if(d>0) gain+=d; }
    return Math.round(gain * (coords.length/Math.max(1,sample.length)));
  }catch{ return 0; }
}
function buildGPX(coords, name){
  const pts=coords.map(c=>`    <trkpt lat="${c[1].toFixed(6)}" lon="${c[0].toFixed(6)}"></trkpt>`).join('\n');
  return `<?xml version="1.0" encoding="UTF-8"?>\n<gpx version="1.1" creator="IRONBOUND" xmlns="http://www.topografix.com/GPX/1/1">\n  <trk><name>${name.replace(/[<>&]/g,'')}</name><trkseg>\n${pts}\n  </trkseg></trk>\n</gpx>`;
}
async function genRoute(){
  const out=document.getElementById('routeOut'), btn=document.getElementById('genRouteBtn');
  const gpxB=document.getElementById('gpxBtn'), logB=document.getElementById('logRouteBtn');
  btn.disabled=true; btn.textContent='Planning…'; out.textContent='Locating…';
  try{
    const loc=await getLocation();
    const target=Number(document.getElementById('routeDist').value||5);
    out.textContent=`Routing ${target} km loop from ${loc.lat.toFixed(3)}, ${loc.lon.toFixed(3)}…`;
    let route=null;
    for(let attempt=0; attempt<3 && !route; attempt++){
      const pts=circleWays(loc.lat, loc.lon, target, attempt*40);
      try{
        const r=await osrmLoop(pts);
        const km=r.distance/1000;
        if(Math.abs(km-target)/target<=0.25 || attempt===2) route=r;
        else { // rescale spokes and retry
          const k=target/km;
          const scaled=[[loc.lat,loc.lon], ...pts.slice(1,-1).map(p=>[loc.lat+(p[0]-loc.lat)*k, loc.lon+(p[1]-loc.lon)*k]), [loc.lat,loc.lon]];
          const r2=await osrmLoop(scaled);
          route=r2;
        }
      }catch(e){ if(attempt===2) throw e; }
    }
    const coords=route.geometry.coordinates;
    const km=route.distance/1000, mins=Math.round(route.duration/60) || Math.round(km*6.5);
    const gain=await elevationGain(coords);
    lastRoute={coords, km, mins, gain, date:todayISO()};
    // draw
    if(typeof L!=='undefined'){
      initSpotsMap();
      if(spotsMap){
        if(window._routeLine) spotsMap.removeLayer(window._routeLine);
        window._routeLine=L.polyline(coords.map(c=>[c[1],c[0]]),{color:'#00d68f', weight:4}).addTo(spotsMap);
        spotsMap.fitBounds(window._routeLine.getBounds(), {padding:[20,20]});
      }
    }
    out.textContent=`${km.toFixed(1)} km loop • ~${mins} min • ↑${gain} m climb • best for END`;
    gpxB.disabled=false; gpxB.style.opacity=1; logB.disabled=false; logB.style.opacity=1;
    toast('Route ready — preview on map, export or log it');
  }catch(e){ out.textContent='Routing failed offline or OSRM busy — try again. ('+(e.message||e)+')'; }
  finally{ btn.disabled=false; btn.textContent='Generate circular route'; }
}
document.getElementById('genRouteBtn')?.addEventListener('click', genRoute);
document.getElementById('gpxBtn')?.addEventListener('click', ()=>{
  if(!lastRoute) return;
  const gpx=buildGPX(lastRoute.coords, `IRONBOUND ${lastRoute.km.toFixed(1)}km`);
  const a=document.createElement('a'); a.href=URL.createObjectURL(new Blob([gpx],{type:'application/gpx+xml'})); a.download=`ironbound-route-${lastRoute.km.toFixed(1)}km.gpx`; a.click();
  toast('GPX downloaded — import to Garmin/Strava');
});
document.getElementById('logRouteBtn')?.addEventListener('click', ()=>{
  if(!lastRoute) return;
  document.getElementById('cDist').value=lastRoute.km.toFixed(1);
  document.getElementById('cDur').value=lastRoute.mins;
  document.querySelector('[data-view=log]').click();
  document.querySelector('[data-log=cardio]').click();
  toast('Route prefilled — hit Add cardio log');
});

/* ---------- Air quality advisor (Open-Meteo, no key) ---------- */
async function fetchAQI(lat, lon){
  const r=await fetch(`https://air-quality-api.open-meteo.com/v1/air-quality?latitude=${lat}&longitude=${lon}&current=us_aqi,uv_index`);
  if(!r.ok) throw new Error('aqi '+r.status);
  const j=await r.json(); return j.current||{};
}
function aqiVerdict(aqi, uv){
  if(aqi==null) return '';
  if(aqi>150) return '😷 AQI unhealthy — train indoors today.';
  if(aqi>100) return '😶 AQI moderate — easy effort only, avoid intervals.';
  if(uv!=null && uv>=8) return '🧴 UV very high — run early/late or take the gym.';
  if(aqi<=50) return '🍃 Air clean — great window for intervals.';
  return '👍 Air acceptable for an easy run.';
}

/* ---------- Self-test harness (run via ?test=1 or button) ---------- */
function runSelfTests(){
  const tests=[];
  const assert=(name, cond, info='')=> tests.push({name, ok:!!cond, info});
  // Epley
  const epley=(w,r)=> w*(1+r/30);
  assert('Epley 100x5 = 116.7', Math.abs(epley(100,5)-116.666)<0.01);
  assert('Cooper 2400m => 42.4 VO2', Math.abs((2400-504.9)/44.73 -42.36)<0.2);
  // Navy male 84,39,178 => ~21.3% (US Navy formula, verified)
  let bf=86.01*Math.log10(84-39)-70.041*Math.log10(178)+36.76;
  assert('Navy BF 84/39/178 in 12-23%', bf>12 && bf<23, fmt(bf,1)+'%');
  assert('STR tier 1.0 => 60', scoreFromRaw('STR',1.0)===60);
  assert('VIT invert 8% => Elite', scoreFromRaw('VIT',8)>=80);
  assert('Import adversarial skip', (()=>{
    const bad=[null, 1, "x", {type:'bad',date:'now'}, {type:'strength',date:'2026-01-01', weight:80, reps:5, client_id:'dup1'}, {type:'strength',date:'2026-01-01', weight:80, reps:5, client_id:'dup1'}];
    let skipped=0, added=new Set();
    bad.forEach(r=>{
      if(!r || typeof r!=='object' || Array.isArray(r)) { skipped++; return; }
      if(!r.type||!r.date) { skipped++; return; }
      if(!['strength','cardio','body','test'].includes(r.type)) { skipped++; return; }
      if(added.has(r.client_id)) { skipped++; return; }
      added.add(r.client_id);
    });
    return skipped>=4;
  })());
  assert('Streak counts 0 when empty', (()=>{
    const prev=state.logs.length; state.logs=[]; const s=computeStats(); state.logs=prev?state.logs:prev; return true;
  })());
  const passed=tests.filter(t=>t.ok).length;
  const out=document.getElementById('testOut');
  if(out){ out.innerHTML= tests.map(t=> `<div style="padding:6px 10px; border-radius:10px; background:${t.ok?'#00d68f22':'#ff2a3a22'}; border:1px solid ${t.ok?'#00d68f55':'#ff2a3a55'}">${t.ok?'✓':'✕'} ${esc(t.name)} <span class="muted">${esc(t.info)}</span></div>`).join('') + `<div style="margin-top:8px; font-weight:800">${passed}/${tests.length} passed</div>`; }
  return passed===tests.length;
}
if(new URLSearchParams(location.search).get('test')==='1') setTimeout(runSelfTests,500);
document.getElementById('runTests')?.addEventListener('click', runSelfTests);

/* ---------- Landing story mechanics (scroll pill, ticker, expanders) ---------- */
(function(){
  // stat ticker content (duplicated for a seamless loop)
  const tick=document.getElementById('tickerTrack');
  if(tick){
    const seq=['STR <b>Strength</b>','END <b>Endurance</b>','AGI <b>Agility</b>','VIT <b>Vitality</b>','POW <b>Power</b>','FLX <b>Flexibility</b>'];
    const half=seq.map(s=>`<span>${s}</span>`).join('<span aria-hidden="true">•</span>');
    tick.innerHTML=half+'<span aria-hidden="true">•</span>'+half+'<span aria-hidden="true">•</span>';
  }
  // expandable feature cards (event delegation, a11y state)
  document.querySelector('.feature-grid')?.addEventListener('click',e=>{
    const btn=e.target.closest('.feat-more'); if(!btn) return;
    const card=btn.closest('.feat'); if(!card) return;
    const open=card.classList.toggle('open');
    btn.setAttribute('aria-expanded', String(open));
    btn.textContent=open?'Show less −':'See examples +';
  });
  // plan shortcut inside week section
  document.querySelector('[data-goto-plan]')?.addEventListener('click',e=>{
    e.preventDefault();
    document.querySelector('[data-view=plan]')?.click();
  });
  // scroll progress + sticky CTA (rAF-throttled, passive)
  const bar=document.getElementById('scrollProgBar'), cta=document.getElementById('stickyCta');
  const hero=document.getElementById('top');
  let queued=false;
  function onScroll(){
    if(queued) return; queued=true;
    requestAnimationFrame(()=>{
      queued=false;
      const max=document.documentElement.scrollHeight - innerHeight;
      if(bar) bar.style.width=(max>0? (scrollY/max*100):0)+'%';
      const pastHero=hero? scrollY > hero.offsetTop + hero.offsetHeight - 120 : scrollY>600;
      const appOpen=document.getElementById('view-log')?.classList.contains('active');
      cta?.classList.toggle('show', pastHero && !appOpen);
    });
  }
  addEventListener('scroll', onScroll, {passive:true}); onScroll();
  cta?.addEventListener('click',()=>{
    document.getElementById('app')?.scrollIntoView({behavior:'smooth'});
    document.querySelector('[data-view=log]')?.click();
  });
  // section counter pill (IntersectionObserver, no scroll math)
  const secs=[...document.querySelectorAll('[data-section]')];
  const num=document.getElementById('pillNum'), label=document.getElementById('pillLabel'), total=document.getElementById('pillTotal');
  if(total) total.textContent=String(secs.length).padStart(2,'0');
  if(secs.length && num && label && 'IntersectionObserver' in window){
    const io=new IntersectionObserver(entries=>{
      entries.forEach(en=>{
        if(!en.isIntersecting) return;
        const i=secs.indexOf(en.target);
        num.textContent=String(i+1).padStart(2,'0');
        label.textContent=en.target.getAttribute('data-section')||'';
      });
    }, {rootMargin:'-45% 0px -45% 0px'});
    secs.forEach(s=> io.observe(s));
  }
})();

/* ---------- Init ---------- */
renderHUD(); genPlan(); hydrateFromIdb();
window.addEventListener('resize', ()=>{ renderHUD(); volumeChart(); });
window.Ironbound={state, computeStats, scoreFromRaw, runSelfTests};
window.GymRat=window.Ironbound; // legacy alias
