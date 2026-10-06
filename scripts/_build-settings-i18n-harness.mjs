#!/usr/bin/env node
// 设置面板汉化自检：在真浏览器里打开设置面板，扫描**用户可见文本**里的裸英文。
//
// 只扫可见文本，不扫代码：select 的 value、对象 key、className 里的英文
// 是代码标识符，不是给人看的，扫它们只会制造假阳性。
import { pathToFileURL } from 'node:url'
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = dirname(dirname(fileURLToPath(import.meta.url)))
const CLIENT = join(ROOT, 'home-plugin/dsh-kexi-crypto/lib/client.js')
const OUT_DIR = join(ROOT, '.kexi-test-tmp')
const REACT = join(process.env.TEMP || process.env.TMPDIR || '.', 'kexi-graph-harness/node_modules')

mkdirSync(OUT_DIR, { recursive: true })
const safe = readFileSync(CLIENT, 'utf8').replace(/<\/script/gi, '<\\/script')

const html = `<!doctype html><html lang="zh"><head><meta charset="utf-8"><title>设置面板汉化自检</title>
<style>:root{--dsw-alias-border-l1:#2a2f3a;--dsw-alias-border-l2:#3a4150;--dsw-alias-bg-layer-1:#1a1e26;
--dsw-alias-label-primary:#e6e9ef;--dsw-alias-label-secondary:#9aa3b2;--dsw-static-blue-500:#3b82f6;
--dsw-static-green-500:#22c55e;--dsw-alias-state-error-primary:#ef4444}
html,body{margin:0;background:#0f1218;color:var(--dsw-alias-label-primary);font-family:system-ui,"Microsoft YaHei",sans-serif}
#root{padding:12px}#hud{font:11px Consolas,monospace;color:#9aa3b2;margin-bottom:8px}</style></head>
<body><div id="hud">…</div><div id="root"></div>
<script>${readFileSync(join(REACT, 'react/umd/react.development.js'), 'utf8')}</script>
<script>${readFileSync(join(REACT, 'react-dom/umd/react-dom.development.js'), 'utf8')}</script>
<script>window.__ModuleLoader__={load:function(m){window.__m=m}};</script>
<script>${safe}</script>
<script>
(function(){
  var hud=document.getElementById('hud');
  // 设置面板要读 /settings 与 /cex 两个端点，这里给足形状，否则组件 return null
  window.fetch=function(u){
    var s=String(u);
    if(s.indexOf('/settings')>=0) return Promise.resolve({ok:true,json:function(){return Promise.resolve({
      settings:{showProgressPopup:true,showCliNodes:true,cliAsMembers:true,cliEnabled:{agy:true,codebuddy:true,mimo:true},
        cliPriority:'agy',autoCliCrosscheck:true,cliCrosscheckTimeoutSec:120,maxEventNodes:60,
        persistentTeam:true,screenerTop:100,screenerWorkers:6,minAdvUsd:500000,riskPerTrade:0.005,
        maxWeight:0.25,defaultLimit:120,cexAutonomy:'limited',cexProfile:'balanced',
        cexEnabled:{binance:true,okx:true,gate:true,mexc:true},cexDefaultLabel:'main',cexAutoNotionalCapUsd:200},
      defaults:{}})}});
    if(s.indexOf('/cex')>=0) return Promise.resolve({ok:true,json:function(){return Promise.resolve({
      storeDir:'kexi-cex',autonomy:'limited',profile:'balanced',registry:['binance','okx','gate','mexc'],
      credential_schema:{binance:{fields:['api_key','api_secret'],passphrase:false,key_name:'API Key',secret_name:'Secret Key'},
                         okx:{fields:['api_key','api_secret','passphrase'],passphrase:true,key_name:'API Key',secret_name:'Secret Key'},
                         gate:{fields:['api_key','api_secret'],passphrase:false,key_name:'API Key',secret_name:'Secret Key'},
                         mexc:{fields:['api_key','api_secret'],passphrase:false,key_name:'API Key',secret_name:'Secret Key'}},
      exchanges:{binance:{configured:true,labels:[{label:'main',api_key_masked:'****1234',has_passphrase:false}]}},
      enabled:['binance','okx','gate','mexc'],default_label:'main',auto_notional_cap_usd:200,
      autonomy_levels:{readonly:{label:'只读'},confirm:{label:'提议+确认'},
                        limited:{label:'受限自动'},full:{label:'全自动'}},
      accounts:[{exchange:'binance',label:'main',api_key_masked:'****1234',has_passphrase:false}],
      note:'demo'})}});
    return Promise.resolve({ok:false,status:404,json:function(){return Promise.resolve({})}});
  };
  try{
    var mod=window.__m.factory(function(n){if(n==='react')return window.React;throw new Error(n)});
    var cap={};
    mod.apply({inject:function(d,cb){cb({get:function(n){return n==='slots'?{register:function(e,c){cap[e.name]=c;return function(){}},inject:function(){}}:undefined},
      slots:{register:function(e,c){cap[e.name]=c;return function(){}},inject:function(){}}})}});
    // SettingsCard 就是挂在 settings.plugin.item 上的那个
    var C=cap['settings.plugin.item'];
    if(!C) throw new Error('未抓到 SettingsCard，实际：'+Object.keys(cap).join(','));
    window.ReactDOM.createRoot(document.getElementById('root')).render(window.React.createElement(C,{}));
    setTimeout(function(){
      var t=document.getElementById('root').innerText||'';
      hud.textContent='ok  可见文本 '+t.length+' 字';
      window.__READY=true; window.__TEXT=t;
    },600);
  }catch(e){hud.textContent='ERR: '+e.message; window.__READY=true;}
})();
</script></body></html>`

const out = join(OUT_DIR, 'settings-i18n-harness.html')
writeFileSync(out, html, 'utf8')
console.log('夹具已生成：' + out)
