// Public Site Field v0 demonstration — no build step, no engine dependency.
// Trust anchors MUST be independently pinned; host-supplied field.json is untrusted.
const $=id=>document.getElementById(id);
const query=new URLSearchParams(location.search);
const root=(query.get('root')||'').toLowerCase();
const indexHash=(query.get('index')||'').toLowerCase();
const base=(query.get('base')||'./field').replace(/\/$/,'');
const version=query.get('version')||'externally-pinned';
const decoder=new TextDecoder();
const zoneName=z=>z<90?'ltm-'+(1+z%45)+'-'+(z<45?'n':'s'):(z===90?'lps-n':'lps-s');
const layers=['Elevation','Slope','Cold-trap fraction','Best-spot sunlight','TiO₂','TiO₂ uncertainty','Usable ground'];
const units=['m','degrees','fraction','fraction','wt%','wt%','fraction'];
const scales=[1,.01,.0001,.0001,.01,.01,.0001];
let keys=[],loaded=null,field={},treeTotal=0;
const hex=bytes=>Array.from(bytes,b=>b.toString(16).padStart(2,'0')).join('');
const unhex=str=>Uint8Array.from(str.match(/.{2}/g)||[],x=>parseInt(x,16));
const exactHex=x=>/^[0-9a-f]{64}$/.test(x);
const utf8=new TextEncoder();
function concat(...arrays){const x=new Uint8Array(arrays.reduce((a,b)=>a+b.length,0));let j=0;for(const a of arrays){x.set(a,j);j+=a.length;}return x;}
async function H(domain,payload){
  const length=new Uint8Array(8),view=new DataView(length.buffer);
  view.setBigUint64(0,BigInt(payload.length),false);
  const bytes=concat(utf8.encode(domain),Uint8Array.of(0),length,payload);
  return new Uint8Array(await crypto.subtle.digest('SHA-256',bytes));
}
async function node(left,right){return H('ld.field.node.v0',utf8.encode(JSON.stringify({left:hex(left),right:hex(right)})));}
async function fetchBytes(path){const resp=await fetch(base+'/'+path,{cache:'no-store'});if(!resp.ok)throw Error('HTTP '+resp.status+' '+path);return new Uint8Array(await resp.arrayBuffer());}
function fail(message){$('error').textContent=message;$('status').textContent='Verification failed';loaded=null;draw();throw Error(message);}
function parseKeys(bytes){
 if(bytes.length%6)fail('index.bin has invalid length');
 const view=new DataView(bytes.buffer,bytes.byteOffset,bytes.byteLength);
 const result=[];let previous=null;
 for(let i=0;i<bytes.length;i+=6){
  const key=[view.getUint16(i,true),view.getUint16(i+2,true),view.getUint16(i+4,true)];
  if(key[0]>=92)fail('index zone outside 0–91');
  if(previous&&compare(previous,key)>=0)fail('index keys not strictly ordered');
  result.push(key);previous=key;
 }
 return result;
}
function compare(a,b){for(let i=0;i<3;i++)if(a[i]!==b[i])return a[i]-b[i];return 0;}
function dataValue(tile,layer,i){
 const offset=196+(layer*625+i)*2,raw=layer===0?tile.view.getInt16(offset,true):tile.view.getUint16(offset,true);
 if(raw===(layer===0?-32768:65535))return null;
 return raw*scales[layer];
}
function bit(bytes,start,i){return Boolean(bytes[start+(i>>3)]&(1<<(i&7)));}
function decodeTile(bytes,expected){
 if(bytes.length!==8946)fail('tile incorrect length, expected 8946');
 const v=new DataView(bytes.buffer,bytes.byteOffset,bytes.byteLength);
 if(decoder.decode(bytes.slice(0,4))!=='LDFT'||v.getUint16(4,true)!==1)fail('tile magic/format mismatch');
 const key=[v.getUint16(6,true),v.getUint16(8,true),v.getUint16(10,true)];
 if(compare(key,expected)!==0||key[0]>=92)fail('tile header/index key mismatch');
 for(let i=14;i<24;i++)if(bytes[i])fail('nonzero reserved tile header byte');
 if(bytes[115]||bytes[195]||(bytes[114]&254)||(bytes[194]&254))fail('invalid bitmap padding');
 let whole=0,partial=0;
 for(let i=0;i<625;i++){
  const w=bit(bytes,36,i),s=bit(bytes,116,i);
  if(w&&!s)fail('whole square outside in-zone bitmap');
  if(w)whole++;
  if(s&&!w)partial++;
  if(!s)for(let k=0;k<7;k++)if(dataValue({view:v},k,i)!==null)fail('out-of-zone cell carries values');
 }
 if(whole!==v.getUint16(12,true))fail('whole-square population mismatch');
 return {key,bytes,view:v,whole,partial};
}
// tree.bin stores raw 32-byte hashes by level, leaves first.
// Read 32 bytes at a time by HTTP Range, without transferring the complete tree.
function levelSizes(n){const lengths=[];while(n){lengths.push(n);if(n===1)break;n=Math.ceil(n/2);}return lengths;}
async function fetchTreeNode(absoluteIndex){
 const start=absoluteIndex*32,end=start+31;
 const response=await fetch(base+'/tree.bin',{headers:{Range:'bytes='+start+'-'+end},cache:'no-store'});
 if(response.status!==206)throw Error('tree.bin server must support byte-range requests (206)');
 const bytes=new Uint8Array(await response.arrayBuffer());
 if(bytes.length!==32)throw Error('tree.bin range length mismatch');
 return bytes;
}
async function verifyProof(tileBytes,index){
 const sizes=levelSizes(keys.length);
 let h=await H('ld.field.tile.v0',tileBytes),i=index,offset=0;
 for(let level=0;level<sizes.length-1;level++){
  const other=i^1;
  if(other<sizes[level]){
   const sibling=await fetchTreeNode(offset+other);
   h=i%2===0?await node(h,sibling):await node(sibling,h);
  }
  offset+=sizes[level];i=Math.floor(i/2);
 }
 if(hex(h)!==root)fail('tile Merkle proof does not match pinned field_root');
 return true;
}
function reportError(err){$('status').textContent='Load failed';$('error').textContent=String(err?.message||err);console.error(err);}
async function init(){
 $('root').textContent=root||'MISSING';$('index').textContent=indexHash||'MISSING';$('base').textContent=base;$('version').textContent=version;
 if(!exactHex(root)||!exactHex(indexHash)){reportError(Error('Supply independently pinned ?root=<64hex>&index=<64hex>'));return;}
 if(!crypto.subtle){reportError(Error('WebCrypto requires a secure context or localhost'));return;}
 try{
  $('status').textContent='Verifying index against pinned index hash…';
  const bytes=await fetchBytes('index.bin');
  if(hex(await H('ld.field.index.v0',bytes))!==indexHash)fail('index.bin does not match pinned index_hash');
  keys=parseKeys(bytes);
  if(!keys.length)fail('empty field');
  const response=await fetch(base+'/field.json');if(!response.ok)throw Error('field.json HTTP '+response.status);
  field=await response.json();
  if(field.field_root&&field.field_root!==root)fail('field.json names a different field_root');
  if(field.index_hash&&field.index_hash!==indexHash)fail('field.json names a different index_hash');
  if(field.tile_count!==undefined&&field.tile_count!==keys.length)fail('field.json tile_count mismatch');
  treeTotal=levelSizes(keys.length).reduce((a,b)=>a+b,0);
  if(field.synthetic){$('status').textContent='SYNTHETIC FIELD — verified index; no actual lunar measurements';}
  else {$('status').textContent='Index verified; tile measurements verify on selection';}
  $('tile').replaceChildren(...keys.map((key,i)=>{const el=document.createElement('option');el.value=i;el.textContent=zoneName(key[0])+' / '+key[1]+','+key[2];return el;}));
  await loadTile(0);
 }catch(err){reportError(err);}
}
async function loadTile(i){
 if(!keys[i])return;
 $('error').textContent='';$('status').textContent='Verifying selected tile…';
 try{
  const key=keys[i],path='tiles/'+zoneName(key[0])+'/'+key[1]+'-'+key[2]+'.ldft';
  const bytes=await fetchBytes(path),tile=decodeTile(bytes,key);
  await verifyProof(bytes,i);
  loaded=tile;
  $('status').textContent=(field.synthetic?'SYNTHETIC — ':'')+'Verified tile '+(i+1)+' / '+keys.length+' ('+tile.whole+' whole, '+tile.partial+' partial)';
  showDetail(0);draw();
 }catch(err){reportError(err);}
}
function draw(){
 const c=$('grid'),ctx=c.getContext('2d');ctx.clearRect(0,0,c.width,c.height);
 if(!loaded)return;
 const layer=Number($('layer').value);let found=[];
 for(let i=0;i<625;i++){const v=dataValue(loaded,layer,i);if(v!==null)found.push(v);}
 const min=Math.min(...found),max=Math.max(...found),range=max-min||1;
 for(let i=0;i<625;i++){
  const x=i%25,y=Math.floor(i/25);
  const outside=!bit(loaded.bytes,116,i),whole=bit(loaded.bytes,36,i),v=dataValue(loaded,layer,i);
  if(outside||v===null)ctx.fillStyle=outside?'#353535':'#aaa';
  else {const ratio=(v-min)/range;ctx.fillStyle='hsl('+Math.round(225-190*ratio)+' 55% '+Math.round(26+46*ratio)+'%)';}
  ctx.fillRect(x*25,24*25-y*25,25,25);
  ctx.strokeStyle=whole?'#ffffff30':'#fbbf24';ctx.lineWidth=whole?.5:2;
  ctx.strokeRect(x*25+.5,24*25-y*25+.5,24,24);
 }
}
function showDetail(i){
 if(!loaded)return;
 const w=bit(loaded.bytes,36,i),inside=bit(loaded.bytes,116,i);
 const [z,e,n]=loaded.key,x=i%25,y=Math.floor(i/25);
 const parcel=zoneName(z)+' / '+(e*25+x)+' km E / '+(n*25+y)+' km N';
 const details=$('details');details.replaceChildren();
 const h=document.createElement('h2');h.textContent=parcel;details.append(h);
 const p=document.createElement('p');
 p.textContent=inside?(w?'Whole · claimable grid geometry':'Partial · unclaimable'):'Outside selected zone';
 details.append(p);
 const dl=document.createElement('dl');details.append(dl);
 for(let k=0;k<7;k++){
  const value=dataValue(loaded,k,i);
  const dt=document.createElement('dt'),dd=document.createElement('dd');
  dt.textContent=layers[k];dd.textContent=value===null?'No data':value+' '+units[k];
  dl.append(dt,dd);
 }
 const label=document.createElement('p');
 label.textContent='Values are expected survey estimates, not actual parcel endowment. Best sunlight is the best spot, not an illumination schedule. The coarse GRS fallback and uncertainty ranges require source-specific interpretation.';
 details.append(label);
}
$('tile').addEventListener('change',()=>loadTile(Number($('tile').value)));
$('layer').addEventListener('change',draw);
$('view').addEventListener('change',()=>{const desired=$('view').value;
  const i=keys.findIndex(k=>desired==='north'?k[0]===90:desired==='south'?k[0]===91:k[0]<90);
  if(i>=0){$('tile').value=i;loadTile(i);}
});
$('grid').addEventListener('click',ev=>{
 if(!loaded)return;
 const rect=ev.currentTarget.getBoundingClientRect(), x=Math.min(24,Math.max(0,Math.floor((ev.clientX-rect.left)/rect.width*25)));
 const y=Math.min(24,Math.max(0,24-Math.floor((ev.clientY-rect.top)/rect.height*25)));
 showDetail(x+y*25);
});
init();
