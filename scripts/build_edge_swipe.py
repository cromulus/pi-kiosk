"""Build the kiosk edge-only variant of hass-swipe-navigation v1.16.1.

The upstream snapshot is kept separately so HACS updates cannot erase this policy.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
source = (ROOT / 'frontend/swipe-navigation.upstream.js').read_text()
replacements = {
    'So=function(e){let i;': '''So=function(e){
// Reset every contact, including ignored or multi-touch starts.
n(this,po,null,"f",mo);n(this,po,null,"f",go);
n(this,po,null,"f",vo);n(this,po,null,"f",_o);
let i;''',
    'Eo=function(e){if(t(this,po,"f",mo)&&t(this,po,"f",go)){': '''Eo=function(e){
if(e.touches && e.touches.length!==1){
n(this,po,null,"f",mo);n(this,po,null,"f",go);
n(this,po,null,"f",vo);n(this,po,null,"f",_o);return;}
if(t(this,po,"f",mo)!=null&&t(this,po,"f",go)!=null){
if(!kioskEdgeStart(t(this,po,"f",mo)))return;''',
    'if(s)o.logd(r,"Swipe ignored, too short.");': '''if(!kioskEdgeTravel(t(this,po,"f",mo),t(this,po,"f",vo),t(this,po,"f",_o)))o.logd(r,"Swipe ignored: requires side-edge entry and continued inward travel.");else if(s)o.logd(r,"Swipe ignored, too short.");''',
    'i.addEventListener("touchend",': '''i.addEventListener("touchcancel",()=>{
n(this,po,null,"f",mo);n(this,po,null,"f",go);
n(this,po,null,"f",vo);n(this,po,null,"f",_o);
},{signal:t(this,po,"f",bo).signal,passive:!0}),i.addEventListener("touchend",''',
}
for old, new in replacements.items():
    assert source.count(old) == 1, f'Upstream changed: {old}'
    source = source.replace(old, new)
policy = '''// A reload in a kiosk session returns to its home view, including kiosk mode.
function kioskReloadHome(){
if(new URLSearchParams(window.location.search).get("kiosk")!=="true")return;
if(window.performance?.getEntriesByType("navigation")[0]?.type!=="reload")return;
const home=new URL("/lovelace/kiosk",window.location.origin);
home.search=window.location.search;
home.searchParams.set("kiosk","true");
if(window.location.href!==home.href)window.location.replace(home.href);
}
kioskReloadHome();
// Edge navigation policy for kiosk=true sessions. Coordinates are CSS pixels.
// Hardware cannot report offscreen contact; first contact must be at the edge.
function kioskEdgeEnabled(){return new URLSearchParams(window.location.search).get("kiosk")==="true";}
function kioskEdgeStart(x){return !kioskEdgeEnabled() || (x!=null && (x<=20 || x>=window.innerWidth-20));}
function kioskEdgeTravel(x,dx,dy){
if(!kioskEdgeEnabled())return true;
return kioskEdgeStart(x) && Math.abs(dx)>=260 && Math.abs(dx)>=2*Math.abs(dy)
 && ((x<=20 && dx<0) || (x>=window.innerWidth-20 && dx>0));
}
'''
(ROOT / 'frontend/swipe-navigation.edge.js').write_text(policy + source)
