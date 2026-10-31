"""Estilo do portal: só variáveis da marca (`var(--brand)`); sem fontes externas nem JavaScript."""

CSS = """
*{box-sizing:border-box}html,body{height:100%}
body{margin:0;background:var(--bg);color:var(--text);font:15.5px/1.55 var(--sans);-webkit-font-smoothing:antialiased}
a{color:var(--accent);text-underline-offset:3px}a:focus-visible,button:focus-visible,summary:focus-visible,input:focus-visible,
select:focus-visible{outline:3px solid var(--text);outline-offset:2px;border-radius:4px}
h1,h2,h3{margin:0;text-wrap:balance;font-family:var(--title)}
h1{font-size:clamp(30px,3vw,42px);font-weight:500;letter-spacing:-.025em;line-height:1.1}
h2{font-size:18px;font-weight:550;letter-spacing:-.01em}
.num,td.n,.big{font-variant-numeric:tabular-nums}
.app{display:grid;grid-template-columns:256px minmax(0,1fr);min-height:100%}
.side{padding:28px 18px 20px 24px;display:flex;flex-direction:column;gap:22px;position:sticky;top:0;height:100vh;overflow-y:auto}
.brand{display:flex;align-items:center;gap:10px;font:600 24px/1 var(--title);letter-spacing:-.02em;padding:0 10px;color:var(--text);
text-decoration:none}.brand i{width:18px;height:18px;border-radius:50% 50% 50% 4px;background:var(--brand);display:inline-block}
.group{display:flex;flex-direction:column;gap:2px}.group span{font-size:13.5px;color:var(--muted);padding:0 10px 4px}
.group a{color:var(--text);text-decoration:none;padding:6px 10px;border-radius:10px}
.group a:hover{background:var(--surface)}.group a.on{background:var(--brand);color:var(--on-brand);font-weight:600}
.me{margin-top:auto;padding:14px 10px 0;display:flex;flex-direction:column;gap:8px;font-size:14px}.me small{color:var(--muted)}
.btn{display:inline-flex;align-items:center;justify-content:center;gap:6px;border:1px solid var(--line);
background:transparent;color:var(--text);border-radius:999px;padding:7px 16px;font:inherit;font-size:14px;
text-decoration:none;cursor:pointer;width:fit-content}.btn:hover{border-color:var(--text)}
.btn.primary{background:var(--brand);border-color:var(--brand);color:var(--on-brand);font-weight:600}
.btn.primary:hover{border-color:var(--on-brand)}
main{padding:32px 44px 64px 20px;display:flex;flex-direction:column;gap:24px;max-width:1360px;width:100%}
.top{display:flex;flex-wrap:wrap;align-items:flex-end;justify-content:space-between;gap:14px;margin-bottom:4px}
.top p{margin:8px 0 0;color:var(--muted);font-size:15px}
.chips{display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.chip{border:1px solid var(--line);background:transparent;color:var(--text);border-radius:999px;padding:5px 14px;
font-size:14px;text-decoration:none;white-space:nowrap}.chip.on{border-color:var(--text);background:var(--text);color:var(--bg);
font-weight:600}a.chip:hover{border-color:var(--text)}
.squadbar{display:flex;flex-wrap:wrap;gap:8px 12px;align-items:center;margin:0 0 20px;font-size:14px}
.picks{display:flex;flex-wrap:wrap;gap:6px;max-height:84px;overflow-y:auto;padding:2px}
.pick{position:relative;cursor:pointer}.pick input{position:absolute;opacity:0;width:1px;height:1px}
.pick span{display:block;border:1px solid var(--line);border-radius:999px;padding:5px 14px;white-space:nowrap}
.pick:hover span{border-color:var(--text)}
.pick input:checked+span{border-color:var(--text);background:var(--text);color:var(--bg);font-weight:600}
.pick input:focus-visible+span{outline:2px solid var(--accent);outline-offset:2px}
details.more{position:relative}details.more>summary{list-style:none;cursor:pointer;display:inline-block}
details.more[open]{flex-basis:100%}
details.more>summary::-webkit-details-marker{display:none}
.filters{display:flex;flex-wrap:wrap;gap:12px;align-items:flex-end;margin-top:12px;padding:18px 20px;
background:var(--surface);border-radius:18px}
.filters label{display:flex;flex-direction:column;gap:4px;font-size:13px;
color:var(--muted)}input,select,textarea{font:inherit;font-size:14px;padding:0 12px;border:1px solid var(--line);
border-radius:10px;background:var(--surface-2);color:var(--text);box-sizing:border-box}button{font:inherit}
input,select{height:38px;min-width:120px;line-height:normal}select{padding-right:6px}textarea{padding:8px 12px}
.filters .btn{height:38px}
.questions{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:16px}
.q{background:var(--surface);border-radius:22px;padding:22px 24px;display:flex;flex-direction:column;gap:10px}
.q .ask{font-size:15px;color:var(--muted)}
.q .big{font:500 clamp(32px,3vw,44px)/1.05 var(--title);color:var(--text);text-decoration:none;letter-spacing:-.03em}
.q a.big:hover{text-decoration:underline;text-decoration-thickness:2px}.q .ctx{font-size:14.5px;color:var(--muted)}
.questions .q:first-child{background:var(--brand)}
.questions .q:first-child,.questions .q:first-child .ask,.questions .q:first-child .ctx,.questions .q:first-child .big{color:var(--on-brand)}
.questions .q:first-child .pill{background:var(--on-brand);color:#fff}
.pill{display:inline-flex;align-items:center;border-radius:999px;padding:3px 12px;font-size:13px;font-weight:600;
width:fit-content}.pill.good{background:var(--good-soft);color:var(--good)}.pill.warn{background:var(--warn-soft);
color:var(--warn)}.pill.bad{background:var(--bad-soft);color:var(--bad)}.pill.neutral{background:var(--surface-2);
color:var(--muted)}.bar{height:8px;border-radius:999px;background:var(--surface-2);overflow:hidden}
.bar>span{display:block;height:100%;background:var(--brand);border-radius:999px}
.cols{display:grid;grid-template-columns:minmax(0,1.5fr) minmax(0,1fr);gap:16px;align-items:start}
.card{background:var(--surface);border-radius:22px;padding:22px 24px;display:flex;
flex-direction:column;gap:14px;min-width:0}.card-head{display:flex;justify-content:space-between;align-items:baseline;
gap:8px;flex-wrap:wrap}.card-head small{color:var(--muted);font-size:14px}
.att{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:10px}
.att li{display:grid;grid-template-columns:4px minmax(0,1fr) auto;gap:14px;align-items:center;padding:14px 16px;
border-radius:14px;background:var(--surface-2)}.att .sev{align-self:stretch;border-radius:4px;background:var(--brand)}
.att .sev.warn{background:var(--warn)}.att .sev.bad{background:var(--bad)}.att b{font-weight:600}
.att small{display:block;color:var(--muted);font-size:14px}.att code{display:block;margin-top:6px;
font-family:var(--mono);font-size:13px;color:var(--accent)}
.charts{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;align-items:start}.charts>:only-child{grid-column:1/-1}.charts>:only-child svg.chart{max-height:250px}
.wrap{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:14.5px}
th{text-align:left;color:var(--muted);font-weight:500;font-size:14px;
padding:8px 14px 8px 0;border-bottom:1px solid var(--line);white-space:nowrap}
td{padding:11px 14px 11px 0;border-bottom:1px solid color-mix(in srgb,var(--line) 55%,transparent);white-space:nowrap}tr:last-child td{border-bottom:0}
th.n,td.n{text-align:right}th a.sort{color:inherit;text-decoration:none}th a.sort:hover{color:var(--text)}
code.id{font-family:var(--mono);font-size:13px;color:var(--muted)}
.empty{border-radius:14px;padding:14px 16px;color:var(--muted);font-size:14.5px;background:var(--surface-2)}
.empty b{color:var(--text);font-weight:600}
.ind{box-shadow:inset 3px 0 0 var(--warn)}.tag{font-size:13px;padding:1px 10px;border-radius:99px;
background:var(--warn-soft);color:var(--warn);font-weight:600;margin-left:6px}
.kgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:12px}
.k{background:var(--surface);border-radius:14px;padding:12px 14px;display:flex;
flex-direction:column;gap:2px}.k small{color:var(--muted);font-size:13.5px}.k div{font:500 22px/1.2 var(--title);
font-variant-numeric:tabular-nums;letter-spacing:-.01em}.k a{color:inherit;text-decoration:none}
.card .k{background:var(--surface-2)}.k a:hover{text-decoration:underline}.k.empty-k div{color:var(--faint);font:400 14px var(--sans)}
details.all>summary{cursor:pointer;color:var(--muted);padding:4px 0}
details.all[open]>summary{margin-bottom:10px}
.note{color:var(--muted);font-size:14px;margin:0}
svg.chart{width:100%;height:auto;display:block}svg.chart text{fill:var(--muted);font:14px var(--sans)}
svg.chart text.v{fill:var(--text);font-weight:600}svg.chart line{stroke:color-mix(in srgb,var(--line) 60%,transparent)}svg.chart rect{fill:var(--brand)}
svg.chart rect.past{fill:var(--brand);opacity:.45}svg.chart rect.zero{fill:var(--faint);opacity:.6}
.alta{color:var(--bad)}.media{color:var(--warn)}.baixa{color:var(--muted)}
.center{min-height:100%;display:grid;place-items:center;padding:24px 16px;background:var(--brand)}
.login{background:var(--surface-2);border-radius:28px;padding:36px;width:min(440px,100%);
display:flex;flex-direction:column;gap:16px}.login form{display:flex;flex-direction:column;gap:12px}
.login .brand{padding:0}.login label{display:flex;flex-direction:column;gap:6px;font-size:14px;color:var(--muted)}
.err{color:var(--bad);margin:0}
form.inline{display:flex;flex-wrap:wrap;gap:10px;align-items:flex-end}form.inline label{display:flex;
flex-direction:column;gap:4px;font-size:13px;color:var(--muted)}
@media (max-width:1240px){.questions{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media (max-width:860px){.charts{grid-template-columns:1fr}
.app{grid-template-columns:1fr}.side{position:static;height:auto;flex-direction:row;flex-wrap:wrap;gap:12px 20px;padding:18px 16px}
.me{margin-top:0;padding-top:0}main{padding:12px 16px 40px}.cols{grid-template-columns:1fr}}
@media (max-width:560px){.questions{grid-template-columns:1fr}}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
"""
