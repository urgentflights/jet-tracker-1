"""Server-rendered pages for sign-in, sign-up and the admin console (self-contained: no app assets needed)."""
import html

CSS = """*{box-sizing:border-box}html,body{margin:0;min-height:100%;font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:#0b1220;color:#e7ecf5}
a{color:#38bdf8}.wrap{min-height:100vh;display:flex;align-items:center;justify-content:center;padding:24px 16px}
.card{width:100%;max-width:400px;background:#0f172a;border:1px solid #1e293b;border-radius:16px;padding:26px 24px;box-shadow:0 20px 60px rgba(0,0,0,.45)}
.brand{font-weight:800;font-size:20px;display:flex;align-items:center;gap:8px;margin-bottom:4px}.brand .logo{color:#38bdf8;font-size:22px}
.sub{color:#94a3b8;font-size:14px;margin:0 0 18px}h1{font-size:18px;margin:0 0 14px}
label{display:block;font-size:13px;font-weight:700;color:#cbd5e1;margin:12px 0 5px}
input[type=text],input[type=email],input[type=password]{width:100%;background:#111827;color:#e7ecf5;border:1px solid #334155;border-radius:10px;padding:11px 12px;font-size:16px}
input:focus{outline:2px solid #38bdf8;outline-offset:0;border-color:#38bdf8}
.chk{display:flex;align-items:center;gap:8px;font-size:14px;color:#cbd5e1;margin:14px 0 4px;font-weight:400}
.btn{display:block;width:100%;margin-top:18px;background:#38bdf8;color:#0b1220;border:0;border-radius:10px;padding:12px;font-size:16px;font-weight:800;cursor:pointer}
.btn:hover{background:#7dd3fc}.alt{text-align:center;font-size:14px;color:#94a3b8;margin-top:16px}
.err{background:#3b0a0a;border:1px solid #991b1b;color:#fca5a5;border-radius:10px;padding:10px 12px;font-size:14px;margin-bottom:6px}
.ok{background:#052e1f;border:1px solid #065f46;color:#6ee7b7;border-radius:10px;padding:10px 12px;font-size:14px;margin-bottom:6px}
.hint{font-size:12px;color:#94a3b8;margin-top:5px}.hide{display:none}"""

def esc(v): return html.escape(str(v if v is not None else ''), quote=True)

def shell(title, body, extra_head=''):
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><title>{esc(title)} · ParaFlight Jet Tracker 1</title>
<meta name="theme-color" content="#0b1220"><link rel="icon" href="/icon-192.png"><link rel="apple-touch-icon" href="/icon-192.png">
<meta name="robots" content="noindex"><style>{CSS}</style>{extra_head}</head><body>{body}</body></html>"""

def login_page(err='', msg='', email='', nxt='/'):
    return shell('Sign in', f"""<div class="wrap"><form class="card" method="post" action="/login" autocomplete="on">
<div class="brand"><span class="logo">✈</span> ParaFlight Jet Tracker 1</div><p class="sub">Live positions for the ParaFlight operator fleet.</p>
<h1>Sign in</h1>{f'<div class="err">{esc(err)}</div>' if err else ''}{f'<div class="ok">{esc(msg)}</div>' if msg else ''}
<input type="hidden" name="next" value="{esc(nxt)}">
<label for="email">Email</label><input id="email" type="email" name="email" value="{esc(email)}" required autocomplete="username" autofocus>
<label for="pw">Password</label><input id="pw" type="password" name="password" required autocomplete="current-password">
<label class="chk"><input type="checkbox" name="remember" value="1" checked> Keep me signed in for 30 days</label>
<button class="btn" type="submit">Sign in</button>
<div class="alt">New here? <a href="/signup">Create an account</a></div></form></div>""")

def signup_page(err='', name='', email='', show_code=False):
    return shell('Create account', f"""<div class="wrap"><form class="card" method="post" action="/signup" autocomplete="on">
