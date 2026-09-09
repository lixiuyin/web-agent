"""Browser-side rendered projection and observation-bound node registry."""

RENDER_HELPERS = r"""
const slot = '__webagentRenderedObservation';
let state = window[slot];
if (!state || state.document !== document) {
    state = {document, documentId: crypto.randomUUID?.() || String(Date.now())+Math.random(), revision: 0, nodes: new Map()};
    state.observer = new MutationObserver(() => state.revision++);
    state.observer.observe(document, {subtree:true, childList:true, characterData:true, attributes:true});
    window.addEventListener('scroll', () => state.revision++, true);
    window.addEventListener('resize', () => state.revision++);
    window[slot] = state;
}
const box = r => ({x:r.left, y:r.top, width:r.width, height:r.height});
const intersect = (a,b) => {
    const x=Math.max(a.x,b.x), y=Math.max(a.y,b.y);
    return {x,y,width:Math.max(0,Math.min(a.x+a.width,b.x+b.width)-x),
        height:Math.max(0,Math.min(a.y+a.height,b.y+b.height)-y)};
};
const nonempty = r => r.width > 0 && r.height > 0;
const parent = el => el.parentElement || el.getRootNode().host;
function rendered(el) {
    for(let p=el;p;p=parent(p)) {
        const s=getComputedStyle(p);
        if(s.display==='none' || s.visibility==='hidden' || Number(s.opacity)===0 || s.contentVisibility==='hidden') return false;
    }
    return true;
}
function clipFor(el) {
    let clip = {x:0,y:0,width:innerWidth,height:innerHeight};
    for (let p=el; p; p=parent(p)) {
        const s=getComputedStyle(p);
        if (s.display==='none' || s.visibility==='hidden' || Number(s.opacity)===0)
            return {x:0,y:0,width:0,height:0};
        if (p!==el && /(auto|scroll|hidden|clip)/.test(s.overflowX+' '+s.overflowY)) {
            const r=p.getBoundingClientRect();
            const sx=p.offsetWidth ? r.width/p.offsetWidth : 1;
            const sy=p.offsetHeight ? r.height/p.offsetHeight : 1;
            const bounds={x:r.left+p.clientLeft*sx,y:r.top+p.clientTop*sy,
                width:p.clientWidth*sx,height:p.clientHeight*sy};
            if (/(auto|scroll|hidden|clip)/.test(s.overflowX)) {
                const c=intersect(clip,{...clip,x:bounds.x,width:bounds.width});
                clip={...clip,x:c.x,width:c.width};
            }
            if (/(auto|scroll|hidden|clip)/.test(s.overflowY)) {
                const c=intersect(clip,{...clip,y:bounds.y,height:bounds.height});
                clip={...clip,y:c.y,height:c.height};
            }
        }
    }
    return clip;
}
function hit(el, rect) {
    if (!nonempty(rect)) return false;
    const root=el.getRootNode();
    const target=root.elementFromPoint?.(rect.x+rect.width/2,rect.y+rect.height/2);
    return target ? target===el || el.contains(target) : null;
}
function cssPath(el) {
    if (!el || el.nodeType!==1) return '';
    if (el.id) return (el.getRootNode().host ? cssPath(el.getRootNode().host)+' ' : '')+'#'+CSS.escape(el.id);
    const parts=[];
    for (let p=el;p;p=p.parentElement) {
        let part=p.tagName.toLowerCase();
        if (p.id) {parts.unshift('#'+CSS.escape(p.id)); break;}
        const siblings=p.parentElement ? [...p.parentElement.children].filter(c=>c.tagName===p.tagName) : [];
        if (siblings.length>1) part+=':nth-of-type('+(siblings.indexOf(p)+1)+')';
        parts.unshift(part);
    }
    const host=el.getRootNode().host;
    return (host ? cssPath(host)+' ' : '')+parts.join(' > ');
}
function lineage(el) {
    const chain=[];
    for(let p=parent(el);p;p=parent(p)) chain.push(p);
    return chain;
}
function targetState(el) {
    // Effective clipping/visibility includes ancestors without hashing their text.
    const r=box(el.getBoundingClientRect());
    return JSON.stringify([location.href,scrollX,scrollY,innerWidth,innerHeight,
        r,clipFor(el),rendered(el),el.scrollLeft,el.scrollTop,
        el.textContent,el.value,el.checked,el.selectedIndex,el.getAttributeNames().sort().map(a=>[a,el.getAttribute(a)]),
        el.href,el.src,el.matches(':disabled'),el.form?.action,el.form?.method,el.form?.target,
        el.form?.getAttributeNames().sort().map(a=>[a,el.form.getAttribute(a)]),
        [...(el.labels || [])].map(label=>label.textContent),
        ['aria-labelledby','aria-describedby'].map(attr=>(el.getAttribute(attr)||'').split(/\s+/)
            .map(id=>el.getRootNode().getElementById?.(id)?.textContent))]);
}
function referenceCurrent(entry) {
    if(!entry || !entry.el.isConnected || entry.localToken!==targetState(entry.el)) return false;
    const chain=lineage(entry.el);
    return chain.length===entry.lineage.length && chain.every((p,i)=>p===entry.lineage[i]);
}
function signature() {
    // Screenshot preparation can temporarily insert styles or resize the viewport.
    // Compare the restored rendered state, not a raw mutation/event counter.
    const records=[...state.nodes.values()].map(v=> {
        const s=getComputedStyle(v.el), r=box(v.el.getBoundingClientRect());
        return [v.el.isConnected,v.source?.isConnected,r,v.el.scrollLeft,v.el.scrollTop,clipFor(v.el),
            v.source?.textContent || v.el.textContent?.slice(0,200),v.el.value,v.el.checked,v.el.selectedIndex,
            v.el.getAttribute('aria-label'),v.el.getAttribute('href'),v.el.matches(':disabled'),
            ['role','tabindex','aria-haspopup','aria-expanded','aria-checked','aria-selected','aria-pressed'].map(a=>v.el.getAttribute(a)),
            s.color,s.backgroundColor,s.fontSize,s.transform,s.cursor,rendered(v.el),hit(v.el,intersect(r,clipFor(v.el)))];
    });
    const value=JSON.stringify([location.href,scrollX,scrollY,innerWidth,innerHeight,records]);
    let h=2166136261;
    for(let i=0;i<value.length;i++) h=Math.imul(h^value.charCodeAt(i),16777619);
    return state.documentId+':'+(h>>>0).toString(16);
}
"""

