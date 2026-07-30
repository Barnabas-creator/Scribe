const fs=require('fs'), vm=require('vm');
const dir=require('path').join(__dirname,'..','src/p2w_gui/frontend')+'/';
const babelSrc=fs.readFileSync(dir+'vendor/babel.min.js','utf8');
const sandbox={}; sandbox.self=sandbox; sandbox.window=sandbox; sandbox.globalThis=sandbox;
vm.createContext(sandbox); vm.runInContext(babelSrc, sandbox);
const Babel=sandbox.Babel||sandbox.window.Babel;
if(!Babel){console.log('Babel not loaded');process.exit(1);}
// Compile every .jsx with Babel to catch syntax errors before bundling.
const files=require('fs').readdirSync(dir).filter(f=>f.endsWith('.jsx')).sort();
let bad=0;
for(const f of files){
  const code=fs.readFileSync(dir+f,'utf8');
  try{ Babel.transform(code,{presets:['react']}); console.log('OK   '+f);}
  catch(e){ bad++; console.log('ERR  '+f+' :: '+e.message.split('\n')[0]);}
}
console.log(bad? '\nFAILED: '+bad+' file(s) have syntax errors' : '\nALL JSX COMPILE OK');
