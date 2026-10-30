"""Estilo do dashboard: só variáveis da marca (`var(--accent)`); sem fontes externas nem JavaScript."""

CSS = """
*{box-sizing:border-box}html,body{height:100%}
body{margin:0;background:var(--bg);color:var(--text);font:14px/1.5 var(--sans);-webkit-font-smoothing:antialiased}
a{color:var(--accent)}a:focus-visible,button:focus-visible,summary:focus-visible,input:focus-visible,
select:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
h1,h2,h3{margin:0;text-wrap:balance}h1{font-size:22px;font-weight:650;letter-spacing:-.01em}
h2{font-size:15px;font-weight:600}.num,td.n,.big{font-family:var(--mono);font-variant-numeric:tabular-nums}
.app{display:grid;grid-template-columns:236px minmax(0,1fr);min-height:100%}
.side{background:var(--surface-2);border-right:1px solid var(--line);padding:20px 12px;display:flex;
flex-direction:column;gap:20px;position:sticky;top:0;height:100vh;overflow-y:auto}
.brand{display:flex;align-items:center;gap:9px;font-weight:700;font-size:17px;padding:0 8px;color:var(--text);
text-decoration:none}.brand i{width:11px;height:11px;border-radius:3px;background:var(--accent);display:inline-block}
.group{display:flex;flex-direction:column;gap:1px}.group span{font-size:11px;font-weight:600;letter-spacing:.08em;
text-transform:uppercase;color:var(--muted);padding:0 8px 4px}
.group a{color:var(--text);text-decoration:none;padding:6px 8px;border-radius:7px}
.group a:hover{background:var(--surface)}.group a.on{background:var(--accent-soft);color:var(--accent);font-weight:600}
.me{margin-top:auto;border-top:1px solid var(--line);padding:14px 8px 0;display:flex;flex-direction:column;gap:8px;
font-size:13px}.me small{color:var(--muted)}
.btn{display:inline-flex;align-items:center;justify-content:center;gap:6px;border:1px solid var(--line);
background:var(--surface);color:var(--text);border-radius:8px;padding:6px 12px;font:inherit;font-size:13px;
text-decoration:none;cursor:pointer;width:fit-content}.btn:hover{border-color:var(--accent)}
.btn.primary{background:var(--accent);border-color:var(--accent);color:var(--on-accent)}
main{padding:24px 28px 56px;display:flex;flex-direction:column;gap:20px;max-width:1240px;width:100%}
.top{display:flex;flex-wrap:wrap;align-items:flex-end;justify-content:space-between;gap:12px}
.top p{margin:4px 0 0;color:var(--muted);font-size:13px}
.chips{display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.chip{border:1px solid var(--line);background:var(--surface);color:var(--text);border-radius:999px;padding:4px 12px;
font-size:13px;text-decoration:none;white-space:nowrap}.chip.on{border-color:var(--accent);color:var(--accent);
background:var(--accent-soft);font-weight:600}a.chip:hover{border-color:var(--accent)}
details.more{position:relative}details.more>summary{list-style:none;cursor:pointer;display:inline-block}
details.more[open]{flex-basis:100%}
details.more>summary::-webkit-details-marker{display:none}
.filters{display:flex;flex-wrap:wrap;gap:10px;align-items:flex-end;margin-top:10px;padding:14px;
background:var(--surface);border:1px solid var(--line);border-radius:12px}
.filters label{display:flex;flex-direction:column;gap:3px;font-size:12px;
color:var(--muted)}input,select,textarea{font:inherit;font-size:13px;padding:0 10px;border:1px solid var(--line);
border-radius:7px;background:var(--surface);color:var(--text);box-sizing:border-box}button{font:inherit}
input,select{height:34px;min-width:120px;line-height:normal}select{padding-right:6px}textarea{padding:8px 10px}
.filters .btn{height:34px}
.questions{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px}
.q{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:16px 18px;display:flex;
flex-direction:column;gap:8px}.q .ask{font-size:13px;color:var(--muted);font-weight:500}
.q .big{font-family:var(--sans);font-size:30px;font-weight:650;line-height:1.1;color:var(--text);
text-decoration:none;letter-spacing:-.01em}
.q a.big:hover{color:var(--accent)}.q .ctx{font-size:13px;color:var(--muted)}
.pill{display:inline-flex;align-items:center;border-radius:8px;padding:2px 10px;font-size:12px;font-weight:600;
width:fit-content}.pill.good{background:var(--good-soft);color:var(--good)}.pill.warn{background:var(--warn-soft);
color:var(--warn)}.pill.bad{background:var(--bad-soft);color:var(--bad)}.pill.neutral{background:var(--surface-2);
color:var(--muted)}.bar{height:8px;border-radius:999px;background:var(--surface-2);overflow:hidden}
.bar>span{display:block;height:100%;background:var(--accent);border-radius:999px}
.cols{display:grid;grid-template-columns:minmax(0,1.5fr) minmax(0,1fr);gap:14px;align-items:start}
.card{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:16px 18px;display:flex;
flex-direction:column;gap:12px;min-width:0}.card-head{display:flex;justify-content:space-between;align-items:baseline;
gap:8px;flex-wrap:wrap}.card-head small{color:var(--muted);font-size:12.5px}
.att{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:8px}
.att li{display:grid;grid-template-columns:4px minmax(0,1fr) auto;gap:12px;align-items:center;padding:10px 12px;
border-radius:9px;background:var(--surface-2)}.att .sev{align-self:stretch;border-radius:4px;background:var(--accent)}
.att .sev.warn{background:var(--warn)}.att .sev.bad{background:var(--bad)}.att b{font-weight:600}
.att small{display:block;color:var(--muted);font-size:12.5px}.att code{display:block;margin-top:4px;
font-family:var(--mono);font-size:12px;color:var(--accent)}
.charts{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;align-items:start}
.wrap{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:13.5px}
th{text-align:left;color:var(--muted);font-weight:500;font-size:11.5px;text-transform:uppercase;letter-spacing:.05em;
padding:6px 10px;border-bottom:1px solid var(--line);white-space:nowrap}
td{padding:8px 10px;border-bottom:1px solid var(--line);white-space:nowrap}tr:last-child td{border-bottom:0}
th.n,td.n{text-align:right}th a.sort{color:inherit;text-decoration:none}th a.sort:hover{color:var(--accent)}
code.id{font-family:var(--mono);font-size:12px;color:var(--muted)}
.empty{border:1px dashed var(--line);border-radius:10px;padding:12px 14px;color:var(--muted);font-size:13px}
.empty b{color:var(--text);font-weight:600}
.ind{border-left:3px solid var(--warn)}.tag{font-size:11px;padding:1px 8px;border-radius:99px;
background:var(--warn-soft);color:var(--warn);font-weight:600;margin-left:6px}
.kgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:10px}
.k{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:10px 12px;display:flex;
flex-direction:column;gap:2px}.k small{color:var(--muted);font-size:12px}.k div{font-family:var(--mono);
font-variant-numeric:tabular-nums;font-size:17px;font-weight:600}.k a{color:inherit;text-decoration:none}
.k a:hover{color:var(--accent)}.k.empty-k div{color:var(--faint);font-family:var(--sans);font-size:13px;font-weight:500}
details.all>summary{cursor:pointer;color:var(--muted);font-weight:500;padding:4px 0}
details.all[open]>summary{margin-bottom:10px}
.note{color:var(--muted);font-size:12.5px;margin:0}
svg.chart{width:100%;height:auto;display:block}svg.chart text{fill:var(--muted);font:11px var(--mono)}
svg.chart text.v{fill:var(--text)}svg.chart line{stroke:var(--line)}svg.chart rect{fill:var(--accent)}
svg.chart rect.past{fill:var(--accent);opacity:.45}svg.chart rect.zero{fill:var(--faint);opacity:.6}
.alta{color:var(--bad)}.media{color:var(--warn)}.baixa{color:var(--muted)}
.center{min-height:100%;display:grid;place-items:center;padding:24px 16px}
.login{background:var(--surface);border:1px solid var(--line);border-radius:16px;padding:28px;width:min(420px,100%);
display:flex;flex-direction:column;gap:14px}.login form{display:flex;flex-direction:column;gap:10px}
.login label{display:flex;flex-direction:column;gap:4px;font-size:13px;color:var(--muted)}
.err{color:var(--bad);margin:0}
form.inline{display:flex;flex-wrap:wrap;gap:8px;align-items:flex-end}form.inline label{display:flex;
flex-direction:column;gap:3px;font-size:12px;color:var(--muted)}
@media (max-width:860px){.charts{grid-template-columns:1fr}
.app{grid-template-columns:1fr}.side{position:static;height:auto;border-right:0;
border-bottom:1px solid var(--line);flex-direction:row;flex-wrap:wrap;gap:12px 20px}.me{margin-top:0;border-top:0;
padding-top:0}main{padding:18px 16px 40px}.cols{grid-template-columns:1fr}}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
"""