RENDER_PROJECTION_JS = (
    r"""({observationId, frameIndex, maxNodes, maxChars, clip}) => {
"""
    + RENDER_HELPERS
    + r"""
state.nodes = new Map(); state.observationId=observationId; state.clip=clip;
const texts=[], controls=[], roots=[document];
let count=0, chars=0, omitted=false;
function viewportText(node, clipping) {
    const range=document.createRange(); let out='';
    const value=node.textContent;
    for (const match of value.matchAll(/\S+\s*/gu)) {
        range.setStart(node,match.index); range.setEnd(node,match.index+match[0].length);
        const rects=[...range.getClientRects()].map(box);
        if (!rects.some(r=>nonempty(intersect(r,clipping)))) continue;
        if (rects.every(r=>JSON.stringify(intersect(r,clipping))===JSON.stringify(r))) {
            if (hit(node.parentElement,rects[0])!==false) out+=match[0];
            continue;
        }
        let offset=match.index;
        for (const c of match[0]) {
            range.setStart(node,offset); offset+=c.length; range.setEnd(node,offset);
            const r=box(range.getBoundingClientRect()), visible=intersect(r,clipping);
            if (nonempty(visible) && visible.width>=r.width-0.5 && visible.height>=r.height-0.5 &&
                hit(node.parentElement,visible)!==false) out+=c;
        }
    }
    return out.trim();
}
const interactiveRoles = new Set(['button','link','textbox','combobox','checkbox','radio',
    'menuitem','menuitemcheckbox','menuitemradio','option','slider','spinbutton','switch','tab','treeitem','scrollbar']);
function interactionSource(el) {
    if (el.matches('a[href],button,input:not([type="hidden"]),textarea,select,summary,label[for],[contenteditable]:not([contenteditable="false"])')) return 'native';
    if (interactiveRoles.has(el.getAttribute('role'))) return 'aria_role';
    if (el.hasAttribute('aria-expanded') || (el.hasAttribute('aria-haspopup') && el.getAttribute('aria-haspopup')!=='false')) return 'aria_disclosure';
    if (el.hasAttribute('tabindex') && el.tabIndex>=0) return 'focusable';
    if (el.matches('html,body')) return null;
    if (['onclick','onmousedown','onpointerdown','onmouseenter','onmouseover','onkeydown'].some(name=>typeof el[name]==='function')) return 'event_handler';
    // Pointer cursors are an affordance hint, not proof of a click handler.
    // Keep only the boundary so inherited cursors do not duplicate every span/icon.
    const ancestor=parent(el);
    if (getComputedStyle(el).cursor==='pointer' && (!ancestor || getComputedStyle(ancestor).cursor!=='pointer')) return 'pointer_hint';
    return null;
}
function addControl(el, interactionSource) {
    const r=box(el.getBoundingClientRect());
    if (!nonempty(r) || !rendered(el)) return;
    const attrs={};
    for (const a of ['id','name','type','role','href','title','aria-label','placeholder',
        'tabindex','aria-haspopup','aria-expanded','aria-checked','aria-selected','aria-pressed']) {
        const v=el.getAttribute(a); if(v) attrs[a]=v;
    }
    if (el.matches('input:not([type="password"]):not([type="file"]),textarea,select')) {
        const value=String(el.value || '');
        attrs.value=value.slice(0,200)+(value.length>200 ? '…' : '');
    }
    const checked=el.matches('input[type="checkbox"],input[type="radio"]') ? {checked:el.checked} : {};
    const visible=intersect(intersect(r,clipFor(el)),clip);
    const ref='f'+frameIndex+':e'+controls.length;
    const selector=cssPath(el);
    const text=(el.innerText || el.textContent || '').trim().slice(0,200);
    state.nodes.set(ref,{el,selector});
    controls.push({tag:el.tagName.toLowerCase(),text,attrs,css_path:selector,ref,interaction_source:interactionSource,...checked,
        observation_id:observationId,frame_index:frameIndex,
        bbox:{...r,x:r.x+scrollX,y:r.y+scrollY},viewport_bbox:r,visible_bbox:visible,
        in_viewport:nonempty(visible),receives_events:hit(el,visible),
        enabled:!el.matches(':disabled,[aria-disabled="true"]'),is_visible:true});
}
for (let rootIndex=0;rootIndex<roots.length;rootIndex++) {
    const root=roots[rootIndex];
    if (root!==document) state.observer.observe(root,{subtree:true,childList:true,characterData:true,attributes:true});
    const walker=document.createTreeWalker(root,NodeFilter.SHOW_ELEMENT|NodeFilter.SHOW_TEXT);
    for (let node=walker.nextNode();node;node=walker.nextNode()) {
        if (++count>maxNodes || chars>=maxChars) {omitted=true; break;}
        if(node.nodeType===1) {
            if(node.shadowRoot) roots.push(node.shadowRoot);
            if(node.matches('iframe,frame')) state.nodes.set('frame'+count,{el:node,selector:null});
            const source=interactionSource(node);
            if(source) addControl(node,source);
            continue;
        }
        const el=node.parentElement;
        if(!el || el.closest('script,style,noscript,template') || !node.textContent.trim() || !rendered(el)) continue;
        const raw=node.textContent;
        if(chars+raw.length>maxChars) {omitted=true; continue;}
        chars+=raw.length;
        const range=document.createRange(); range.selectNodeContents(node);
        if(![...range.getClientRects()].some(r=>r.width && r.height)) continue;
        const clipping=intersect(clipFor(el),clip);
        const text=raw.trim(), viewport=nonempty(clipping) ? viewportText(node,clipping) : '';
        texts.push({text,viewport_text:viewport,frame_index:frameIndex,
            editable:el.isContentEditable || !!el.closest('input,textarea,select')});
        // Track text layout, including noninteractive blocks and scroll containers.
        state.nodes.set('t'+texts.length,{el,source:node,selector:null});
    }
    if(omitted && count>maxNodes) break;
}
for(const entry of state.nodes.values()) {
    if(entry.source) continue;
    entry.localToken=targetState(entry.el); entry.lineage=lineage(entry.el);
}
state.token=signature();
return {controls,texts,token:state.token,document_id:state.documentId,
    observation_id:observationId,omitted,frame_index:frameIndex};
} """
)