<div class="brand"><span class="logo">✈</span> ParaFlight Jet Tracker 1</div><p class="sub">Create an account to see the live tracker.</p>
<h1>Create account</h1>{f'<div class="err">{esc(err)}</div>' if err else ''}
<label for="name">Name</label><input id="name" type="text" name="name" value="{esc(name)}" required autocomplete="name" maxlength="80" autofocus>
<label for="email">Email</label><input id="email" type="email" name="email" value="{esc(email)}" required autocomplete="email">
<label for="pw">Password</label><input id="pw" type="password" name="password" required minlength="8" autocomplete="new-password">
<div class="hint">At least 8 characters.</div>
<div class="hint" style="margin-top:12px"><a href="#" id="admt">Setting up the site admin account?</a></div>
<div id="adm" class="hide"><label for="code">Admin setup code</label><input id="code" type="password" name="code" autocomplete="off">
<div class="hint">Only the site admin needs this.</div></div>
<label class="chk"><input type="checkbox" name="remember" value="1" checked> Keep me signed in for 30 days</label>
<button class="btn" type="submit">Create account</button>
<div class="alt">Already have an account? <a href="/login">Sign in</a></div></form></div>
<script>document.getElementById('admt').addEventListener('click',e=>{{e.preventDefault();document.getElementById('adm').classList.remove('hide');e.target.parentNode.classList.add('hide');document.getElementById('code').focus();}});{'' if not show_code else "document.getElementById('admt').click();"}</script>""")

def message_page(title, text):
    return shell(title, f"""<div class="wrap"><div class="card"><div class="brand"><span class="logo">✈</span> ParaFlight Jet Tracker 1</div>
