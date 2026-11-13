const {readFileSync} = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
let source = readFileSync('frontend/swipe-navigation.edge.js','utf8');
source = source.slice(0,source.lastIndexOf('console.info(')-1);
const handlers = {};
class Element {}
class TouchEvent {constructor(x,y,count=1){this.touches=Array.from({length:count},()=>({clientX:x,clientY:y}));} composedPath(){return [];} preventDefault(){this.prevented=true;}}
const context = vm.createContext({document:{getElementsByTagName:()=>[]},URLSearchParams,window:{location:{search:'?kiosk=true'},innerWidth:720,TouchEvent},screen:{width:720,height:1280},Element,TouchEvent,MouseEvent:class{},AbortController,console});
vm.runInContext(source+';globalThis.test={To,to,eo};',context);
const {To,to,eo}=context.test;
eo.haAppLayout.getDomNode=()=>({addEventListener:(name,fn)=>handlers[name]=fn});
eo.ha.getDomNode=()=>({style:{}});
to.getCurrentConfig=()=>({getEnable:()=>true,getEnableOnSubviews:()=>true,getEnableMouseSwipe:()=>false,getPreventDefault:()=>true,getSwipeAmount:()=>0.15,getWrap:()=>true,getSkipTabs:()=>[],getSkipHidden:()=>false,getSkipSubviews:()=>false});
to.getViews=()=>[{path:'one'},{path:'two'}];to.getCurrentViewIndex=()=>0;
let navigations=0;To.animatedNavigateTo=()=>navigations++;
To.init();
function swipe(x,y,endX,endY,event='touchend') {const before=navigations;handlers.touchstart(new TouchEvent(x,y));const move=new TouchEvent(endX,endY);handlers.touchmove(move);handlers[event]();return {count:navigations-before,prevented:!!move.prevented};}
assert.deepEqual(swipe(350,300,20,300),{count:0,prevented:false});
assert.equal(swipe(0,300,300,300).count,1);
assert.equal(swipe(719,300,400,300).count,1);
assert.equal(swipe(10,300,200,300).count,0);
assert.equal(swipe(21,300,400,300).count,0);
assert.equal(swipe(10,300,300,600).count,0);
assert.equal(swipe(10,300,300,300,'touchcancel').count,0);
handlers.touchend();assert.equal(navigations,2);
handlers.touchstart(new TouchEvent(10,300));handlers.touchmove(new TouchEvent(300,300));handlers.touchstart(new TouchEvent(300,300,2));handlers.touchend();assert.equal(navigations,2);
context.window.location.search='';assert.equal(swipe(350,300,20,300).count,1);
console.log('Edge swipe integration checks passed');
// Refresh returns to the kiosk home, retains kiosk options, and clears anchors.
context.URL=URL;
let redirected;
context.window.location={origin:'https://home.thesolarium.io',href:'https://home.thesolarium.io/lovelace/weather?kiosk=true&browser_id=panel#card',search:'?kiosk=true&browser_id=panel',replace:url=>redirected=url};
context.window.performance={getEntriesByType:()=>[{type:'reload'}]};
vm.runInContext('kioskReloadHome()',context);
assert.equal(redirected,'https://home.thesolarium.io/lovelace/kiosk?kiosk=true&browser_id=panel');
redirected=undefined;
context.window.location.href='https://home.thesolarium.io/lovelace/kiosk?kiosk=true&browser_id=panel';
vm.runInContext('kioskReloadHome()',context);assert.equal(redirected,undefined);
context.window.location.href='https://home.thesolarium.io/lovelace/weather?kiosk=true&browser_id=panel';
context.window.performance={getEntriesByType:()=>[{type:'navigate'}]};
vm.runInContext('kioskReloadHome()',context);assert.equal(redirected,undefined);
context.window.location.search='';context.window.performance={getEntriesByType:()=>[{type:'reload'}]};
vm.runInContext('kioskReloadHome()',context);assert.equal(redirected,undefined);
console.log('Kiosk refresh routing checks passed');