CHECK_PROJECTION_JS = (
    r"""(observationId) => {
"""
    + RENDER_HELPERS
    + r"""
return state.observationId===observationId && state.token===signature();
}"""
)

RESOLVE_REFERENCE_JS = (
    r"""({observationId, ref, selector, requireViewport}) => {
"""
    + RENDER_HELPERS
    + r"""
if(state.observationId!==observationId)
    throw new Error('Stale observation; re-observe before acting');
const entry=state.nodes.get(ref);
if(!entry || (selector!==null && entry.selector!==selector))
    throw new Error('Unknown or mismatched observation reference');
if(!referenceCurrent(entry)) throw new Error('Stale observation target; re-observe before acting');
const el=entry.el;
if(requireViewport) {
    const visible=intersect(intersect(box(el.getBoundingClientRect()),clipFor(el)),state.clip);
    if(!nonempty(visible)) throw new Error('Target outside viewport; scroll_to_element then re-observe');
    if(hit(el,visible)!==true) throw new Error('Target obscured or hit test unknown; re-observe');
}
return el;
}"""
)

CHECK_FRAME_REFERENCE_JS = (
    r"""(el, {observationId, requireViewport}) => {
"""
    + RENDER_HELPERS
    + r"""
if(state.observationId!==observationId) return false;
const entry=[...state.nodes.values()].find(v=>v.el===el && !v.source);
if(!referenceCurrent(entry)) return false;
if(!requireViewport) return true;
const visible=intersect(intersect(box(el.getBoundingClientRect()),clipFor(el)),state.clip);
return nonempty(visible) && hit(el,visible)===true;
}"""
)

FRAME_GEOMETRY_JS = (
    r"""el => {
"""
    + RENDER_HELPERS
    + r"""
const r=el.getBoundingClientRect(), style=getComputedStyle(el);
const matrix=style.transform==='none' ? null : new DOMMatrix(style.transform);
const supported=!matrix || matrix.is2D && matrix.b===0 && matrix.c===0 && matrix.a>0 && matrix.d>0;
const sx=el.offsetWidth ? r.width/el.offsetWidth : 1, sy=el.offsetHeight ? r.height/el.offsetHeight : 1;
const content={x:r.left+el.clientLeft*sx,y:r.top+el.clientTop*sy,width:el.clientWidth*sx,height:el.clientHeight*sy};
return {content,clip:intersect(content,clipFor(el)),sx,sy,supported,
    receives_events:hit(el,intersect(content,clipFor(el)))};
}"""
)