<h1 style="margin-top:14px">{esc(title)}</h1><p class="sub">{esc(text)}</p><a class="btn" style="text-align:center;text-decoration:none" href="/login">Back to sign in</a></div></div>""")

ADMIN_CSS = """body{background:#0b1220}.top{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:12px 16px;border-bottom:1px solid #1e293b;flex-wrap:wrap}
.top .brand{margin:0;font-size:18px}.top a{font-weight:700;text-decoration:none}.main{max-width:1100px;margin:0 auto;padding:16px}
.bar{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-bottom:12px}
.tog{display:flex;align-items:center;gap:10px;background:#0f172a;border:1px solid #1e293b;border-radius:12px;padding:10px 14px;font-weight:700}
.sw{position:relative;width:44px;height:24px;flex:0 0 auto}.sw input{opacity:0;width:0;height:0}.sl{position:absolute;inset:0;background:#334155;border-radius:999px;cursor:pointer;transition:.2s}
.sl:before{content:'';position:absolute;width:18px;height:18px;left:3px;top:3px;background:#fff;border-radius:50%;transition:.2s}.sw input:checked+.sl{background:#38bdf8}.sw input:checked+.sl:before{transform:translateX(20px)}
table{width:100%;border-collapse:collapse;background:#0f172a;border:1px solid #1e293b;border-radius:12px;overflow:hidden;font-size:14px}
th,td{text-align:left;padding:10px;border-bottom:1px solid #1e293b;vertical-align:middle}th{color:#94a3b8;font-size:12px;text-transform:uppercase;letter-spacing:.4px}
td .em{color:#94a3b8;font-size:12px;display:block}.st{display:inline-block;border-radius:999px;padding:2px 9px;font-size:12px;font-weight:800}
.st.active{background:#052e1f;color:#6ee7b7}.st.pending{background:#3b2a05;color:#fbbf24}.st.restricted{background:#3b0a0a;color:#fca5a5}.st.admin{background:#0c2a44;color:#7dd3fc;margin-left:4px}
.acts{display:flex;gap:6px;flex-wrap:wrap}.b{background:#111827;color:#e7ecf5;border:1px solid #334155;border-radius:8px;padding:6px 10px;font-weight:700;font-size:13px;cursor:pointer}
.b.ok{border-color:#065f46;color:#6ee7b7}.b.warn{border-color:#92400e;color:#fbbf24}.b.del{border-color:#991b1b;color:#fca5a5}.muted{color:#94a3b8;font-size:13px}
#msg{min-height:20px;font-size:14px;color:#6ee7b7}
@media (max-width:720px){thead{display:none}table,tbody,tr,td{display:block;width:100%}tr{border-bottom:1px solid #1e293b;padding:8px 0}td{border:0;padding:4px 12px}
td[data-l]:before{content:attr(data-l);color:#94a3b8;font-size:11px;text-transform:uppercase;display:block}}"""

def admin_page(me):
    return shell('Admin', f"""<div class="top"><div class="brand"><span class="logo">✈</span> Jet Tracker · Admin</div>
<div style="display:flex;gap:14px;align-items:center"><a href="/">← Back to tracker</a><form method="post" action="/logout" style="margin:0"><button class="b" type="submit">Sign out</button></form></div></div>
<div class="main"><div class="bar"><div><b style="font-size:18px">Users</b> <span class="muted" id="cnt"></span><div class="muted">Signed in as {esc(me['name'])} ({esc(me['email'])})</div></div>
<label class="tog"><span class="sw"><input type="checkbox" id="appr"><span class="sl"></span></span> New signups need approval</label></div>
<div id="msg"></div>
<table><thead><tr><th>Name</th><th>Email</th><th>Signed up</th><th>Last sign-in</th><th>Status</th><th>Actions</th></tr></thead><tbody id="tb"><tr><td colspan="6" class="muted">Loading…</td></tr></tbody></table>
<p class="muted">Restrict blocks sign-in and signs the person out everywhere. Restore lets them back in. Delete removes the account (they could sign up again unless approval is required). Times in your local zone.</p></div>
<script>
(()=>{{
const $=s=>document.querySelector(s),esc=v=>String(v??'').replace(/[&<>"]/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}}[c]));
const fmt=t=>t?new Date(t*1000).toLocaleString([],{{month:'short',day:'numeric',year:'numeric',hour:'numeric',minute:'2-digit',timeZoneName:'short'}}):'—';
async function api(p,b){{const r=await fetch(p,b?{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(b),credentials:'same-origin'}}:{{credentials:'same-origin',cache:'no-store'}});
  if(r.status===401){{location.href='/login?next=/admin';throw 0}}const j=await r.json();if(!r.ok)throw new Error(j.error||r.status);return j;}}
async function load(){{const d=await api('/api/admin/users');$('#appr').checked=!!d.requireApproval;
  const order={{pending:0,active:1,restricted:2}};d.users.sort((a,b)=>(order[a.status]-order[b.status])||(b.created-a.created));
  $('#cnt').textContent=`· ${{d.users.length}} total · ${{d.users.filter(u=>u.status==='pending').length}} pending`;
  $('#tb').innerHTML=d.users.map(u=>{{const me=u.id===d.me,a=[];
    if(!me){{if(u.status==='pending')a.push(`<button class="b ok" data-a="approve" data-id="${{u.id}}">Approve</button>`);
      if(u.status==='active')a.push(`<button class="b warn" data-a="restrict" data-id="${{u.id}}">Restrict</button>`);
      if(u.status==='restricted')a.push(`<button class="b ok" data-a="restore" data-id="${{u.id}}">Restore</button>`);
      a.push(`<button class="b del" data-a="delete" data-id="${{u.id}}" data-n="${{esc(u.name)}}">Delete</button>`);}}
    return `<tr><td data-l="Name"><b>${{esc(u.name)}}</b>${{u.role==='admin'?'<span class="st admin">admin</span>':''}}${{me?' <span class="muted">(you)</span>':''}}</td><td data-l="Email">${{esc(u.email)}}</td>
      <td data-l="Signed up">${{fmt(u.created)}}</td><td data-l="Last sign-in">${{fmt(u.lastLogin)}}</td><td data-l="Status"><span class="st ${{u.status}}">${{u.status}}</span></td>
      <td data-l="Actions"><div class="acts">${{a.join('')||'<span class="muted">—</span>'}}</div></td></tr>`;}}).join('')||'<tr><td colspan="6" class="muted">No users yet.</td></tr>';}}
$('#tb').addEventListener('click',async e=>{{const b=e.target.closest('button[data-a]');if(!b)return;const act=b.dataset.a;
  if(act==='delete'&&!confirm(`Delete ${{b.dataset.n}}'s account?`))return;
  try{{await api('/api/admin/user',{{id:b.dataset.id,action:act}});$('#msg').textContent='Done.';load();}}catch(err){{if(err)$('#msg').textContent='Error: '+err.message;}}}});
$('#appr').addEventListener('change',async e=>{{try{{await api('/api/admin/settings',{{requireApproval:e.target.checked}});$('#msg').textContent=e.target.checked?'New signups now wait for your approval.':'New signups get in right away.';}}catch(err){{if(err)$('#msg').textContent='Error: '+err.message;load();}}}});
load().catch(e=>{{if(e)$('#msg').textContent='Error: '+e.message;}});setInterval(()=>load().catch(()=>{{}}),30000);
}})();
</script>""", f'<style>{ADMIN_CSS}</style>')
